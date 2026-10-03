"""Process runner: raw documents in, structured postings out.

Pattern (same as collect): ONE read query, then NO database connection while the pure extraction
runs, then ONE write transaction per batch. A raw document is "pending" while its gzipped body
(`clean_text_gz`) is still in the database; the write transaction clears the body of every
document it consumed, so a crashed or failed extraction is simply retried next run. The long-term
copy of every body lives in the raw archive, not in Neon.

Logs and output carry counts and ids only (the Actions logs of this repository are public).
"""
from __future__ import annotations

import gzip
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from sqlalchemy import bindparam, text

from etl.core.db import batch_upsert, session_scope
from etl.core.types import Field
from etl.extract.ladder import StoredDocument, extract
from etl.extract.model import FIELD_KEYS, Extracted

EXTRACTION_VERSION = 1

PROVENANCE = {"jsonld", "source", "rule", "llm", "user", "unknown"}
REMOTE_TYPES = {"remote", "hybrid", "onsite"}
ELIGIBILITY_SCOPES = {"worldwide", "regions", "countries"}
VISA = {"yes", "no"}
PAY_PERIODS = {"hour", "day", "month", "year"}

_NORMALIZE_STRIP = re.compile(r"[^\w\s+#.]", re.UNICODE)


@dataclass(frozen=True)
class PendingDoc:
    """One raw document waiting for extraction, with its board and company if known."""

    id: int
    source: str
    source_key: str
    url: str
    content_type: str
    content_hash: str
    body: bytes
    board_id: int | None = None
    company_id: int | None = None


@dataclass
class ProcessResult:
    seen: int = 0
    written: int = 0
    skipped: int = 0
    failed: int = 0
    conflicts: int = 0
    batches: int = 0
    failed_ids: list[int] = field(default_factory=list)


def normalize_title(title: str) -> str:
    """Lower case, punctuation (other than + # .) removed, whitespace collapsed."""
    cleaned = _NORMALIZE_STRIP.sub(" ", (title or "").lower())
    return " ".join(cleaned.split())


def split_source_id(source_key: str) -> str:
    """The posting id is the part of the key after the first `/` (the whole key without one)."""
    _, sep, rest = (source_key or "").partition("/")
    return rest if sep and rest else (source_key or "")


_INT4_MAX_YEARS = 60          # experience years beyond this are not a requirement
_NUMERIC_LIMIT = Decimal(10) ** 12  # pay beyond a trillion is junk, and keeps numeric sane


def _clean_text(value: str) -> str:
    """Postgres rejects NUL and unpaired surrogates: drop NUL, replace the rest."""
    return value.replace("\x00", "").encode("utf-8", "replace").decode("utf-8")


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, Mapping):
        return {_clean_text(str(k)): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _prov(f: Field | None) -> str:
    p = getattr(f, "provenance", None)
    return p if p in PROVENANCE else "unknown"


def _known(f: Field | None) -> bool:
    return f is not None and f.value is not None


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return value


def _json_text(value: Any) -> str | None:
    try:
        return json.dumps(_clean(_jsonable(value)), ensure_ascii=False)
    except Exception:
        return None


def _enum(f: Field | None, allowed: set[str]) -> tuple[str | None, str]:
    if _known(f) and f.value in allowed:
        return f.value, _prov(f)
    return None, "unknown"


def _aware(f: Field | None) -> tuple[datetime | None, str]:
    if _known(f) and isinstance(f.value, datetime) and f.value.tzinfo is not None:
        return f.value, _prov(f)
    return None, "unknown"


def _int(f: Field | None) -> tuple[int | None, str]:
    if not _known(f) or isinstance(f.value, bool):
        return None, "unknown"
    try:
        n = int(f.value)
    except (TypeError, ValueError, OverflowError):
        return None, "unknown"
    return (n, _prov(f)) if 0 <= n <= _INT4_MAX_YEARS else (None, "unknown")


def _number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() and abs(d) < _NUMERIC_LIMIT else None


def _text_list(f: Field | None) -> tuple[list[str] | None, str]:
    if _known(f) and isinstance(f.value, (list, tuple)):
        items = [str(x) for x in f.value if isinstance(x, str) and x]
        if items:
            return items, _prov(f)
    return None, "unknown"


def _text(f: Field | None) -> tuple[str | None, str]:
    if _known(f) and isinstance(f.value, str) and f.value.strip():
        return f.value, _prov(f)
    return None, "unknown"


def _json_field(f: Field | None) -> tuple[str | None, str]:
    if not _known(f):
        return None, "unknown"
    dumped = _json_text(f.value)
    return (dumped, _prov(f)) if dumped is not None else (None, "unknown")


def _employment(f: Field | None) -> tuple[str | None, str]:
    """JSON-LD gives a list of types; the column holds them comma-joined."""
    if _known(f) and isinstance(f.value, (list, tuple)):
        items = [str(x) for x in f.value if isinstance(x, str) and x]
        return (",".join(items), _prov(f)) if items else (None, "unknown")
    return _text(f)


def to_posting_row(
    raw: PendingDoc,
    extracted: Extracted,
    *,
    board_id: int | None,
    company_id: int | None,
    now: datetime,
) -> dict[str, Any]:
    """Map one `Extracted` plus its raw document to `postings` columns. Never raises."""
    fields = extracted.fields
    row: dict[str, Any] = {
        "raw_document_id": raw.id,
        "source": raw.source,
        "source_id": split_source_id(raw.source_key),
        "board_id": board_id,
        "company_id": company_id,
        "title": extracted.title or raw.source_key,
        "title_normalized": normalize_title(extracted.title or raw.source_key),
        "status": "open",
        "content_hash": raw.content_hash,
        "extraction_version": EXTRACTION_VERSION,
        "first_seen_at": now,
        "last_seen_at": now,
        "missing_polls": 0,
    }
    description, _ = _text(fields.get("description_md"))
    row["description_md"] = description or ""
    row["requisition_id"], _ = _text(fields.get("requisition_id"))
    row["apply_url_raw"], _ = _text(fields.get("apply_url"))

    row["employment_type"], row["employment_type_provenance"] = _employment(fields.get("employment_type"))
    row["seniority"], row["seniority_provenance"] = _text(fields.get("seniority"))
    row["experience_min_years"], row["experience_min_years_provenance"] = _int(fields.get("experience_min_years"))
    row["experience_max_years"], row["experience_max_years_provenance"] = _int(fields.get("experience_max_years"))
    row["remote_type"], row["remote_type_provenance"] = _enum(fields.get("remote_type"), REMOTE_TYPES)
    row["locations"], row["locations_provenance"] = _json_field(fields.get("locations"))
    row["eligible_countries"], row["eligible_countries_provenance"] = _text_list(fields.get("eligible_countries"))
    row["eligibility_scope"], row["eligibility_scope_provenance"] = _enum(
        fields.get("eligibility_scope"), ELIGIBILITY_SCOPES)
    row["timezone_window"], row["timezone_window_provenance"] = _json_field(fields.get("timezone_window"))
    row["visa_sponsorship"], row["visa_sponsorship_provenance"] = _enum(fields.get("visa_sponsorship"), VISA)
    row["work_auth_required"], row["work_auth_required_provenance"] = _text_list(fields.get("work_auth_required"))

    pay_field = fields.get("pay")
    pay = pay_field.value if _known(pay_field) and isinstance(pay_field.value, Mapping) else None
    row.update({
        "pay_min": None, "pay_max": None, "pay_currency": None, "pay_period": None,
        "pay_min_inr_annual": None, "pay_max_inr_annual": None,
        "pay_disclosed": False, "pay_fx_date": None, "pay_provenance": "unknown",
    })
    if pay is not None:
        lo, hi = _number(pay.get("min")), _number(pay.get("max"))
        if lo is not None or hi is not None:
            period = pay.get("period") if pay.get("period") in PAY_PERIODS else None
            currency = pay.get("currency")
            row.update({
                "pay_min": lo, "pay_max": hi,
                "pay_currency": currency if isinstance(currency, str) and currency else None,
                "pay_period": period,
                "pay_min_inr_annual": _number(pay.get("annual_inr_min")),
                "pay_max_inr_annual": _number(pay.get("annual_inr_max")),
                "pay_disclosed": bool(pay.get("disclosed", True)),
                "pay_provenance": _prov(pay_field),
            })

    row["posted_at"], row["posted_at_provenance"] = _aware(fields.get("posted_at"))
    row["deadline_at"], row["deadline_at_provenance"] = _aware(fields.get("deadline_at"))
    row["joining"], row["joining_provenance"] = _json_field(fields.get("joining"))
    if row["pay_min"] is None and row["pay_max"] is None:
        row.update({"pay_currency": None, "pay_period": None, "pay_disclosed": False, "pay_provenance": "unknown"})
    return {k: _clean(v) if isinstance(v, (str, list)) else v for k, v in row.items()}


# Every column except identity and the first-seen timestamp is rewritten on conflict.
INSERT_COLUMNS: tuple[str, ...] = tuple(
    to_posting_row(
        PendingDoc(0, "s", "k", "u", "t", "h", b""),
        Extracted(title="t", fields={k: Field(None, "unknown") for k in FIELD_KEYS}),
        board_id=None, company_id=None, now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ).keys()
)
UPDATE_COLUMNS: tuple[str, ...] = tuple(
    c for c in INSERT_COLUMNS if c not in ("source", "source_id", "first_seen_at")
)

_PENDING_SQL = text(
    "SELECT r.id, r.source, r.source_key, r.url, r.content_type, r.content_hash, r.clean_text_gz, "
    "b.id AS board_id, b.company_id AS company_id "
    "FROM hunterrr.raw_documents r "
    "LEFT JOIN hunterrr.boards b ON b.ats::text = r.source AND b.slug = (r.fetch_meta->>'slug') "
    "WHERE r.clean_text_gz IS NOT NULL AND r.id > :after "
    "ORDER BY r.id LIMIT :n"
)
_CONSUME_SQL = text(
    "UPDATE hunterrr.raw_documents SET clean_text_gz = NULL WHERE id IN :ids"
).bindparams(bindparam("ids", expanding=True))


def _decode_body(blob: Any) -> bytes:
    data = bytes(blob)
    try:
        return gzip.decompress(data)
    except Exception:
        return data  # tolerate an uncompressed body


def _write(conn, rows: list[dict[str, Any]], consumed: list[int]) -> None:
    if rows:
        batch_upsert(
            conn, "hunterrr.postings", rows,
            conflict_cols=["source", "source_id"], update_cols=list(UPDATE_COLUMNS),
            # A posting is only ever replaced by data from the same or a NEWER raw document.
            update_where='"postings"."raw_document_id" <= EXCLUDED."raw_document_id"',
        )
    if consumed:
        conn.execute(_CONSUME_SQL, {"ids": consumed})


def process(
    engine,
    *,
    batch_size: int = 200,
    limit: int | None = None,
    now: datetime | None = None,
    release_connections: bool = True,
) -> ProcessResult:
    """Extract pending raw documents and upsert their postings. Idempotent."""
    result = ProcessResult()
    stamp = now or datetime.now(timezone.utc)
    after = 0
    while limit is None or result.seen < limit:
        n = batch_size if limit is None else min(batch_size, limit - result.seen)
        with session_scope(engine) as conn:
            rows = conn.execute(_PENDING_SQL, {"after": after, "n": n}).fetchall()
        if release_connections:
            engine.dispose()
        if not rows:
            break
        pending = [
            PendingDoc(
                id=int(r.id), source=r.source, source_key=r.source_key, url=r.url,
                content_type=r.content_type, content_hash=r.content_hash,
                body=_decode_body(r.clean_text_gz), board_id=r.board_id, company_id=r.company_id,
            )
            for r in rows
        ]
        after = pending[-1].id
        result.seen += len(pending)
        result.batches += 1

        by_key: dict[tuple[str, str], dict[str, Any]] = {}
        doc_ids: dict[tuple[str, str], list[int]] = {}
        for doc in pending:
            try:
                extracted = extract(StoredDocument(
                    doc.source, doc.source_key, doc.url, doc.body, doc.content_type))
                row = to_posting_row(
                    doc, extracted, board_id=doc.board_id, company_id=doc.company_id, now=stamp)
                result.conflicts += len(extracted.conflicts)
            except Exception:
                result.failed += 1
                result.failed_ids.append(doc.id)
                continue
            key = (row["source"], row["source_id"])
            if key in by_key:
                result.skipped += 1  # an older version of the same posting in this batch
            by_key[key] = row  # ids ascend, so the newest document wins
            doc_ids.setdefault(key, []).append(doc.id)

        try:
            with session_scope(engine) as conn:
                _write(conn, list(by_key.values()), [i for ids in doc_ids.values() for i in ids])
            result.written += len(by_key)
        except Exception:
            # One row Postgres rejects must not block the batch forever: retry row by row.
            for key, row in by_key.items():
                try:
                    with session_scope(engine) as conn:
                        _write(conn, [row], doc_ids[key])
                    result.written += 1
                except Exception:
                    result.failed += 1
                    result.failed_ids.extend(doc_ids[key])
        if release_connections:
            engine.dispose()
    return result


def record_run(engine, result: ProcessResult, *, started_at: datetime, status: str, error: str = "") -> None:
    """Write the `runs` ledger row (counts only)."""
    counts = {
        "seen": result.seen, "written": result.written, "skipped": result.skipped,
        "failed": result.failed, "conflicts": result.conflicts, "batches": result.batches,
    }
    with session_scope(engine) as conn:
        conn.execute(
            text(
                "INSERT INTO hunterrr.runs (workflow, shard, started_at, finished_at, status, counts, error_summary) "
                "VALUES ('process', 0, :started, :finished, CAST(:status AS hunterrr.run_status), "
                "CAST(:counts AS jsonb), :summary)"
            ),
            {
                "started": started_at, "finished": datetime.now(timezone.utc), "status": status,
                "counts": json.dumps(counts), "summary": error,
            },
        )


__all__ = [
    "EXTRACTION_VERSION", "PendingDoc", "ProcessResult", "normalize_title", "split_source_id",
    "to_posting_row", "process", "record_run",
]
