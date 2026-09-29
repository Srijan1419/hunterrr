"""One module per job source, each mapping its own fields onto the contract.

Tasks f1-04 (Remote OK), f1-05 (Jobicy), f1-06 (Himalayas), f1-06b (Greenhouse/Lever/Ashby).

`ats_connector.py` is the one source that is not one module per *company*. Greenhouse, Lever
and Ashby are three **providers**, and one adapter class each covers every company on that
provider: adding a company is a line in `SEED_SLUGS` and no code at all. Its seed list was
built by hitting all three public board APIs directly for twelve candidate companies and
keeping only the ones that answered a real HTTP 200 with postings — 36 checks, 10 kept. The
full matrix is committed at `etl/fixtures/ats_seed_manifest.json`.

Every module here follows the same two steps: **parse the source's own response, then land
it in `raw_jobs` through `pipeline.land_raw_jobs`.** Nothing in `sources/` normalizes —
country resolution, seniority and `role_type` are the four-step ladder in
`etl/contract/normalization.md`, which reads `raw_jobs.payload` and is owned by task f1-07.
"""
