"""Skills dictionary and extraction."""
import pytest

from etl.extract.rules.skills import SkillsConfigError, extract_skills, load_skills, parse_skills


def test_the_shipped_dictionary_is_valid_and_has_tech_and_non_tech_skills():
    skills = load_skills()
    families = {s.family for s in skills}
    assert len(skills) >= 150
    assert {"software-engineering", "data", "customer-support", "sales-bd", "marketing-content", "finance-accounting", "general"} <= families


@pytest.mark.parametrize("data,needle", [
    ({"skills": [{"id": "a", "label": "A", "family": "x"}, {"id": "a", "label": "B", "family": "x"}]}, "duplicate skill id"),
    ({"skills": [{"id": "a", "label": "Foo", "family": "x"}, {"id": "b", "label": "B", "family": "x", "aliases": ["foo"]}]}, "claimed by both"),
    ({"skills": [{"id": "Bad Id", "label": "A", "family": "x"}]}, "bad skill id"),
    ({"skills": [{"id": "a", "label": "", "family": "x"}]}, "label and a family"),
    ({}, "non-empty"),
])
def test_bad_dictionaries_are_refused(data, needle):
    with pytest.raises(SkillsConfigError, match=needle):
        parse_skills(data)


def ids(found, importance=None):
    return {f.skill for f in found if importance is None or f.importance == importance}


def test_whole_word_matching_with_aliases():
    got = ids(extract_skills("Engineer", "We use JavaScript, React.js and Node.js. Postgres too. Not Javascripty."))
    assert {"javascript", "react", "nodejs", "postgresql"} <= got
    assert "java" not in got                       # "Java" inside "JavaScript" is not Java
    assert "c-lang" not in ids(extract_skills("", "Experience with C++ and C#"))
    assert {"cpp", "csharp"} <= ids(extract_skills("", "Experience with C++ and C#"))


def test_longest_name_wins_and_titles_count_as_must():
    got = extract_skills("React Native Developer", "")
    assert ids(got) == {"react-native"} and got[0].importance == "must"


def test_nice_to_have_sections_and_sentences():
    text = (
        "## Requirements\n- Strong Python and SQL\n- Excel for reporting\n\n"
        "## Nice to have\n- Airflow, Docker\n\n"
        "## Benefits\n- Great team\n"
        "Familiarity with Tableau is a plus."
    )
    found = extract_skills("Data Analyst", text)
    assert ids(found, "must") >= {"python", "sql", "excel"}
    assert ids(found, "nice") >= {"airflow", "docker", "tableau"}
    assert "airflow" not in ids(found, "must")


def test_a_skill_named_both_ways_is_must():
    found = extract_skills("", "Requirements:\nPython\n\nNice to have:\nPython")
    assert [(f.skill, f.importance) for f in found] == [("python", "must")]


def test_non_technical_postings_get_skills_too():
    text = "Handle customer queries over live chat and email support using Zendesk. Excellent English communication. Tally knowledge preferred."
    got = extract_skills("Customer Support Associate", text)
    assert {"customer-support", "zendesk", "chat-support", "communication"} <= ids(got)
    assert "tally" in ids(got, "nice")


def test_empty_and_odd_inputs_never_raise():
    assert extract_skills("", "") == [] and extract_skills(None, None) == []  # type: ignore[arg-type]
