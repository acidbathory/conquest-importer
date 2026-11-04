# Conquest Importer

[![ci](https://github.com/acidbathory/conquest-importer/actions/workflows/ci.yml/badge.svg)](https://github.com/acidbathory/conquest-importer/actions/workflows/ci.yml)

Move your **AWS CloudWatch** alarms, dashboards and notifications to **New Relic**, and get a report
that tells you exactly what moved, what did not, why, and what to do about it.

![The migration report](docs/img/sample-report.png)

> **Status: prototype.** The CloudWatch path is built and tested against a real AWS account. Other
> platforms are on the [roadmap](#roadmap). Treat every converted item as something to review, not to
> trust blindly.

## What you need

| | |
|---|---|
| **Python 3.12 or newer** | `python3 --version` |
| **AWS keys** that can read CloudWatch | [exact permissions](docs/permissions.md) (read-only) |
| **A New Relic API key** | only to *create* things; a plan-only run does not need it |

Nothing else. No AWS CLI, no Terraform (Terraform is optional, for streaming metric data).

## Try it in five minutes

```sh
git clone https://github.com/acidbathory/conquest-importer.git && cd conquest-importer
python3 -m venv .venv && source .venv/bin/activate
pip install .

export AWS_ACCESS_KEY_ID=...        # read-only keys, see docs/permissions.md
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...        # only if your keys are temporary

importer doctor                     # checks Python, AWS keys and (optionally) New Relic
importer migrate --aws-account-id 123456789012 --nr-account-id 1234567
open out/migration/report.html      # or double-click it
```

That run **reads** your AWS account and writes files on your machine. It creates nothing in New
Relic. The last line it prints is your headline number:

```
Total completion: 70% (19 of 27 assets migrated, 8 need manual work)
```

When you are happy with the plan, [create the objects in New Relic](docs/getting-started.md#step-5-create-it-in-new-relic)
(everything is created disabled or private, and one command undoes it).

## What can and cannot be migrated

| Asset | Migrates | Does not migrate (and what to do) |
|---|---|---|
| **Alarms** | Single-metric alarms on 15 AWS services and `Custom/` metrics, including percentiles and simple metric math | Composite, anomaly-detection, high-resolution (< 60 s) and complex-math alarms: each gets written manual steps |
| **Dashboards** | Metric charts and text widgets | Log, alarm-status and Metrics Explorer widgets, and math inside widgets |
| **Notifications** | SNS email and https subscriptions become destinations and (disabled) workflows | SMS, Lambda, SQS, mobile push: reported with a replacement suggestion |
| **Metric data** | Terraform for AWS Metric Streams is generated for you to deploy | Logs, traces, SLOs, synthetics, EventBridge: not handled |

Every row, with the reason and the fix: **[docs/what-migrates.md](docs/what-migrates.md)**.

## How the report counts

One alarm, one dashboard or one notification subscription is one *asset*. **Migrated** means it was
converted (exactly, or approximately with notes to read first). **Not migrated** means a person must
finish it. Total completion is migrated assets divided by all assets, shown with the per-type split
and the reason behind every miss. Percentages measured on our own test data are not customer
results; yours will differ.

## Safe by default

- Reads only. The AWS permissions are read-only, and the code can only call a fixed list of read operations.
- Creating anything needs `--apply`, the account id repeated, and the account on an allow-list. Alerts
  and workflows are created **disabled**; dashboards **private**.
- `importer rollback` removes exactly what a run created, nothing else.
- Keys are read from the environment, never written to files or logs.
- The report contains your alarm names. `importer stats` prints a version with names removed, safe to share.

## Documentation

| | |
|---|---|
| [Getting started](docs/getting-started.md) | The full walkthrough, from install to rollback |
| [What migrates](docs/what-migrates.md) | Everything that does and does not move, and why |
| [Permissions](docs/permissions.md) | The AWS policy and New Relic key, copy and paste |
| [Troubleshooting](docs/troubleshooting.md) | Error messages and what to do |
| [Sharing results](docs/customer-export.md) | How to send us feedback without leaking names |
| [Contributing](CONTRIBUTING.md) | Checks, conventions, how the code is laid out |

## Roadmap

AWS CloudWatch is supported today. These platforms are planned, in this order:

1. Google Cloud (GCP)
2. Microsoft Azure
3. Datadog
4. Grafana
5. Dynatrace

Want to help finish the project? Fork the repository, make your change on a branch and open a pull
request. Contributions of any size are welcome, from a new platform to a fix to the docs; start with
[CONTRIBUTING.md](CONTRIBUTING.md).

## Give feedback

Tried it? [Open a feedback issue](../../issues/new/choose) and paste the output of `importer stats`.
It contains no names. What failed, and what you expected to work, is the most useful thing we can get.
