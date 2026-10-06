"""The accuracy gate: the real extraction + decision chain scored against the hand-labelled gold set.

If a rule change makes the data less accurate on real postings, THIS test fails the build. The thresholds are in
`etl/eval/gold.py` (precision first). When real data shows a new failure, add the posting to the gold set (a label in
`etl/fixtures/gold/labels/`, the raw board JSON in `postings.jsonl`) before fixing the rule.
"""
import pytest

from etl.eval.gold import MIN_SAMPLES, THRESHOLDS, entry_level, evaluate, format_report, load_gold

VALID = {
    "remote": {"remote", "hybrid", "onsite", "unknown"},
    "india": {"yes", "no", "unknown", "na"},
    "employment": {"full_time", "contract", "part_time", "internship", "volunteer", "temporary", "unknown"},
    "entry": {"yes", "no", "unknown"},
}


@pytest.fixture(scope="module")
def items():
    return load_gold()


def test_the_gold_set_is_large_and_every_label_is_valid(items):
    assert len(items) >= 250
    assert len({g.gid for g in items}) == len(items)
    for g in items:
        for field, allowed in VALID.items():
            assert getattr(g, field) in allowed, (g.gid, field, getattr(g, field))
        assert g.why.strip(), f"{g.gid} has no justification"
        # `na` means "not a remote job": it must never sit on a job labelled remote
        assert (g.india == "na") == (g.remote != "remote"), (g.gid, g.remote, g.india)


def test_there_is_a_fresh_holdout_and_a_dev_set(items):
    splits = {g.split for g in items}
    assert splits == {"dev", "holdout"}
    assert sum(1 for g in items if g.split == "holdout") >= 50


def test_the_set_covers_the_cases_that_decide_what_an_indian_fresher_sees(items):
    assert sum(1 for g in items if g.remote == "remote" and g.india == "yes") >= 30
    assert sum(1 for g in items if g.remote == "remote" and g.india == "no") >= 40
    assert sum(1 for g in items if g.employment == "internship") >= 10
    assert sum(1 for g in items if "language_required" in g.flags) >= 8
    assert sum(1 for g in items if "not_a_job" in g.flags) >= 5
    assert {"greenhouse", "lever", "ashby"} <= {g.ats for g in items}


def test_the_chain_meets_the_accuracy_thresholds(items):
    report = evaluate(items)
    assert not report.failures, "\n" + format_report(report)


def test_thresholds_are_set_for_the_metrics_that_matter():
    assert {"feed", "remote", "india", "hidden_kind"} <= set(THRESHOLDS)
    assert THRESHOLDS["feed"] >= 0.95 and MIN_SAMPLES >= 3


@pytest.mark.parametrize("row,kind,expected", [
    ({"seniority": "entry", "experience_min_years": None}, "unknown", "yes"),
    ({"seniority": "entry", "experience_min_years": 3}, "unknown", "no"),
    ({"seniority": None, "experience_min_years": 2}, "unknown", "yes"),
    ({"seniority": None, "experience_min_years": 4}, "unknown", "no"),
    ({"seniority": "senior", "experience_min_years": 0}, "unknown", "no"),
    ({"seniority": None}, "unknown", "unknown"),
    ({"seniority": "intern"}, "internship", "no"),
])
def test_entry_level_rule_mirrors_the_feed(row, kind, expected):
    assert entry_level(row, kind) == expected
