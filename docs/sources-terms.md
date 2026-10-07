# Source terms: what each source asks of us

Status of this review: written on 2026-10-07 from the terms as they were noted during the v3 research and from the
way each source's API is documented. **Before a public (non-invite) launch the owner should re-read each source's
current terms once**, because terms change and this file cannot see them.

| Source | What we use | What it asks | What we do | Public launch? |
|---|---|---|---|---|
| Greenhouse job boards | `boards-api.greenhouse.io` public board API | The API exists so companies' jobs can be listed elsewhere; no key | Poll politely, link every job to the company's own apply page | Yes |
| Lever postings | `api.lever.co/v0/postings/<company>` | Public postings API, no key | Same | Yes |
| Ashby job boards | `api.ashbyhq.com/posting-api/job-board/<company>` | Public posting API, no key | Same | Yes |
| SmartRecruiters, Workable, Recruitee | Their public posting endpoints | Public, no key | Same | Yes |
| Himalayas | `himalayas.app/jobs/api/search`, one saved query | Show where the job came from; do not resubmit its jobs to other job platforms | Every card says "via Himalayas" and the apply link goes to the Himalayas page. We never post its jobs anywhere else. Requests are paced at one per 2 s and stop on 429 | Yes, keeping the attribution and not reposting |
| Remotive | Not used | Its terms forbid republishing its jobs to collect sign-ups | Not collected | No |
| Remote OK | Not used (an earlier version did) | Requires a followed attribution link | Not collected | n/a |
| Career pages without an API | A small number of companies, read as plain pages | Respect `robots.txt` and the page's own terms | Polite pacing; skipped when disallowed | Per company |

Rules we follow for every source: no login-only sources, no scraping of personal accounts, one polite request at a
time per host, stop when asked to slow down, and always link the person to the employer's or the source's own page
to apply (we never apply for anyone).

## Public vs invite-only

Today the app is invite-only (friends and the owner). Nothing above blocks a public launch if the attribution stays
and Himalayas jobs are never reposted. A public launch should also add a visible link back to each source in the
footer and a contact address for removal requests.
