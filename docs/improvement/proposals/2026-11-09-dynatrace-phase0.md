# Proposal: Dynatrace source, phase 0

Branch `improve/dynatrace-phase0` (2 commits on main at 9f9eb35). Status: waiting for the owner.

## What it does

- Reads workflows and SLOs from a Dynatrace Platform tenant (`extract-dynatrace`, read-only,
  GET only, host-checked, token never printed) and converts them (`convert --source dynatrace`).
- A scheduled workflow that runs one DQL log-count query and notifies when the count passes a
  threshold becomes a New Relic log-count condition. Any other DQL, trigger or task layout is
  manual with steps.
- A Davis problem notification workflow is manual (a New Relic destination, channel and workflow
  must be built). An SLO is manual (a New Relic service level must be built).
- The New Relic window follows the query's `from:` range, not the schedule interval.

## Result

| | Value |
|---|---|
| Dynatrace fixtures converted | 2 of 8 (25%): the one real scheduled workflow and one hand-made variant |
| AWS and GCP fixtures | unchanged (27 of 32, 2 of 4); no regression |
| Tests | 725 passed |
| Live check | `extract-dynatrace` run against the owner's tenant: 0 workflows, 1 SLO (a test object), paging shapes confirmed, convert and report ran end to end |

## Review

An independent reviewer found 13 issues in round 1 (infinite thresholds breaking Terraform and
the report, a crash on a huge SLO target, duplicate resource addresses, truncated caveats, a
token that could appear in error text, a report command that did not work as documented, and
others). All were fixed in round 2 and the gate was run again by the maintainer session.
Open for the owner: the SLO and service-level wording in the remediation steps (written without
New Relic documentation open), and whether a workflow the extractor cannot complete should be
dropped with a warning (current) or fail the run.

## Risks and gaps

- Only two real workflows and one real SLO were ever seen. The list-response shape for a tenant
  with many workflows, and the guess that a list entry without `tasks` is a summary completed by
  `GET .../workflows/{id}`, are unverified.
- The `loglevel` to `level` mapping for forwarded logs is not confirmed; the notes say so.
- No Davis anomaly detector shape is covered: the tenant has none and the feature is not a
  settings schema there.
- The gcp extractor does not have the new safeguards (3xx as error, token scrubbing, size cap).
  Hardening it is a separate change.

## Needs the owner

1. Merge decision for this branch and for `improve/gcp-absence`.
2. Approval to regenerate `docs/improvement/baseline.json` (adds dynatrace; gcp manual 2 to 1
   after the absence merge).
3. Delete the leftover test SLO: `python3 scripts/live-test/dynatrace/test_objects.py delete`.
4. A Davis anomaly detector created in the Dynatrace UI, so its real shape can be read.
