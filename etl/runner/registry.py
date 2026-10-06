"""Registry of sources for the collect runner."""

from etl.sources.ats.ashby import AshbySource
from etl.sources.ats.greenhouse import GreenhouseSource
from etl.sources.ats.lever import LeverSource
from etl.sources.ats.recruitee import RecruiteeSource
from etl.sources.ats.smartrecruiters import SmartRecruitersSource
from etl.sources.ats.workable import WorkableSource
from etl.sources.careerpage import CareerPageSource


def get_sources() -> list:
    """Return the list of registered sources."""
    return [
        GreenhouseSource(), LeverSource(), AshbySource(),
        WorkableSource(), RecruiteeSource(), SmartRecruitersSource(),
        CareerPageSource(),
    ]
