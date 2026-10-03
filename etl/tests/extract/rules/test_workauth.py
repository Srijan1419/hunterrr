import pytest

from etl.extract.rules.context import ParseContext
from etl.extract.rules.workauth import parse_workauth

CTX = ParseContext()

# (text, visa_sponsorship, work_auth_required)
CASES = [
    ("visa sponsorship available", "yes", None),
    ("Visa sponsorship is provided for the right candidate", "yes", None),
    ("we sponsor H-1B", "yes", None),
    ("We can sponsor work visas", "yes", None),
    ("no visa sponsorship", "no", None),
    ("No sponsorship offered", "no", None),
    ("unable to sponsor", "no", None),
    ("We are unable to sponsor visas at this time", "no", None),
    ("will not sponsor", "no", None),
    ("without sponsorship", "no", None),
    ("sponsorship not required", None, None),
    ("Sponsorship is not required", None, None),
    ("We offer visa sponsorship but cannot sponsor contractors", None, None),
    ("must be authorized to work in the US", None, ["us_work_authorization"]),
    ("Candidates must be authorised to work in the United States", None, ["us_work_authorization"]),
    ("right to work in the UK", None, ["uk_right_to_work"]),
    ("active security clearance", None, ["security_clearance"]),
    ("TS/SCI", None, ["security_clearance"]),
    ("US citizens only", None, ["citizenship", "us_work_authorization"]),
    ("Authorized to work in the US. No visa sponsorship.", "no", ["us_work_authorization"]),
    ("", None, None),
    ("Great benefits and a friendly team", None, None),
    ("sponsor a child's education", None, None),
]


@pytest.mark.parametrize("text,visa,auth", CASES, ids=[c[0][:50] for c in CASES])
def test_workauth(text, visa, auth):
    v, a = parse_workauth(text, CTX)
    assert v.value == visa
    assert a.value == auth
    assert v.provenance == ("rule" if visa else "unknown")
    assert a.provenance == ("rule" if auth else "unknown")
