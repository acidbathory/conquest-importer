---
name: migrate-aws
description: Plan an AWS CloudWatch to New Relic migration end to end without creating anything: read, convert, report, and list the decisions a person must make. Use when asked to scope or preview an AWS migration.
---

Run the AWS flow in plan-only mode and hand back a report. Creating anything is a separate step
that needs explicit approval in this session.

1. Check the sign-in with `uv run importer doctor`. If the account is not the one the user named,
   stop and ask.
2. Start the `scanner` subagent with: source cloudwatch, the account id and region the user
   gave, an output folder `out/migration-<date>-<label>`, plan-only. Ask it to try the other
   regions only if the user did not name one.
3. If nothing was found, say so and stop.
4. Render the HTML report with `uv run importer report <run folder> --aws-account-id <id>` and
   give the user the path.
5. Summarise in plain language: assets read, converted, manual, and the top reasons with their
   steps. State that converted means "query written, review needed", not "verified".
6. Ask whether to create objects in New Relic. Creating needs the account in
   `NEW_RELIC_ALLOWED_ACCOUNT_IDS`, `--apply --confirm-account <id>`, and the user's yes. Objects
   are created disabled and a manifest is written so `rollback` can undo them. Do not enable
   anything; if asked to, say it changes shared objects and let the user confirm the exact one.
7. Anything that carries cost must be named with its estimated cost before it is created.
