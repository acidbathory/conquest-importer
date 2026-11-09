# Conquest Importer

[![license](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![ci](https://github.com/acidbathory/conquest-importer/actions/workflows/ci.yml/badge.svg)](https://github.com/acidbathory/conquest-importer/actions/workflows/ci.yml)

Moves your AWS CloudWatch alarms, dashboards and notifications to New Relic. When it finishes you
get a report of what moved, what didn't, why, and what to do about it.

![The migration report (preview: nothing created yet)](docs/img/sample-report.png)

> Status: prototype. The CloudWatch path has been tested against a real AWS account and the Google
> Cloud path against a sandbox project. Check every converted item before you enable it. Other
> platforms are on the [roadmap](#roadmap).

## What you need

| | |
|---|---|
| Python 3.12 or newer | `python3 --version` |
| AWS keys that can read CloudWatch | [exact permissions](docs/permissions.md) (read-only) |
| A New Relic API key | only to create things; a plan-only run doesn't need it |

You don't need the AWS CLI. Terraform is optional and only used to stream metric data.

## Try it in five minutes

```sh
git clone https://github.com/acidbathory/conquest-importer.git && cd conquest-importer
python3 -m venv .venv && source .venv/bin/activate
pip install .

cp .env.example .env     # then fill in the three New Relic lines (skip for a plan only)
importer migrate
```

AWS uses the login you already have (environment variables or a profile; add `--profile name` for
a named one). `migrate` then:

1. Tells you which AWS account you're signed in to and asks you to confirm it.
2. Reads that account and writes a plan and a report on your machine.
3. Opens the report in your browser. It starts with a Preview banner, and its numbers are a
   forecast of what would be created.
4. Asks whether to create everything in New Relic. Press Enter to stop here with nothing created,
   or answer `y` to create it.

The last line it prints before the question is the headline number:

```
Total completion (forecast): 70% (19 of 27 assets can be migrated, 8 need manual work; nothing has been created in New Relic yet)
```

After `y`, alerts and workflows exist disabled and dashboards private. The report is rewritten as
Applied: its numbers describe what was created, and anything New Relic refused counts as not
migrated, with the reason. To undo it:

```sh
importer rollback
```

That lists what the latest run created and asks before deleting it. Creating needs the New Relic key
and your account on `NEW_RELIC_ALLOWED_ACCOUNT_IDS`, so a mistyped account id can't write to the
wrong place. In a script, pass `--aws-account-id` and `--yes`. The
[walkthrough](docs/getting-started.md) covers the details.

## What can and cannot be migrated

| Asset | Migrates | Does not migrate (and what to do) |
|---|---|---|
| Alarms | Single-metric alarms on 15 AWS services and `Custom/` metrics, including percentiles and simple metric math, and alarms on log metrics | Composite, anomaly-detection, high-resolution (under 60 s) and complex-math alarms. Each gets written manual steps |
| Dashboards | Metric charts and text widgets | Log, alarm-status and Metrics Explorer widgets, and math inside widgets |
| Notifications | SNS email and https subscriptions become destinations and (disabled) workflows | SMS, Lambda, SQS and mobile push. Each is reported with a replacement suggestion |
| Log metric filters | Alarms built on a filter with a simple pattern become log queries | Complex patterns, plus EventBridge rules, canaries, SLOs and health checks. Each is listed with steps |
| Metric data | Terraform for AWS Metric Streams is generated for you to deploy | Logs, traces, SLOs, synthetics and EventBridge are not handled |

Every row, with the reason and the fix, is in [docs/what-migrates.md](docs/what-migrates.md).

## How the report counts

One alarm, one dashboard or one notification subscription is one asset. An asset counts as migrated
when it was converted, either exactly or approximately with notes to read first. Anything else
needs a person to finish it. Total completion is migrated assets divided by all assets, with the
split per type and the reason behind every miss. Percentages measured on our own test data aren't
customer results, and yours will differ.

## Safe by default

- It only reads AWS. The permissions are read-only, and the code can call only a fixed list of read operations.
- It creates nothing in New Relic until you answer `y` (or pass `--yes`), and then only if the account is on an allow-list.
- `importer rollback` removes what a run created and nothing else.
- Keys are read from the environment and never written to files or logs.
- The report contains your alarm names. `importer stats` prints a version with the names removed, which is safe to share.

## Documentation

| | |
|---|---|
| [Getting started](docs/getting-started.md) | The full walkthrough, from install to rollback |
| [What migrates](docs/what-migrates.md) | Everything that does and doesn't move, and why |
| [Permissions](docs/permissions.md) | The AWS policy and New Relic key, ready to copy |
| [Troubleshooting](docs/troubleshooting.md) | Error messages and what to do about them |
| [Sharing results](docs/customer-export.md) | How to send feedback without leaking names |
| [Contributing](CONTRIBUTING.md) | Checks, conventions and how the code is laid out |

## Roadmap

AWS CloudWatch is the platform tested most. Google Cloud alert policies also convert (`extract-gcp`,
`convert --source gcp`); that path has been tested in a sandbox project and not on customer data.
Datadog support was built and then removed; it is paused until AWS, GCP and Azure are covered.
Planned, in this order:

1. Microsoft Azure
2. Datadog (paused)
3. Grafana
4. Dynatrace

Want to help finish the project? Fork the repository, make your change on a branch and open a pull
request. Contributions of any size are welcome, from a new platform to a fix to the docs; start with
[CONTRIBUTING.md](CONTRIBUTING.md).

## Give feedback

Tried it? [Open a feedback issue](../../issues/new/choose) and paste the output of `importer stats`.
It contains no names. The most useful thing you can tell us is what failed and what you expected
to work.

## License

[Apache License 2.0](LICENSE).
