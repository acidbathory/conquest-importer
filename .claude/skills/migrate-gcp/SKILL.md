---
name: migrate-gcp
description: Plan a Google Cloud to New Relic alert migration end to end without creating anything: extract, convert, check the data arrives, report. Use when asked to scope or preview a GCP migration.
---

Run the Google Cloud flow in plan-only mode.

1. `uv run importer doctor --source gcp`. Confirm the project id with the user if more than one is
   plausible.
2. Start the `scanner` subagent: `extract-gcp --project <id> --out out/gcp-<date>`, then
   `convert --source gcp --in out/gcp-<date>/policies --out out/gcp-<date>/alerts`.
3. If there are no alert policies, say so and stop.
4. Link and data: if the project is already linked to New Relic, run
   `check-gcp-data --alerts out/gcp-<date>/alerts --nr-account-id <id>` and report which
   conditions are blind. If it is not linked, run `gcp-link-terraform` and show the file. The
   apply grants IAM roles, so the user runs it; never run it yourself. Remind them that polling
   is billed by Google and that destroying the link stops it.
5. `uv run importer report out/gcp-<date> --source gcp` and give the path.
6. Summarise: conditions read, converted, manual with steps, blind conditions, and the open
   caveats (VM CPU utilization does not arrive in New Relic; names verified only for Compute
   Engine and Pub/Sub; no firing test).
7. Creating conditions in New Relic needs the user's yes in this session. They are created
   disabled; enabling is a separate decision for the user.
