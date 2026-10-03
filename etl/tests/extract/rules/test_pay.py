from decimal import Decimal as D

import pytest

from etl.extract.rules.context import ParseContext
from etl.extract.rules.pay import parse_pay

CTX = ParseContext()
FX = ParseContext(fx={"USD": D("84")})

# (text, min, max, currency, period)
POSITIVE = [
    ("monthly base pay: $9,500", 9_500, 9_500, "USD", "month"),
    ("hourly rate $55.00", 55, 55, "USD", "hour"),
    ("₹12–18 LPA", 1_200_000, 1_800_000, "INR", "year"),
    ("12-18 LPA", 1_200_000, 1_800_000, "INR", "year"),
    ("INR 12 to 18 lakhs per annum", 1_200_000, 1_800_000, "INR", "year"),
    ("1.5 Cr", 15_000_000, 15_000_000, "INR", "year"),
    ("₹1.5 crore p.a.", 15_000_000, 15_000_000, "INR", "year"),
    ("CTC 24 LPA", 2_400_000, 2_400_000, "INR", "year"),
    ("₹80,000 - ₹1,20,000 per month", 80_000, 120_000, "INR", "month"),
    ("Rs. 50k/month", 50_000, 50_000, "INR", "month"),
    ("$70-90k", 70_000, 90_000, "USD", "year"),
    ("$70k - $90k", 70_000, 90_000, "USD", "year"),
    ("USD 70,000 - 90,000 per year", 70_000, 90_000, "USD", "year"),
    ("$60/hr", 60, 60, "USD", "hour"),
    ("$60 per hour", 60, 60, "USD", "hour"),
    ("€55,000", 55_000, 55_000, "EUR", "year"),
    ("£40k-£50k", 40_000, 50_000, "GBP", "year"),
    ("S$8,000 per month", 8_000, 8_000, "SGD", "month"),
    ("AED 15,000 monthly", 15_000, 15_000, "AED", "month"),
    ("$120,000 - $150,000 plus equity", 120_000, 150_000, "USD", "year"),
    ("$100-120k", 100_000, 120_000, "USD", "year"),
    ("up to $90k", None, 90_000, "USD", "year"),
    ("from $90k", 90_000, None, "USD", "year"),
    ("$90k+", 90_000, None, "USD", "year"),
    ("stipend ₹15,000/month", 15_000, 15_000, "INR", "month"),
    ("Salary: $150,000 - $180,000 per year", 150_000, 180_000, "USD", "year"),
    ("Compensation £60,000 - £75,000", 60_000, 75_000, "GBP", "year"),
    ("base salary of €70k–€85k", 70_000, 85_000, "EUR", "year"),
]

UNKNOWN = [
    "USA monthly base pay: $9,500 and USA hourly base pay: $55.00",  # two different figures
    "Compensation is $9,500",  # no period and small: could be monthly or yearly
    "Salary 7.4",  # no currency marker
    "We raised $50m in funding",
    "Revenue of $200M",
    "competitive", "competitive salary", "DOE", "depends on experience",
    "best in industry", "attractive package", "unpaid", "equity only",
    "market rate", "negotiable", "", "   ", "2020-2024", "Call 555-123-4567",
    "ZIP 94107", "5+ years", "401(k)", "3 days a week",
    "Series B, $50M raised", "We raised $200 million in funding",
    "valuation of $2B", "$500 per year", "Join 10 teams of 25 people",
    "Range A is $60,000-$80,000; range B is $150,000-$170,000.",
]


def _expected(v):
    return None if v is None else D(v)


@pytest.mark.parametrize("text,lo,hi,cur,period", POSITIVE, ids=[c[0] for c in POSITIVE])
def test_pay_positive(text, lo, hi, cur, period):
    f = parse_pay(text, CTX)
    assert f.provenance == "rule"
    v = f.value
    assert v["min"] == _expected(lo)
    assert v["max"] == _expected(hi)
    assert v["currency"] == cur
    assert v["period"] == period
    assert v["disclosed"] is True


@pytest.mark.parametrize("text", UNKNOWN, ids=[repr(t) for t in UNKNOWN])
def test_pay_unknown(text):
    f = parse_pay(text, CTX)
    assert f.value is None and f.provenance == "unknown"


def test_annual_inr_conversions():
    v = parse_pay("₹80,000 - ₹1,20,000 per month", CTX).value
    assert v["annual_inr_min"] == D(960_000) and v["annual_inr_max"] == D(1_440_000)
    v = parse_pay("₹12–18 LPA", CTX).value
    assert v["annual_inr_min"] == D(1_200_000) and v["annual_inr_max"] == D(1_800_000)
    v = parse_pay("Rs. 50k/month", CTX).value
    assert v["annual_inr_min"] == D(600_000)


def test_annual_inr_for_foreign_currency_needs_fx():
    v = parse_pay("$60/hr", CTX).value
    assert v["annual_inr_min"] is None and v["annual_inr_max"] is None
    v = parse_pay("$60/hr", FX).value
    assert v["annual_inr_min"] == D(60) * 2080 * 84
    v = parse_pay("$70-90k", FX).value
    assert v["annual_inr_min"] == D(70_000) * 84
    v = parse_pay("$2000 per day", FX).value
    assert v["annual_inr_min"] == D(2000) * 260 * 84


def test_ambiguous_ranges_are_unknown_but_a_labelled_one_wins():
    f = parse_pay("Range A is $60,000-$80,000; range B is $150,000-$170,000.", CTX)
    assert f.value is None
    f = parse_pay("Signing bonus $20,000-$30,000. Base salary $120,000-$140,000.", CTX)
    assert f.value["min"] == D(120_000)


def test_evidence_is_a_short_snippet():
    f = parse_pay("x " * 3000 + "Salary $120,000 - $150,000 per year " + "y " * 3000, CTX)
    assert f.value is not None and len(f.evidence) <= 80
