"""Accuracy of the data: run the real extraction chain + decisions on the hand-labelled gold set and score it.

    python -m etl.run eval

The gold set (`etl/fixtures/gold/`) is 200 real postings from public boards. Their labels were written by
READING the posting text, never by running the extractor, so the score is not circular. For every posting the
chain is the production one (extraction ladder, rules only, no AI -> stored-column mapping -> decisions), so a
rule change that hurts real postings shows up here and fails the build.

What is measured (precision first: a wrong job shown costs a fresher an hour, a missed one costs less):
  feed        among the postings the feed would SHOW (remote, India-eligible, full-time-ish, no hard flag),
              how many really are remote, India-eligible, full-time-ish and clean   (+ recall)
  remote      among postings predicted remote, how many are
  india       among postings predicted India-eligible, how many are (gold remote jobs with an India label)
  hidden kind among postings predicted internship / part-time / volunteer / temporary, how many are
  flag:<name> per hard flag, precision and recall
  entry       among postings predicted entry level, how many the gold set says are NOT entry (false entries)

Split: label files named `holdout_*.yaml` are the HOLDOUT (only counts are shown, never its misses, so the rules are
not fitted to them); every other label file is DEV (tune the rules against its misses). A holdout posting that has been
looked at moves to DEV, and a fresh holdout is labelled from postings the rules have never seen.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from etl.decide import ALL_HARD_FLAGS, HIDDEN_KINDS, decide
from etl.extract.ladder import StoredDocument, extract
from etl.runner.process import PendingDoc, to_posting_row

GOLD_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "gold"
NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)
HARD = set(ALL_HARD_FLAGS)
HIDDEN = set(HIDDEN_KINDS)

#: Minimum PRECISION per metric (a metric with fewer than MIN_SAMPLES predictions is not judged: too small to mean
#: anything). Set from honest runs on postings the rules had not been tuned on, with a margin, and only ever raised.
THRESHOLDS: dict[str, float] = {
    "feed": 0.95,
    "remote": 0.93,
    "india": 0.95,
    "hidden_kind": 0.90,
    "flag:language_required": 0.90,
    "flag:non_english": 0.90,
    "flag:not_a_job": 0.90,
}
MIN_SAMPLES = 5


@dataclass
class Gold:
    gid: str
    ats: str
    slug: str
    jid: str
    job: dict
    remote: str
    india: str
    employment: str
    entry: str
    flags: list[str]
    why: str
    split: str


@dataclass
class Pred:
    remote: str
    india: str
    kind: str
    flags: list[str]
    entry: str
    title: str = ""


@dataclass
class Metric:
    name: str
    hits: int
    predicted: int
    gold_positive: int = 0
    true_positive: int = 0

    @property
    def precision(self) -> float | None:
        return None if self.predicted == 0 else self.hits / self.predicted

    @property
    def recall(self) -> float | None:
        return None if self.gold_positive == 0 else self.true_positive / self.gold_positive


@dataclass
class Report:
    metrics: dict[str, dict[str, Metric]] = field(default_factory=dict)  # split -> name -> metric
    misses: dict[str, list[str]] = field(default_factory=dict)  # metric -> readable lines (dev only)
    failures: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def split_of_file(path: Path) -> str:
    return "holdout" if path.name.startswith("holdout") else "dev"


def load_gold(directory: Path = GOLD_DIR) -> list[Gold]:
    labels: dict[str, dict] = {}
    for path in sorted((directory / "labels").glob("*.yaml")):
        for item in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
            gid = str(item["gid"])
            if gid in labels:
                raise ValueError(f"gold label {gid} appears twice")
            labels[gid] = {**item, "_split": split_of_file(path)}
    out: list[Gold] = []
    for line in (directory / "postings.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        p = json.loads(line)
        lab = labels.pop(p["gid"], None)
        if lab is None:
            raise ValueError(f"gold posting {p['gid']} has no label")
        out.append(Gold(
            gid=p["gid"], ats=p["ats"], slug=p["slug"], jid=p["jid"], job=p["job"],
            remote=str(lab["remote"]), india=str(lab["india"]), employment=str(lab["employment"]),
            entry=str(lab["entry"]), flags=list(lab.get("flags") or []), why=str(lab.get("why") or ""),
            split=lab["_split"],
        ))
    if labels:
        raise ValueError(f"labels without a posting: {sorted(labels)}")
    return out


def entry_level(row: dict[str, Any], kind: str) -> str:
    """The feed's entry-level rule in Python: yes | no | unknown (mirrors ENTRY_LEVEL in web/lib/queries/feed.ts)."""
    seniority, lo, hi = row.get("seniority"), row.get("experience_min_years"), row.get("experience_max_years")
    if kind in HIDDEN:
        return "no"
    if seniority in ("senior", "lead", "staff", "principal", "director", "mid"):
        return "no"
    if seniority == "entry":
        return "yes" if lo is None or lo <= 2 else "no"
    if seniority is None:
        if (lo is not None and lo <= 2) or (hi is not None and hi <= 2):
            return "yes"
        if lo is not None and lo >= 3:
            return "no"
    return "unknown"


def predict(item: Gold) -> Pred:
    doc = StoredDocument(source=item.ats, source_key=f"{item.slug}/{item.jid}", url="https://example.test",
                         body=json.dumps(item.job).encode(), content_type="application/json")
    ex = extract(doc)
    row = to_posting_row(PendingDoc(1, item.ats, f"{item.slug}/{item.jid}", doc.url, "application/json", "h", b""),
                         ex, board_id=None, company_id=None, now=NOW)
    view = dict(row)
    for key in ("locations", "timezone_window"):
        if isinstance(view.get(key), str):
            view[key] = json.loads(view[key])
    d = decide(view)
    return Pred(remote=view.get("remote_type") or "unknown", india=d.india_eligible, kind=d.employment_kind,
                flags=list(d.flags), entry=entry_level(view, d.employment_kind), title=view.get("title") or "")


def _shown(remote: str, india: str, kind: str, flags: Iterable[str]) -> bool:
    return remote == "remote" and india == "yes" and kind not in HIDDEN and not (set(flags) & HARD)


def _score(name: str, pairs: list[tuple[bool, bool]]) -> Metric:
    """pairs of (predicted positive, gold positive)."""
    predicted = sum(1 for p, _ in pairs if p)
    hits = sum(1 for p, g in pairs if p and g)
    gold_pos = sum(1 for _, g in pairs if g)
    return Metric(name, hits=hits, predicted=predicted, gold_positive=gold_pos, true_positive=hits)


def evaluate(items: list[Gold] | None = None) -> Report:
    items = load_gold() if items is None else items
    report = Report()
    preds = {g.gid: predict(g) for g in items}
    report.counts = {"total": len(items), "dev": sum(1 for g in items if g.split == "dev"),
                     "holdout": sum(1 for g in items if g.split == "holdout")}
    for split in ("dev", "holdout", "all"):
        subset = [g for g in items if split == "all" or g.split == split]
        m: dict[str, Metric] = {}
        m["feed"] = _score("feed", [
            (_shown(preds[g.gid].remote, preds[g.gid].india, preds[g.gid].kind, preds[g.gid].flags),
             _shown(g.remote, g.india, g.employment, g.flags)) for g in subset])
        m["remote"] = _score("remote", [(preds[g.gid].remote == "remote", g.remote == "remote") for g in subset])
        scoped = [g for g in subset if g.remote == "remote" and g.india != "na"]
        m["india"] = _score("india", [(preds[g.gid].india == "yes", g.india == "yes") for g in scoped])
        m["hidden_kind"] = _score("hidden_kind", [(preds[g.gid].kind in HIDDEN, g.employment in HIDDEN) for g in subset])
        for flag in sorted(HARD):
            pairs = [(flag in preds[g.gid].flags, flag in g.flags) for g in subset]
            if any(p or g for p, g in pairs):
                m[f"flag:{flag}"] = _score(f"flag:{flag}", pairs)
        entry_pred = [g for g in subset if preds[g.gid].entry == "yes"]
        m["entry_false"] = Metric("entry_false", hits=sum(1 for g in entry_pred if g.entry != "no"), predicted=len(entry_pred),
                                  gold_positive=sum(1 for g in subset if g.entry == "yes"),
                                  true_positive=sum(1 for g in entry_pred if g.entry == "yes"))
        report.metrics[split] = m

    for g in items:
        if g.split != "dev":
            continue  # never show the holdout's misses: the rules must not be fitted to them
        p = preds[g.gid]
        line = f"{g.slug} | {p.title[:55]} | gold: remote={g.remote} india={g.india} emp={g.employment} entry={g.entry} flags={g.flags} | pred: remote={p.remote} india={p.india} kind={p.kind} entry={p.entry} flags={p.flags} | {g.why[:90]}"
        if _shown(p.remote, p.india, p.kind, p.flags) and not _shown(g.remote, g.india, g.employment, g.flags):
            report.misses.setdefault("feed (shown but should not be)", []).append(line)
        if not _shown(p.remote, p.india, p.kind, p.flags) and _shown(g.remote, g.india, g.employment, g.flags):
            report.misses.setdefault("feed (should show, hidden)", []).append(line)
        if p.remote == "remote" and g.remote != "remote":
            report.misses.setdefault("remote (predicted remote, is not)", []).append(line)
        if g.remote == "remote" and p.remote != "remote":
            report.misses.setdefault("remote (is remote, missed)", []).append(line)
        if g.remote == "remote" and g.india != "na" and p.india == "yes" and g.india != "yes":
            report.misses.setdefault("india (predicted yes, is not)", []).append(line)
        if g.remote == "remote" and g.india == "yes" and p.india != "yes":
            report.misses.setdefault("india (is yes, missed)", []).append(line)
        if p.kind in HIDDEN and g.employment not in HIDDEN:
            report.misses.setdefault("kind (hidden wrongly)", []).append(line)
        if g.employment in HIDDEN and p.kind not in HIDDEN:
            report.misses.setdefault("kind (internship etc. missed)", []).append(line)
        for flag in HARD:
            if flag in p.flags and flag not in g.flags:
                report.misses.setdefault(f"flag {flag} (false alarm)", []).append(line)
            if flag in g.flags and flag not in p.flags:
                report.misses.setdefault(f"flag {flag} (missed)", []).append(line)
        if p.entry == "yes" and g.entry == "no":
            report.misses.setdefault("entry (predicted entry, gold says not)", []).append(line)

    for split in ("dev", "holdout", "all"):
        for name, floor in THRESHOLDS.items():
            metric = report.metrics[split].get(name)
            if (metric is not None and metric.precision is not None and metric.predicted >= MIN_SAMPLES
                    and metric.precision < floor):
                report.failures.append(f"{split}: {name} precision {metric.precision:.3f} is below {floor}")
    return report


def _pct(x: float | None) -> str:
    return "  n/a" if x is None else f"{x * 100:5.1f}"


def format_report(report: Report, show_misses: bool = True) -> str:
    lines = [f"gold set: {report.counts['total']} postings ({report.counts['dev']} dev, {report.counts['holdout']} holdout)", ""]
    lines.append(f"{'metric':<26}{'split':<9}{'precision':>10}{'recall':>8}{'predicted':>10}{'correct':>9}")
    for name in report.metrics["all"]:
        for split in ("dev", "holdout"):
            m = report.metrics[split].get(name)
            if m is None:
                continue
            lines.append(f"{name:<26}{split:<9}{_pct(m.precision):>10}{_pct(m.recall):>8}{m.predicted:>10}{m.hits:>9}")
    if show_misses:
        for name, rows in sorted(report.misses.items()):
            lines += ["", f"-- {name} ({len(rows)}) [dev only]"] + ["   " + r for r in rows[:25]]
    if report.failures:
        lines += ["", "FAILED:"] + ["  " + f for f in report.failures]
    return "\n".join(lines)


__all__ = ["evaluate", "load_gold", "predict", "format_report", "THRESHOLDS", "Gold", "Pred", "Report", "split_of_file"]
