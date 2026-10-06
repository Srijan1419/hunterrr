"""Career-page source (dq-20): JSON-LD on the page or one level of job links; robots.txt; never a junk or partial board."""
import json

import httpx
import pytest

from etl.extract.ladder import extract
from etl.sources.careerpage import CareerPageSource
from etl.sources.careerpage.source import MAX_JOB_PAGES, job_links, posting_key

from etl.tests.sources.ats.conftest import json_response, make_client

BOARD = "https://jobs.acme.example/careers"


def job_ld(title, ident=None, url=None, **extra):
    ld = {"@context": "https://schema.org", "@type": "JobPosting", "title": title,
          "description": "<p>Build things with SQL.</p>", "datePosted": "2026-10-01",
          "hiringOrganization": {"@type": "Organization", "name": "Acme"}, **extra}
    if ident:
        ld["identifier"] = {"@type": "PropertyValue", "value": ident}
    if url:
        ld["url"] = url
    return ld


def page(*postings, links=()):
    blocks = "".join(f'<script type="application/ld+json">{json.dumps(p)}</script>' for p in postings)
    anchors = "".join(f'<a href="{h}">x</a>' for h in links)
    return f"<html><head>{blocks}</head><body>{anchors}</body></html>"


def site(pages: dict, robots: str | None = None, robots_status: int | None = None):
    """A fake site: path -> (html | int status). robots.txt text or status."""
    hits = []

    def handler(request: httpx.Request):
        path = request.url.path
        hits.append(path)
        if path == "/robots.txt":
            if robots_status:
                return json_response(b"", robots_status, "text/plain")
            if robots is None:
                return json_response(b"", 404, "text/plain")
            return json_response(robots.encode(), 200, "text/plain")
        value = pages.get(path)
        if value is None:
            return json_response(b"not found", 404, "text/html")
        if isinstance(value, int):
            return json_response(b"", value, "text/html")
        return json_response(value.encode(), 200, "text/html")

    return make_client(handler), hits


async def run(pages, robots=None, robots_status=None, url=BOARD, slug="acme"):
    http, hits = site(pages, robots, robots_status)
    src = CareerPageSource()
    try:
        from etl.core.types import FetchTask
        res = await src.fetch(FetchTask(source="careerpage", key=slug, url=url, board_id="1"), http)
    finally:
        await http.aclose()
    return res, hits


# ---- JSON-LD on the page itself ----------------------------------------------------------------
async def test_postings_on_the_listing_page_become_one_document_each():
    html = page(job_ld("Data Analyst Intern", ident="A-1"), job_ld("Support Engineer", ident="A-2", url="https://jobs.acme.example/careers/support"))
    res, hits = await run({"/careers": html})
    assert res.status == "ok" and len(res.documents) == 2
    assert res.posting_ids == frozenset(d.source_key.split("/", 1)[1] for d in res.documents)
    assert {d.source for d in res.documents} == {"careerpage"} and {d.fetch_meta["slug"] for d in res.documents} == {"acme"}
    assert hits == ["/robots.txt", "/careers"]  # robots first, then exactly one page
    urls = {d.url for d in res.documents}
    assert "https://jobs.acme.example/careers/support" in urls and BOARD in urls


async def test_the_stored_document_goes_through_the_extractor_to_real_fields():
    ld = job_ld("Data Analyst Intern (Remote - India)", ident="A-1", jobLocationType="TELECOMMUTE",
                baseSalary={"@type": "MonetaryAmount", "currency": "INR", "value": {"@type": "QuantitativeValue", "minValue": 300000, "maxValue": 400000, "unitText": "YEAR"}})
    res, _ = await run({"/careers": page(ld)})
    e = extract(res.documents[0])
    assert e.title == "Data Analyst Intern (Remote - India)"
    assert e.fields["remote_type"].value == "remote"
    assert e.fields["seniority"].value == "intern"
    assert e.fields["pay"].value["currency"] == "INR"


def test_posting_keys_are_stable_distinct_and_survive_missing_ids():
    a, b = job_ld("Same title"), job_ld("Same title", ident="X1")
    assert posting_key("acme", a, BOARD) == posting_key("acme", a, BOARD)
    assert posting_key("acme", a, BOARD) != posting_key("acme", b, BOARD)
    assert posting_key("acme", a, BOARD) != posting_key("other", a, BOARD)
    assert posting_key("acme", {}, BOARD).startswith("acme/")
    assert posting_key("acme", {"identifier": {"name": "N-9"}}, BOARD).startswith("acme/")


async def test_the_same_posting_listed_twice_is_stored_once():
    ld = job_ld("Analyst", ident="A-1")
    res, _ = await run({"/careers": page(ld, ld)})
    assert len(res.documents) == 1


# ---- following job links, one level -----------------------------------------------------------
async def test_a_listing_without_json_ld_is_followed_to_its_job_pages_only():
    listing = page(links=[
        "/careers/data-analyst", "/careers/data-analyst#apply", "/jobs/support-engineer",
        "/careers/saved-jobs", "/careers/login", "/careers",                      # skipped: account pages and the page itself
        "/about", "/blog/hiring-news",                                              # not job-like
        "/careers/logo.png", "https://other.example/careers/x-1",                  # an asset and another host
        "mailto:jobs@acme.example", "javascript:void(0)",
    ])
    res, hits = await run({
        "/careers": listing,
        "/careers/data-analyst": page(job_ld("Data Analyst", ident="1")),
        "/jobs/support-engineer": page(job_ld("Support Engineer", ident="2")),
    })
    assert res.status == "ok" and len(res.documents) == 2
    assert sorted(hits[2:]) == ["/careers/data-analyst", "/jobs/support-engineer"]  # nothing else was requested


def test_job_links_are_deduplicated_resolved_and_capped():
    html = "".join(f'<a href="/careers/role-{n}">r</a><a href="/careers/role-{n}/">dup</a>' for n in range(MAX_JOB_PAGES + 10))
    links = job_links(BOARD, html)
    assert len(links) == MAX_JOB_PAGES + 1 and len(set(l.rstrip("/") for l in links)) == len(links)
    assert links[0] == "https://jobs.acme.example/careers/role-0"
    assert job_links(BOARD, "<a href='careers/data-engineer'>x</a>") == ["https://jobs.acme.example/careers/data-engineer"]
    assert job_links(BOARD, "<<<not html") == []


async def test_more_job_pages_than_the_cap_is_degraded_never_half_read():
    listing = page(links=[f"/careers/role-{n}" for n in range(MAX_JOB_PAGES + 5)])
    res, hits = await run({"/careers": listing})
    assert res.status == "degraded" and res.posting_ids is None and res.documents == []
    assert hits == ["/robots.txt", "/careers"]  # it did not start reading job pages it could not finish


async def test_a_failed_job_page_makes_the_whole_poll_degraded():
    listing = page(links=["/careers/role-a", "/careers/role-b"])
    res, _ = await run({"/careers": listing, "/careers/role-a": page(job_ld("A", ident="1")), "/careers/role-b": 500})
    assert res.status == "degraded" and res.posting_ids is None and res.documents == []  # a partial list is never "complete"


# ---- nothing to read -----------------------------------------------------------------------------
@pytest.mark.parametrize("html", [
    "<html><body><div id='root'></div><script src='app.js'></script></body></html>",   # a JavaScript app
    "<html><body><p>We are not hiring. Email jobs@acme.example</p></body></html>",
    "",
])
async def test_a_page_with_no_posting_data_stores_nothing_and_closes_nothing(html):
    res, _ = await run({"/careers": html})
    assert res.status == "degraded" and res.documents == [] and res.posting_ids is None


async def test_job_pages_that_carry_no_posting_data_are_also_nothing():
    res, _ = await run({"/careers": page(links=["/careers/role-a"]), "/careers/role-a": "<html><body>Apply by email</body></html>"})
    assert res.status == "degraded" and res.documents == []


# ---- statuses and robots.txt ---------------------------------------------------------------------------
@pytest.mark.parametrize("status,want", [(404, "dead"), (403, "blocked"), (429, "blocked"), (500, "degraded")])
async def test_board_page_statuses(status, want):
    res, _ = await run({"/careers": status})
    assert res.status == want and res.posting_ids is None


async def test_robots_disallow_means_the_page_is_never_fetched():
    res, hits = await run({"/careers": page(job_ld("A", ident="1"))}, robots="User-agent: *\nDisallow: /careers")
    assert res.status == "blocked" and "/careers" not in hits


async def test_robots_allow_missing_and_forbidden_files():
    ok = page(job_ld("A", ident="1"))
    assert (await run({"/careers": ok}, robots="User-agent: *\nDisallow: /private"))[0].status == "ok"
    assert (await run({"/careers": ok}, robots=None))[0].status == "ok"                   # 404 robots.txt: nothing forbids
    res, hits = await run({"/careers": ok}, robots_status=403)                            # a robots.txt closed to us: stay out
    assert res.status == "blocked" and "/careers" not in hits


async def test_a_job_page_robots_forbids_is_skipped_but_the_others_are_read():
    listing = page(links=["/careers/open-role", "/careers/private/role"])
    res, hits = await run(
        {"/careers": listing, "/careers/open-role": page(job_ld("Open", ident="1")), "/careers/private/role": page(job_ld("Hidden", ident="2"))},
        robots="User-agent: *\nDisallow: /careers/private",
    )
    assert res.status == "ok" and len(res.documents) == 1 and "/careers/private/role" not in hits


async def test_robots_txt_is_read_once_per_host_per_run():
    http, hits = site({"/careers": page(job_ld("A", ident="1"))})
    from etl.core.types import FetchTask
    src = CareerPageSource()
    try:
        for slug in ("a", "b"):
            await src.fetch(FetchTask(source="careerpage", key=slug, url=BOARD, board_id="1"), http)
    finally:
        await http.aclose()
    assert hits.count("/robots.txt") == 1


# ---- planning (database) -----------------------------------------------------------------------
pg = pytest.mark.pg


@pytest.fixture(scope="module")
def engine(pg_url):
    from etl.core.db import make_engine
    eng = make_engine(pg_url, pooled=True)
    yield eng
    eng.dispose()


@pg
def test_plan_returns_due_career_page_boards_with_their_own_url(engine):
    from sqlalchemy import text
    from etl.core.db import session_scope
    from etl.runner.source import Shard

    with session_scope(engine) as conn:
        conn.execute(text("TRUNCATE hunterrr.postings, hunterrr.raw_documents, hunterrr.boards, hunterrr.companies RESTART IDENTITY CASCADE"))
        cid = conn.execute(text("INSERT INTO hunterrr.companies (name, normalized_name) VALUES ('GemPages', 'gempages') RETURNING id")).scalar()
        for slug, ats, url in (("gempages", "other", "https://gempages.com/careers"), ("acme", "greenhouse", "https://boards.greenhouse.io/acme"),
                               ("bad", "other", "not-a-url")):
            conn.execute(text("INSERT INTO hunterrr.boards (company_id, ats, slug, url) VALUES (:c, CAST(:a AS hunterrr.ats), :s, :u)"),
                         {"c": cid, "a": ats, "s": slug, "u": url})
    with session_scope(engine) as conn:
        tasks = CareerPageSource().plan(conn, Shard(0, 1))
    assert [(t.source, t.key, t.url) for t in tasks] == [("careerpage", "gempages", "https://gempages.com/careers")]  # not greenhouse, not a bad url
    with session_scope(engine) as conn:
        conn.execute(text("UPDATE hunterrr.boards SET last_polled_at = now() WHERE slug = 'gempages'"))
    with session_scope(engine) as conn:
        assert CareerPageSource().plan(conn, Shard(0, 1)) == []  # polled just now: not due yet
