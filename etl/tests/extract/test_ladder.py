"""Task h2-11 criterion 4: the ladder (merge, conflicts, title, never-raise)."""

import json
import random
from datetime import datetime, timezone
from pathlib import Path

from etl.core.types import RawDocument
from etl.extract.ladder import StoredDocument, extract
from etl.extract.model import FIELD_KEYS

REPO_FIX = Path(__file__).resolve().parents[2] / "fixtures"
FIX = REPO_FIX / "extract"


def doc(source: str, body: bytes, key: str = "k-1") -> StoredDocument:
    return StoredDocument(source=source, source_key=key, url="https://example.test/x",
                          body=body, content_type="application/json")


def json_doc(source: str, payload, key: str = "k-1") -> StoredDocument:
    return doc(source, json.dumps(payload).encode(), key)


def test_every_key_always_present():
    gh = json.loads((REPO_FIX / "ats_greenhouse_sample.json").read_text(encoding="utf-8"))["jobs"][0]
    ex = extract(json_doc("greenhouse", gh))
    assert set(ex.fields) == set(FIELD_KEYS)
    assert ex.title == gh["title"]
    assert ex.skills == () and ex.llm_calls == 0
    assert ex.conflicts == ()


def test_lever_and_ashby_bodies_map():
    lv = json.loads((REPO_FIX / "ats_lever_sample.json").read_text(encoding="utf-8"))[0]
    ex = extract(json_doc("lever", lv))
    assert set(ex.fields) == set(FIELD_KEYS)
    assert ex.fields["remote_type"].value == "hybrid"
    ab = json.loads((REPO_FIX / "ats_ashby_sample.json").read_text(encoding="utf-8"))["jobs"][0]
    ex = extract(json_doc("ashby", ab))
    # the captured posting says isRemote=true AND workplaceType=Hybrid: Ashby sets isRemote for hybrid roles too
    assert ex.fields["remote_type"].value == "hybrid"


def test_ashby_work_mode_comes_from_workplace_type_and_is_remote_is_only_a_fallback():
    base = {"title": "Engineer", "id": "1", "location": "Berlin", "descriptionPlain": "A job."}
    mode = lambda **kw: extract(json_doc("ashby", {**base, **kw})).fields["remote_type"].value
    assert mode(isRemote=True, workplaceType="Hybrid") == "hybrid"
    assert mode(isRemote=True, workplaceType="OnSite") == "onsite"
    assert mode(isRemote=True, workplaceType="Remote") == "remote"
    assert mode(isRemote=False, workplaceType="Remote") == "remote"
    assert mode(isRemote=True) == "remote"          # no workplaceType: isRemote is all we have
    assert mode(isRemote=None, workplaceType=None) is None


def test_unknown_source_uses_no_mapper():
    ex = extract(doc("jobicy", b'{"title": "Whatever"}'))
    assert ex.title == "k-1"
    assert ex.conflicts == ("title: missing",)
    assert all(v.value is None for v in ex.fields.values())


def test_garbage_bytes_never_raise_and_stay_unknown():
    for body in (b"", b"\xff\xfe\x00not json", b"<html><p>no json-ld here", b"{{{"):
        ex = extract(doc("greenhouse", body))
        assert set(ex.fields) == set(FIELD_KEYS)
        assert all(v.value is None and v.provenance == "unknown"
                   for v in ex.fields.values())


def test_jsonld_in_description_html_applies_and_wins_conflicts():
    ld = ("<script type=\"application/ld+json\">"
          '{"@type": "JobPosting", "title": "LD Title",'
          ' "url": "https://ld.test/apply"}'
          "</script>")
    payload = {"title": "Board Title", "absolute_url": "https://board.test/apply",
               "content": f"<p>Hi</p>{ld}"}
    ex = extract(json_doc("greenhouse", payload))
    assert ex.title == "LD Title"  # JSON-LD title wins
    assert ex.fields["apply_url"].value == "https://ld.test/apply"
    assert ex.fields["apply_url"].provenance == "jsonld"
    assert any(c.startswith("apply_url: jsonld ") and "vs source" in c
               for c in ex.conflicts)
    assert any(c.startswith("title: jsonld ") for c in ex.conflicts)


def test_agreement_keeps_jsonld_provenance_without_conflict():
    ld = ("<script type=\"application/ld+json\">"
          '{"@type": "JobPosting", "title": "Same",'
          ' "url": "https://board.test/apply"}'
          "</script>")
    payload = {"title": "Same", "absolute_url": "https://board.test/apply",
               "content": ld}
    ex = extract(json_doc("greenhouse", payload))
    assert ex.title == "Same"
    assert ex.fields["apply_url"].provenance == "jsonld"
    assert not [c for c in ex.conflicts if c.startswith("apply_url:")]
    assert not [c for c in ex.conflicts if c.startswith("title: jsonld")]


def test_single_rung_fields_keep_their_provenance():
    lv = json.loads((REPO_FIX / "ats_lever_sample.json").read_text(encoding="utf-8"))[0]
    ex = extract(json_doc("lever", lv))
    assert ex.fields["employment_type"].provenance == "source"
    assert ex.fields["eligibility_scope"].value is None  # no rung knows it


def test_plain_remote_is_never_worldwide_at_ladder_level():
    lv = json.loads((REPO_FIX / "ats_lever_sample.json").read_text(encoding="utf-8"))
    remote = next(p for p in lv if p.get("workplaceType") == "remote")
    ex = extract(json_doc("lever", remote))
    assert ex.fields["remote_type"].value == "remote"
    assert ex.fields["eligibility_scope"].value is None
    assert ex.fields["eligible_countries"].value is None


def test_raw_document_input_supported():
    gh = json.loads((REPO_FIX / "ats_greenhouse_sample.json").read_text(encoding="utf-8"))["jobs"][0]
    raw = RawDocument(source="greenhouse", source_key="gh-1", url="https://x.test",
                      fetched_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                      http_status=200, content_type="application/json",
                      body=json.dumps(gh).encode())
    ex = extract(raw)
    assert ex.title == gh["title"]


def test_windows_1252_fallback_decodes():
    body = '{"title": "Caf\xe9 role", "location": {"name": "Lyon"}}'.encode("latin-1")
    ex = extract(doc("greenhouse", body))
    assert ex.title == "Café role"


def test_determinism_same_input_equal_output():
    gh = json.loads((REPO_FIX / "ats_greenhouse_sample.json").read_text(encoding="utf-8"))["jobs"][0]
    first = extract(json_doc("greenhouse", gh))
    second = extract(json_doc("greenhouse", gh))
    assert first == second


def _mutated(blob: bytes, rng: random.Random) -> bytes:
    if not blob:
        return blob
    ops = rng.choice(["flip", "truncate", "splice"])
    if ops == "truncate":
        return blob[:rng.randrange(len(blob))]
    buf = bytearray(blob)
    if ops == "flip":
        for _ in range(rng.randrange(1, 6)):
            buf[rng.randrange(len(buf))] = rng.randrange(256)
    else:
        pos = rng.randrange(len(buf))
        buf[pos:pos] = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 8)))
    return bytes(buf)


def test_extract_never_raises_on_300_random_and_mutated_inputs():
    rng = random.Random(20260914)
    alphabet = b"<>{}[]\"':,/@._ abcdefgh0123456789\xff\xfe\x00\xe9"
    gh = (REPO_FIX / "ats_greenhouse_sample.json").read_bytes()
    lv = (REPO_FIX / "ats_lever_sample.json").read_bytes()
    ab = (REPO_FIX / "ats_ashby_sample.json").read_bytes()
    full_html = (FIX / "full_jobposting.html").read_bytes()
    for i in range(300):
        if i % 3 == 0:
            blob = bytes(rng.choice(alphabet) for _ in range(rng.randrange(0, 400)))
        else:
            blob = _mutated(rng.choice([gh, lv, ab, full_html]), rng)
        source = rng.choice(["greenhouse", "lever", "ashby", "other", ""])
        ex = extract(doc(source, blob, key=f"k-{i}"))  # must not raise
        assert set(ex.fields) == set(FIELD_KEYS)
        assert isinstance(ex.title, str) and ex.title
