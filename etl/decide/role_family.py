"""Role family from the job title (pure). Used for matching and for "roles near you", never to hide a job.

`role_family(title) -> (family, matched_text)`. The first rule that matches wins, so order matters: specific
wording ("data entry", "program manager", "customer success", "sales engineer") sits above the broad words
("data", "manager", "engineer"). A title no rule recognises is `other`.

The rules were tuned on ~6,600 real postings from 65 company boards (2026-10-06): titles such as "Customer
Success Manager, Revenue Suite", "Lead, Revenue Accounting" and "Partner Manager" are in the tests.
"""
from __future__ import annotations

import re

FAMILIES = (
    "software-engineering", "data", "qa-testing", "devops-cloud", "design", "product", "customer-support",
    "sales-bd", "marketing-content", "writing-editing", "community", "operations", "hr-recruiting",
    "finance-accounting", "other",
)
_I = re.IGNORECASE


def _r(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, _I)


_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("operations", _r(r"\bdata\s+entry\b|\bvirtual\s+assistant\b|\bback[- ]?office\b|\bexecutive\s+assistant\b|\bpersonal\s+assistant\b"
                      r"|\b(?:technical\s+)?(?:program|project)\s+manager\b|\bworkplace\b|\bfacilit(?:y|ies)\b|\boffice\s+manager\b")),
    ("qa-testing", _r(r"\bqa\b|\bquality\s+(?:assurance|engineer|analyst)\b|\bsdet\b|\btest(?:er|ing)?\b(?!\s*(?:prep|preparation))|\bautomation\s+(?:engineer|tester)\b")),
    ("devops-cloud", _r(r"\bdevops\b|\bsre\b|\bsite\s+reliability\b|\bcloud\b|\binfrastructure\b|\bplatform\s+engineer\b|\bsys(?:tem)?\s*admin\w*\b|\bnetwork\s+engineer\b|\bsecurity\b|\bcyber\w*\b|\binfosec\b|\bsoc\s+analyst\b|\bidentity\b")),
    ("customer-support", _r(r"\bcustomer\s+(?:support|success|service|care|experience|operations)\b|\bsupport\s+(?:engineer|specialist|agent|associate|representative|executive)\b|\bhelp\s*desk\b|\btechnical\s+support\b|\bchat\s+support\b|\b(?:voice|non[- ]voice)\b|\bbpo\b|\bcall\s+cent(?:er|re)\b|\bclient\s+support\b|\btier\s*[123]\b|\bonboarding\b|\bsupport\b")),
    ("sales-bd", _r(r"\bsales\b|\bsolutions?\s+(?:engineer|consultant)\b|\bpre[- ]?sales\b|\bbusiness\s+development\b|\bbdr\b|\bsdr\b|\bbde\b|\baccount\s+(?:executive|manager|director)\b|\blead\s+generation\b|\binside\s+sales\b|\bpartnerships?\b|\bpartner\s+(?:manager|specialist|development|success|account)\b|\bchannel\b|\balliances?\b|\brenewals?\b|\bgtm\b|\bdeal\s+desk\b|\bcommercial\b")),
    ("design", _r(r"\bdesigner\b|\bux\b|\bui\b|\bproduct\s+design\w*\b|\bgraphic\w*\b|\bvisual\b|\billustrat\w+\b|\bmotion\b|\bcreative\s+(?:designer|director)\b|\bbrand\s+design\w*\b|\bdesign\b")),
    ("data", _r(r"\bdata\s+(?:scien\w+|analy\w+|engineer\w*|analytics)\b|\bmachine\s+learning\b|\bml\b|\bai\s+(?:engineer|researcher|specialist)\b|\bapplied\s+(?:ai|scientist|researcher)\b|\bbusiness\s+intelligence\b|\bbi\s+(?:developer|analyst)\b|\banalytics\b|\bresearch(?:er)?\s*scientist\b|\bresearcher\b|\bscientist\b|\bnlp\b|\bcomputer\s+vision\b|\bdata\b|\bstatistic\w*\b")),
    ("product", _r(r"\bproduct\s+(?:manager|owner|analyst|operations|specialist|management)\b|\bassociate\s+product\b|\bapm\b")),
    ("writing-editing", _r(r"\bwriter\b|\bcopywrit\w+\b|\beditor\b|\bjournalist\b|\btranslat\w+\b|\btranscri\w+\b|\bproofread\w*\b|\bsubtitl\w+\b|\bcontent\s+(?:writer|editor)\b")),
    ("community", _r(r"\bcommunity\b|\bmoderator\b|\bdeveloper\s+(?:advocate|relations)\b|\bdevrel\b|\bambassador\b")),
    ("hr-recruiting", _r(r"\brecruit\w*\b|\btalent\b|\bhr\b|\bhuman\s+resources\b|\bpeople\s+(?:operations|partner|ops|team)\b|\bsourcer\b|\bbenefits\b|\btotal\s+rewards\b|\bcompensation\b")),
    ("finance-accounting", _r(r"\baccountant\b|\baccounts?\b|\baccounting\b|\bfinanc\w+\b|\bbookkeep\w+\b|\baudit\w*\b|\btax\b|\bpayroll\b|\bbilling\b|\bcollections\b|\bfp&a\b|\bcontroller\b|\binvoic\w+\b|\btreasury\b")),
    ("marketing-content", _r(r"\bmarketing\b|\bseo\b|\bsem\b|\bsocial\s+media\b|\bcontent\b|\bgrowth\b|\bbrand\b|\bperformance\s+marketing\b|\binfluencer\b|\bcampaign\w*\b|\bpr\b|\bpublic\s+relations\b|\bcommunications?\b|\bdemand\s+gen\w*\b|\bmedia\b|\bevents?\b")),
    ("operations", _r(r"\boperations?\b|\bops\b|\badmin\w*\b|\bcoordinator\b|\blogistics\b|\bsupply\s+chain\b|\bprocurement\b|\boffice\b|\breceptionist\b|\bscheduler\b|\bdispatcher\b|\bprocess\s+associate\b|\bstrategy\b|\benablement\b")),
    ("software-engineering", _r(r"\bsolutions?\s+architect\b|\bengineer\w*\b|\bdeveloper\b|\bprogrammer\b|\bsoftware\b|\bsde\b|\bswe\b|\bfront[- ]?end\b|\bback[- ]?end\b|\bfull[- ]?stack\b|\bweb\s+dev\w*\b|\bmobile\b|\bandroid\b|\bios\b|\breact\b|\bnode\b|\bjava\b|\.net\b|\bphp\b|\bgolang\b|\brust\b|\bpython\b|\bruby\b|\btypescript\b|\bjavascript\b|\bembedded\b|\bfirmware\b|\bblockchain\b|\bsolidity\b|\bgame\s+dev\w*\b|\bunity\b|\barchitect\b")),
)


def role_family(title: str) -> tuple[str, str]:
    t = title or ""
    for family, pattern in _RULES:
        m = pattern.search(t)
        if m:
            return family, " ".join(m.group(0).split())
    return "other", ""


__all__ = ["role_family", "FAMILIES"]
