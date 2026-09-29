"""The dlt pin. dlt 1.27.0 and 1.27.1 were yanked; this file fails if either is installed.

The bug those releases shipped is silent data loss — the incremental merge truncates the
destination table — so the check is worth the two tests it takes. Every test name in this
directory contains `test_version` and every test in the directory contains `test_version`
or `test_pipeline` on purpose: the gate's `-k` filter is
`"test_version or test_pipeline"`, so a test named anything else would never run under
`verify.ps1` and would be dead code that looks like coverage.
"""

import re
from pathlib import Path

import dlt

#: Releases pulled from PyPI for a data-loss bug in the incremental merge.
YANKED_VERSIONS = frozenset({"1.27.0", "1.27.1"})

#: The floor the task file makes non-negotiable.
REQUIRED_MINIMUM = (1, 27, 2)

REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"


def _version_tuple(version: str) -> tuple:
    """`(1, 27, 2)` from `"1.27.2"`. Not a packaging.version — the suite deliberately
    depends on nothing but dlt, SQLAlchemy and pytest."""
    return tuple(int(part) for part in re.findall(r"\d+", version)[:3])


def _dlt_requirements() -> list:
    """Every non-comment line in requirements.txt that installs dlt."""
    lines = []
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and re.match(r"^dlt\b", line, flags=re.IGNORECASE):
            lines.append(line)
    return lines


def test_version_installed_dlt_is_not_a_yanked_release():
    """The check the task file asks for by name."""
    assert dlt.__version__ not in YANKED_VERSIONS, (
        f"dlt {dlt.__version__} is a yanked release (incremental merge truncates the "
        f"destination table). Install dlt>={'.'.join(map(str, REQUIRED_MINIMUM))} or newer."
    )


def test_version_installed_dlt_satisfies_the_pinned_minimum():
    """A version outside the pin is a failed install even if it is not one of the two
    yanked releases — 1.26.0 is not a data-loss bug but it is not the reviewed pin."""
    assert _version_tuple(dlt.__version__) >= REQUIRED_MINIMUM, (
        f"dlt {dlt.__version__} is below the pinned minimum "
        f"{'.'.join(map(str, REQUIRED_MINIMUM))}"
    )


def test_version_requirements_file_pins_dlt_at_or_above_the_minimum():
    """The pin has to be in the file, not only in the installed environment: a fresh
    install reads requirements.txt and nothing else."""
    requirements = _dlt_requirements()
    assert requirements, f"no dlt requirement found in {REQUIREMENTS}"

    floor = ".".join(map(str, REQUIRED_MINIMUM))
    for requirement in requirements:
        assert floor in requirement, (
            f"{requirement!r} does not pin dlt>={floor}; the yanked releases must be "
            "unreachable by a fresh install"
        )
        for yanked in sorted(YANKED_VERSIONS):
            assert yanked not in requirement, (
                f"{requirement!r} names the yanked release {yanked}"
            )
