# Provenance of the `liveoutage` / `staleincident` fixtures

These two scenarios are built from a **real** outage captured on 2026-07-31,
not from a synthetic 5xx. A synthetic one proves the code path; only a real one
proves the pairing this feature exists for - an API error that is not the
caller's fault, alongside a published incident that says so.

## What is verbatim, and what is not

| File | Provenance |
| --- | --- |
| `api-501-response.json` | **Verbatim.** The error body returned by a Google API during the outage: `code: 501`, `status: UNIMPLEMENTED`. Classified directly by a test, so the real bytes are what the classifier is measured against. |
| `incidents.json` | **Verbatim** (wrapped in the array the feed serves). Google's own published incident record, captured while `end` was still `null`. |
| `run_services_list.err` | **Reconstructed.** The status code and message text are verbatim from the capture; the `ERROR: (gcloud.run.services.list) HTTPError 501: ...` wrapper is gcloud's standard framing. The capture came from a direct REST call, so no gcloud-framed copy of it exists. This is stated rather than passed off as captured. |
| `staleincident/incidents.json` | The same record with `end` set. Nothing else differs, so a test that still reports it open can only be misreading `end`. |

## Why the negative case is a separate scenario

`liveoutage` proves the hint *finds* an open incident. `staleincident` proves it
does not *invent* one: the same 5xx, the same incident, the only difference
being that the incident has closed. A hint that reports an incident in both
cases is not reading the feed, it is decorating the error.

## Honest limits

- One service (Hosting) and one error shape. The feed's field set is observed
  from this record, not from documentation - treat other services' records as
  unverified until seen. Note this record spells `most_recent_update` with an
  underscore, while the Google Cloud feed this tool's default URL points at uses
  `most-recent-update` with hyphens. Nothing here reads that field, but it is
  evidence the schema is not uniform across Google's status feeds.
- The tool does **not** match an incident to the failing command's service. It
  reports every open incident and names the service each belongs to, leaving
  relevance to the reader. Claiming a match this code does not perform would be
  the same class of overreach the feature exists to prevent.
- Nothing here identifies a project, site, host or account: the API body carries
  no identifiers at all, and the incident record is Google's own published data.
