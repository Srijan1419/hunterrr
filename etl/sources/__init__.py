"""Job sources, one package per way of reading postings.

* `ats/`: public job-board APIs of applicant-tracking systems (Greenhouse, Lever, Ashby, Workable,
  Recruitee, SmartRecruiters). One class per system covers every company on it: adding a company is
  a line in `config/companies.yaml`, not code.
* `careerpage/`: a company's own careers page, read through its schema.org `JobPosting` data.

Every source implements the `Source` protocol in `etl/runner/source.py` (`plan` which boards are due,
`fetch` one board into raw documents). Sources never extract fields: the extraction ladder in
`etl/extract/` reads the stored raw documents.

New sources (remote job boards, Keka) are added in Loop 2 of `The Coding Agency/hunterrr-v2/ROADMAP-v3-basecamp.md`.
"""
