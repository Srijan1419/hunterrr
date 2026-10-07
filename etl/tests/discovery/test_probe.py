"""discover: slug guesses, board summaries, and the polite probe on mocked board APIs."""
from __future__ import annotations

import asyncio

import httpx

from etl.core.http import HttpClient
from etl.discovery.probe import Board, append_to_config, discover, known_boards, slug_candidates, summarize


def test_slug_candidates_try_the_plain_and_the_stripped_name():
    assert slug_candidates("Razorpay") == ["razorpay"]
    got = slug_candidates("Acme Technologies Private Limited")
    assert got[0] == "acme" and "acmetechnologiesprivatelimited" in got and len(got) <= 6
    assert slug_candidates("Foo Bar")[:2] == ["foobar", "foo-bar"]


def test_summaries_count_india_remote_and_early_career_titles():
    gh = {"jobs": [{"title": "Junior Engineer", "location": {"name": "Bengaluru, India"}},
                   {"title": "Staff Engineer", "location": {"name": "Remote - US"}},
                   {"title": "Support Associate", "location": {"name": "Remote, India"}}]}
    b = summarize("Acme", "greenhouse", "acme", gh)
    assert (b.jobs, b.india, b.remote, b.early) == (3, 2, 2, 2)
    lever = [{"text": "Intern", "categories": {"location": "Pune", "allLocations": ["Pune"]}, "workplaceType": "remote"}]
    assert summarize("X", "lever", "x", lever).remote == 1
    ashby = {"jobs": [{"title": "Analyst", "location": "Delhi", "isRemote": True, "secondaryLocations": []}]}
    assert (summarize("Y", "ashby", "y", ashby).india, summarize("Y", "ashby", "y", ashby).remote) == (1, 1)
    assert summarize("Z", "greenhouse", "z", {"jobs": []}) is None
    assert summarize("Z", "lever", "z", {"not": "a list"}) is None


def test_discover_returns_the_first_answering_board_per_ats_and_skips_known_ones():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "boards-api.greenhouse.io" and "/acme/" in request.url.path:
            return httpx.Response(200, json={"jobs": [{"title": "Junior Analyst", "location": {"name": "Remote, India"}}]})
        if request.url.host == "api.lever.co" and "/acme" in request.url.path:
            return httpx.Response(200, json=[{"text": "Engineer", "categories": {"location": "Berlin"}}])
        return httpx.Response(404)

    client = HttpClient(transport=httpx.MockTransport(handler), rate_per_host=1000.0, burst_per_host=1000.0)
    found = asyncio.run(discover(["Acme"], client=client))
    assert [(b.ats, b.slug) for b in found] == [("greenhouse", "acme"), ("lever", "acme")]
    client2 = HttpClient(transport=httpx.MockTransport(handler), rate_per_host=1000.0, burst_per_host=1000.0)
    again = asyncio.run(discover(["Acme"], client=client2, known={("greenhouse", "acme")}))
    assert [b.ats for b in again] == ["lever"]


def test_append_to_config_keeps_the_file_loadable(tmp_path):
    cfg = tmp_path / "companies.yaml"
    cfg.write_text("companies:\n  - {name: Old, ats: greenhouse, slug: old}\n", encoding="utf-8")
    n = append_to_config(cfg, [Board("New Co", "ashby", "newco", 5, 2, 3, 1)])
    assert n == 1 and known_boards(cfg) == {("greenhouse", "old"), ("ashby", "newco")}
