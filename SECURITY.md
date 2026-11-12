# Security policy

## Reporting a problem

Report a vulnerability, or any place where the tool writes or prints a secret, privately: use
GitHub's "Report a vulnerability" button on the repository's Security tab (private advisory). Do
not open a public issue or pull request for it, and do not paste real keys, tokens, account ids
or customer data anywhere public; redact them first. If the private route is not available,
contact the maintainer directly and ask for a private channel before sending details.

Expect an acknowledgement within a few working days. Fixes go to `main`; there are no
backports.

## Supported versions

The project is pre-1.0 (`0.1.0`). Only the latest commit on `main` is supported.

## What the tool does and does not do with your data

- **Sources are read-only.** Grafana, Dynatrace, AWS CloudWatch, Google Cloud and Azure are
  only read through list and get calls. Nothing is created, changed or deleted in them.
- **New Relic is create-only.** Applying creates new alert policies, conditions, workflows,
  destinations and dashboards, and nothing is created until you confirm after a preview. It
  never updates or deletes anything that already exists in the account.
- **Secrets are never written.** API keys and tokens are read from environment variables or a
  git-ignored `.env`. They are not saved in run records, reports, generated Terraform, logs or
  fixtures. Untrusted text from a source (monitor names, queries, messages) reaches outputs only
  through the escaping functions.
- **Output is private.** Run records and extracted inventories are written to folders with mode
  `0700` and files with mode `0600`, atomically.
- **No telemetry.** The tool talks only to the platform APIs you point it at.

## Checks in this repository

Secret scanning (gitleaks, in pre-commit and CI), a dependency licence gate, a clean-room scan,
`pip-audit`, and a CycloneDX software bill of materials built in CI.
