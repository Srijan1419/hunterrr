"""Record a cassette: one live call per posting, against a real fixture, into a real file.

    python -m etl.llm.capture --posting remoteok:1137431 --out etl/tests/cassettes/residue.json

Owned by task f1-08. This is how a cassette gets made, and it is a module rather than a flag on
the test suite because **a test that can record is a test that can hit the network.** The suite
replays; only this writes.

What it does, in order, so there is nothing to remember when re-recording:

1. Reads the posting out of a committed fixture, through the real `etl.normalize` adapter —
   the same path a production run takes, so the description in the request is the description
   the extractor would send.
2. Builds the ladder context by running the real `normalize_posting` with a context-capturing
   resolver, so the recorded request is byte-for-byte the request the test will replay.
3. Calls the live endpoint with the real client, real 1.5-second spacing, and validates the
   response against the real schema. **A recording of an answer that fails validation is not
   written** — a cassette exists to prove the extractor works, and a broken one would only
   prove the test is not looking.
4. Writes the cassette, and refuses to write a file containing anything shaped like a key.

The key comes from the environment and is never printed: this module reports what it
recorded — model, posting, the values it settled, the request fingerprint — and the cache
statistics. Not the credential.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..normalize.normalizer import normalize_posting
from .cache import ExtractionCache
from .cassette import Cassette, RecordTransport, new_cassette
from .client import LlmClient, RateLimiter, http_transport
from .providers import get_provider
from .resolver import SkillExtractor

#: `hunterrr/`, so the CLI works from anywhere.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = PROJECT_ROOT / "etl" / "fixtures"

#: source -> the committed capture holding that source's postings. Only the files this task
#: records from are listed, so a typo is an error rather than a wild fixture.
FIXTURE_FILES = {
    "remoteok": "remoteok/remoteok_sample.json",
    "jobicy": "jobicy/jobicy_sample.json",
    "himalayas": "himalayas/himalayas_sample.json",
}


def postings_for(source: str, relative: str | None = None) -> list[dict]:
    """Every posting in `source`'s committed capture, as `raw_jobs` rows.

    `raw_jobs.payload` is the source's own JSON text (schema.md §2), so a row is assembled
    rather than read: the normalizer parses it and must not be handed anything else.
    """
    page = json.loads((FIXTURES / (relative or FIXTURE_FILES[source])).read_text(encoding="utf-8"))
    if isinstance(page, dict) and "rows" in page:  # the ATS capture envelope
        payloads = [entry["row"] for entry in page["rows"]]
    elif isinstance(page, dict) and "jobs" in page:  # the API sources' envelope
        payloads = page["jobs"]
    else:
        payloads = page
    return [
        {
            "source": source,
            "source_id": str(payload.get("id") or payload.get("guid") or ""),
            "fetched_at": "2026-09-28T00:00:00Z",
            "content_hash": "",
            "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        }
        for payload in payloads
        if isinstance(payload, dict) and (payload.get("id") or payload.get("guid"))
    ]


def context_for(source: str, source_id: str, relative: str | None = None) -> dict:
    """The exact ladder context `normalizer` builds for one posting.

    Captured rather than reimplemented: a hand-built context would drift from f1-07's the
    first time a key was added, and the recorded cassette would then be a recording of a
    request the production code never makes. The tests import this rather than copying it, for
    the same reason.
    """
    captured: dict = {}

    def capture(context):
        captured.update(context)
        return None  # no model call: this pass exists only to capture the context

    for row in postings_for(source, relative):
        if row["source_id"] == str(source_id):
            normalize_posting(row, llm_resolve=capture)
            if not captured:
                raise SystemExit(f"{source}:{source_id} reached no step-3 decision; nothing to record")
            return captured
    raise SystemExit(f"{source}:{source_id} is not in {relative or FIXTURE_FILES.get(source)!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record an llm cassette from a real fixture posting.")
    parser.add_argument(
        "--posting",
        required=True,
        help="`source:source_id`, e.g. remoteok:1137431. Its title and tags must leave "
             "seniority or role_type at step 4, or step 3 is never reached and nothing is "
             "recorded.",
    )
    parser.add_argument("--out", required=True, help="the cassette file to write")
    parser.add_argument("--provider", default=None, help="defaults to nvidia")
    parser.add_argument("--model", default=None, help="defaults to the provider's own model")
    parser.add_argument(
        "--fixture",
        default=None,
        help="a capture path under etl/fixtures, e.g. remoteok/remoteok_long_description.json, "
             "for a posting that is not in the default capture",
    )
    parser.add_argument(
        "--note", default="", help="one line on what this cassette covers, stored in the file"
    )
    args = parser.parse_args(argv)

    source, _, source_id = args.posting.partition(":")
    if not source_id:
        parser.error(f"--posting must be `source:source_id`, got {args.posting!r}")

    provider = get_provider(args.provider, model=args.model)  # reads the key from the env
    context = context_for(source, source_id, args.fixture)
    cassette: Cassette = new_cassette(
        name=Path(args.out).stem,
        provider=provider,
        note=args.note
        or f"real response for {args.posting} from {args.fixture or FIXTURE_FILES[source]}",
    )
    recorder = RecordTransport(cassette, http_transport, provider=provider)
    extractor = SkillExtractor(
        cache=ExtractionCache(":memory:"),
        client=LlmClient(
            provider=provider,
            transport=recorder,
            limiter=RateLimiter(min_interval=provider.min_call_interval),
        ),
    )
    result = extractor(context)
    if not recorder.recorded:
        print(
            f"nothing recorded for {args.posting}: the resolver declined "
            f"(description_chars={context.get('description_chars')!r}). A posting whose steps 1 "
            f"and 2 both answer never reaches the model, which is the ladder working, not a "
            f"failure — pick a row that lands on step 4 for seniority or role_type.",
            file=sys.stderr,
        )
        return 1
    if extractor.stats.schema_violations:
        print(
            f"refusing to record: the response for {args.posting} failed the extraction schema",
            file=sys.stderr,
        )
        return 1
    written = cassette.save(args.out)
    print(f"recorded {len(cassette)} exchange(s) to {written}")
    print(f"  posting    {args.posting}")
    print(f"  provider   {provider.name} / {provider.model}")
    print(f"  settled    {sorted(result.values) if result else 'nothing'}")
    print(f"  stats      {json.dumps(extractor.stats.as_dict(), sort_keys=True)}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
