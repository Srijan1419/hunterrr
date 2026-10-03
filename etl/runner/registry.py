"""Registry of sources for the collect runner."""

from etl.sources.ats.ashby import AshbySource
from etl.sources.ats.greenhouse import GreenhouseSource
from etl.sources.ats.lever import LeverSource


def get_sources() -> list:
    """Return the list of registered sources."""
    return [GreenhouseSource(), LeverSource(), AshbySource()]
