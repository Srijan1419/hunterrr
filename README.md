# hunterrr

**Remote Job Market Intelligence Dashboard** — a scheduled ETL pipeline over public
no-login job feeds and a public analytics dashboard over what it finds.

> **Stub.** This is the scaffold README written in task `f1-01`. It carries only the
> project name, the one-line description, and a placeholder table of contents. The real
> README — architecture diagram, setup instructions, a "why this project" paragraph
> written for a recruiter, the source-attribution section, and an honest limitations
> section — is written in Phase 3, once there is something real to document. Do not read
> anything below as a description of finished work.

## Table of contents (placeholder)

Each entry is a work item, not a section of this file. Links land as the sections exist.

| # | Task | What it adds | Status |
|---|---|---|---|
| 1 | `f1-01` | This repo layout: `etl/` + `web/`, license, notice, gitignore, gates | done |
| 2 | `f1-02` | The data contract: canonical schema, per-source field mappings, the four-step normalization ladder, controlled vocabularies, captured JSON fixtures | not started |
| 3 | `f1-03` | dlt pipeline skeleton, pinned `>=1.27.2`, writing to a local SQLite file | not started |
| 4 | `f1-04` | Remote OK source module | not started |
| 5 | `f1-05` | Jobicy source module | not started |
| 6 | `f1-06` | Himalayas source module | not started |
| 6b | `f1-06b` | Generic Greenhouse/Lever/Ashby ATS connector | not started |
| 7 | `f1-07` | Normalizer: the ladder, rules before LLM, `unknown` always legal | not started |
| 8 | `f1-08` | LLM skill extractor: provider abstraction, strict JSON, `content_hash` cache | not started |
| 9 | `f1-09` | Aggregator: `skills_daily` and `source_coverage` | not started |
| 10 | `f1-10` | `sync_to_turso.py` — upsert derived tables to libSQL over HTTPS | not started |
| 11 | `f1-11` | GitHub Actions cron workflow | not started |
| 12 | `f1-12` | pytest suite, passing offline with no API key | not started |
| — | Phase 2 | The `web/` application: Next.js, Drizzle, Better Auth, routes, charts, Vitest suite | not yet sequenced |
| — | Phase 3 | Full README, deployment, CEO walkthrough | not yet sequenced |

## Layout

```
etl/     Python 3.12 pipeline. contract/ sources/ normalize/ llm/ aggregate/ pipeline/
web/     Next.js app. Created in the Phase 2 tasks, not yet.
scripts/ Project gates read by the build's verify step.
```

`etl/` and `web/` are two deployables sharing one database: the pipeline is the only
writer of derived data, and the web app is a pure read path. The design and its
rejected alternatives are in the project ADR, not here.

## Third-party and attribution

`NOTICE` records the dlt (Apache-2.0), Crawl4AI (Apache-2.0), and Better Auth (MIT)
references, and the Remote OK link-back condition that the deployed app must honour.
Read it before adding a dependency or a data source.

## Repo topology

This project is built as a subfolder of a larger internal repository so it can use that
repository's task and verification loop. Before deployment it is split into its own
public repository with `git subtree split --prefix=hunterrr`, which carries this
folder's history and none of the surrounding content. Anyone who clones the parent
repository before that split is looking at the wrong thing.
