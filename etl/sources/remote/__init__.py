"""Remote-job aggregators: sources whose postings come from many different companies (Himalayas first).

Each aggregator query is stored as one pseudo-board (ats `other`, slug `agg-<source>-<query>`); the company of each
posting is a field of the posting (`company_name`), resolved to a company row by the process step.
See `etl/extract/sources_remote.py`.
"""

#: Sources that are aggregators (their documents carry a `company_name`; their boards have ats `other`).
AGGREGATOR_SOURCES: tuple[str, ...] = ("himalayas",)
