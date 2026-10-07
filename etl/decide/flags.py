"""Scam / unpaid signals (hard flags that hide a job) and soft labels (shown on the card, never hide).

`scan_flags(view) -> FlagResult(flags, labels, evidence)`

Hard flags (the feed hides a job that has any):
  fee_requested       asks the candidate to pay (registration / training / security-deposit fee ...)
  unpaid              unpaid, volunteer, exposure-only, equity-only
  commission_only     no fixed pay, commission only
  suspicious_contact  apply only through WhatsApp / Telegram on a posting that is not from a known job board
  too_good            "no interview", "guaranteed job", "earn 5,000 a day from home"

Soft labels: night_shift (must overlap US/EU hours), freelance, occasional_office.

Every pattern has a guard against the way honest postings use the same words: "no registration fee",
"we never charge any fee", "we cover the training costs", "unpaid leave", "no salary history". A guard
that is too weak hides good jobs; a pattern that is too broad shows scams. Both directions are tested.
Pure code: no database, no network, no AI, no clock.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

HARD_FLAGS = ("fee_requested", "unpaid", "commission_only", "suspicious_contact", "too_good", "not_a_job")
SOFT_LABELS = ("night_shift", "freelance", "occasional_office", "remote_unverified")

MAX_SCAN = 20_000
_I = re.IGNORECASE
#: Job-board systems whose postings come from companies we chose to follow.
KNOWN_BOARD_SOURCES = {"greenhouse", "lever", "ashby", "workable", "recruitee", "smartrecruiters", "careerpage"}

_NEGATION_BEFORE = re.compile(
    r"(?:\bno\b|\bnot\b|\bnever\b|\bzero\b|\bwithout\b|\bfree\s+of\b|\bnothing\b|\bdon't\b|\bdo\s+not\b|\bdoesn't\b|"
    r"\bdoes\s+not\b|\bwon't\b|\bwill\s+not\b|\bisn't\b|\baren't\b|\bnor\b|\bwe\s+(?:cover|pay|bear|reimburse|provide|sponsor|fund)\b|"
    r"\b(?:the\s+company|the\s+employer|employer|company)\s+(?:will\s+)?(?:cover|pay|bear|reimburse|provide|sponsor|fund)s?\b)"
    r"[^.;!?\n]{0,50}$",
    _I,
)
_NEGATION_AFTER = re.compile(
    r"^[^.;!?\n]{0,30}\b(?:is|are|will\s+be)\s+(?:not\s+(?:required|charged|collected|applicable|needed)|waived|free|covered|paid\s+by\s+(?:us|the\s+company))\b"
    r"|^[^.;!?\n]{0,15}\b(?:waived|free\s+of\s+(?:charge|cost))\b",
    _I,
)

# Fee words that, on their own, are a scam signal in a job posting.
_FEE_STRONG = re.compile(
    r"\b(?:registration|training|joining|security|refundable|onboarding|certification|enrol+ment|admission|"
    r"uniform|kit|laptop|id[- ]?card|placement|course)\s+(?:fees?|charges?|deposit|amount)\b"
    r"|\b(?:pay|deposit|transfer)\s+(?:rs\.?|₹|inr|usd|\$)\s*[\d,]+\s+(?:to|for|as)\s+(?:apply|join|register|start|secure|confirm|get)\b",
    _I,
)
# Words that are ordinary in payments / fintech / SaaS postings ("processing fees", "application fee waived",
# "pay a fee for membership"): they count only when the text asks THE CANDIDATE to pay (found on production:
# 60 Peloton postings and two payments roles were flagged by the broad version).
_FEE_WEAK = re.compile(
    r"\b(?:processing|application|verification|equipment|software|documentation|interview|background[- ]check)\s+(?:fees?|charges?|deposit|amount)\b"
    r"|\b(?:pay|deposit|transfer|send|remit)\s+(?:an?\s+)?(?:(?:refundable|small|one[- ]time|nominal|initial)\s+)?(?:fee|deposit|amount)\b",
    _I,
)
_CANDIDATE_PAYS = re.compile(
    r"\b(?:you\s+(?:will\s+|would\s+)?(?:have\s+to|need\s+to|must|should)\s+pay|candidates?\s+(?:must|need\s+to|have\s+to|will\s+have\s+to)\s+pay"
    r"|applicants?\s+(?:must|need\s+to|have\s+to)\s+pay|before\s+(?:joining|you\s+start|your\s+interview)|to\s+(?:apply|join|register)\b|upfront|up[- ]front"
    r"|non[- ]?refundable|payable\s+(?:at|before|to\s+us|upon)|pay\s+us)\b",
    _I,
)
_UNPAID = re.compile(
    r"\bunpaid\b|\bnon[- ]paid\b"
    r"|\bno\s+(?:salary|stipend|pay|compensation|remuneration)\b(?!\s+(?:history|histories|expectations?|information|details|gap|cuts?|disparity|ceiling|negotiation|data))"
    r"|\bwithout\s+(?:any\s+)?(?:pay|salary|compensation|remuneration)\b"
    r"|\bvolunteer\s+(?:position|basis)\b|\bthis\s+is\s+a\s+volunteer\b|\bon\s+a\s+volunteer\b"
    r"|\b(?:equity|shares?)[- ]only\b|\bpaid\s+in\s+(?:exposure|experience|equity\s+only)\b"
    r"|\b(?:compensation|payment|pay)\s+is\s+(?:experience|exposure|learning|equity)\b|\bin\s+exchange\s+for\s+(?:exposure|experience)\b",
    _I,
)
_UNPAID_BENEFIT = re.compile(r"\bunpaid\s+(?:leave|time\s+off|vacation|holidays?|pto|sabbatical|breaks?|parental|maternity|paternity|lunch)\b", _I)
_COMMISSION_ONLY = re.compile(
    r"\bcommission[- ]only\b|\b100\s*%\s*commission\b|\bonly\s+(?:on\s+)?commissions?\b|\bpurely\s+(?:on\s+)?commissions?\b"
    r"|\bstraight\s+commission\b|\bno\s+(?:fixed|base|basic)\s+(?:salary|pay)\b|\bnot\s+(?:a\s+)?salaried\b"
    r"|\bcommission[- ]based\s+(?:only|income|role|position)\b|\bearn\s+(?:only\s+)?(?:through|via|from)\s+commissions?\b",
    _I,
)
_CONTACT_VERB = re.compile(r"\b(?:apply|send|share|forward|contact|reach|message|text|resume|cv|hr|whatsapp\s+us)\b", _I)
_WHATSAPP = re.compile(r"\b(?:whats\s?app|telegram)\b", _I)
_TOO_GOOD = re.compile(
    r"\bno\s+interviews?\b|\bwithout\s+(?:any\s+)?interviews?\b|\bguaranteed\s+(?:job|placement|income|salary|joining)\b"
    r"|\b100\s*%\s*(?:job\s+)?(?:guarantee|placement)\b|\bpay\s+per\s+(?:task|click|like|view|order|video)\b"
    r"|\bearn\s+(?:up\s+to\s+)?(?:rs\.?|₹|inr|\$)?\s*[\d,]{4,}\s*(?:/|per|a)\s*(?:day|week)\b"
    r"|\bwork\s+from\s+home\b[^.\n]{0,60}\bearn\s+(?:rs\.?|₹|inr)?\s*[\d,]{5,}",
    _I,
)

# Postings that are not an open job: talent-community sign-ups, open / spontaneous applications, "future roles"
# pools, and conversion postings only existing temporary staff may use (all found on real boards, 2026-10-06).
_NOT_A_JOB_TITLE = re.compile(
    r"\btalent\s+(?:community|network|pool)\b|\bopen\s+(?:applications?|sollicitatie)\b|\b(?:general|spontaneous)\s+application\b"
    r"|\brefer\s+a\s+(?:candidate|friend|colleague)\b|\binitiativbewerbung\b|\bblindbewerbung\b|\bcandidature\s+spontan[ée]e\b"
    r"|\bcandidatura\s+espont[aá]nea\b|\bspontane\s+sollicitatie\b"
    r"|\bfuture\s+(?:opportunities|openings|roles|positions?|\w+\s+roles)\b|\bexpression\s+of\s+interest\b|\bapply\s+here\b|\bsollicitatie\b"
    r"|\bjoin\s+our\s+(?:talent|team)\s+(?:community|network|pool)\b|\btemp\s+to\s+(?:full[- ]?time|perm\w*)\b"
    r"|\b(?:campus|student|college)\s+ambassador\b",
    _I,
)
_NOT_A_JOB_TEXT = re.compile(
    r"\bonly\s+(?:\w+\s+){0,3}(?:may|can)\s+apply\b|\bconversion\s+program\b|\bnot\s+every\s+(?:\w+\s+)?role\s+is\s+open\b"
    r"|\bwe(?:'|’)re\s+always\s+interested\s+(?:in\s+meeting|to\s+meet)\b|\bno\s+(?:current|open)\s+(?:positions|openings|vacancies)\b"
    r"|\b(?:campus|student|college)\s+ambassador\b"
    # "even when there may not be an immediate opening ... submit your resume for future consideration"
    r"|\beven\s+(?:when|if)\s+there\s+(?:may\s+not\s+be|is\s+not|isn(?:'|’)t)\s+an?\s+(?:immediate|current|open)\s+(?:opening|position|vacancy|role)\b"
    r"|\bsubmit\s+your\s+(?:resume|cv)\s+for\s+future\s+(?:consideration|opportunities|openings)\b",
    _I,
)
# A college-batch registration post, not a job: "RVCE - EBA (January, 2027)" (institution - programme (month, year)).
_CAMPUS_BATCH_TITLE = re.compile(
    r"^\s*[A-Z]{2,8}\s*[-–]\s*[A-Z]{2,6}\s*\((?i:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s*20\d\d\)\s*$"
)

# A posting that says remote anywhere in its text; tells an aggregator's blanket "remote" from a stated one.
_SAYS_REMOTE = re.compile(r"\bremote(?:ly)?\b|\bwork(?:ing)?\s+(?:from\s+(?:home|anywhere)|virtually)\b|\bwfh\b|\bdistributed\s+team\b|\banywhere\b", _I)

_NIGHT_SHIFT = re.compile(
    r"\b(?:overlap|work|working|available|availability)\b[^.\n]{0,50}\b(?:us|u\.s\.|pacific|eastern|central|mountain|north\s+american?)\b[^.\n]{0,20}\b(?:hours?|time\s*zones?)\b"
    r"|\b(?:est|pst|cst|pdt|edt|cet|cest)\s+(?:hours|time\s*zone|working\s+hours|business\s+hours|shift)\b"
    r"|\bus\s+(?:time\s*zone|business\s+hours|shift|hours)\b|\bnight\s+shift\b|\boverlap\s+with\s+(?:the\s+)?(?:us|north\s+america|european)\b",
    _I,
)
_FREELANCE = re.compile(r"\bfreelanc\w*\b|\bcontractor\b|\bindependent\s+contractor\b", _I)
_OCCASIONAL_OFFICE = re.compile(
    r"\b(?:visit|come\s+(?:in|to)|travel\s+to|meet\s+(?:at|in))\s+(?:the\s+|our\s+)?office\b[^.\n]{0,30}\b(?:once|twice|occasionally|quarterly|monthly|every)\b"
    r"|\boccasional\s+(?:office|travel|visits?)\b|\bquarterly\s+(?:offsites?|meetups?|get[- ]?togethers?)\b",
    _I,
)


@dataclass
class FlagResult:
    flags: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)


def _quote(m: re.Match[str]) -> str:
    return " ".join(m.group(0).split())[:80]


def _first_unguarded(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    """The first match that no honest-wording guard explains ("no registration fee", "we cover the kit fee")."""
    for m in pattern.finditer(text):
        before = text[max(0, m.start() - 70):m.start()]
        if _NEGATION_BEFORE.search(before) or _NEGATION_AFTER.search(text[m.end():m.end() + 60]):
            continue
        return m
    return None


def scan_flags(view: Mapping[str, Any]) -> FlagResult:
    title = view.get("title") or ""
    description = (view.get("description_md") or "")[:MAX_SCAN]
    text = f"{title}\n{description}"
    cleaned = _UNPAID_BENEFIT.sub(" ", text)
    source = str(view.get("source") or "")
    out = FlagResult()

    def hard(name: str, m: re.Match[str] | None) -> None:
        if m is not None and name not in out.flags:
            out.flags.append(name)
            out.evidence[name] = _quote(m)

    fee = _first_unguarded(_FEE_STRONG, text)
    if fee is None:
        weak = _first_unguarded(_FEE_WEAK, text)
        if weak is not None and _CANDIDATE_PAYS.search(text[max(0, weak.start() - 120):weak.end() + 120]):
            fee = weak
    hard("fee_requested", fee)
    hard("unpaid", _first_unguarded(_UNPAID, cleaned))
    hard("commission_only", _first_unguarded(_COMMISSION_ONLY, text))
    hard("too_good", _first_unguarded(_TOO_GOOD, text))
    hard("not_a_job", _NOT_A_JOB_TITLE.search(title) or _CAMPUS_BATCH_TITLE.search(title) or _NOT_A_JOB_TEXT.search(description[:3000]))
    if source not in KNOWN_BOARD_SOURCES:
        for m in _WHATSAPP.finditer(text):
            near = text[max(0, m.start() - 80):m.end() + 80]
            if _CONTACT_VERB.search(near):
                hard("suspicious_contact", m)
                break

    for name, pattern in (("night_shift", _NIGHT_SHIFT), ("freelance", _FREELANCE), ("occasional_office", _OCCASIONAL_OFFICE)):
        if pattern.search(text if name != "freelance" else f"{title}\n{view.get('employment_type') or ''}\n{description[:600]}"):
            out.labels.append(name)
    # Himalayas lists remote jobs only, but some postings never say so themselves (an office employer's text): keep
    # the job and say on the card that only the aggregator claims it.
    if source == "himalayas" and view.get("remote_type") == "remote" and not _SAYS_REMOTE.search(text):
        out.labels.append("remote_unverified")
    return out


__all__ = ["scan_flags", "FlagResult", "HARD_FLAGS", "SOFT_LABELS", "KNOWN_BOARD_SOURCES"]
