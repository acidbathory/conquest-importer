# Migration testing against real AWS and New Relic

This is the guide for testing the importer end to end on real accounts. Unit tests (see
[dev/testing.md](dev/testing.md)) never touch the network; this suite does. Agents and humans use
the same steps.

The suite creates a different mix of AWS resources each run, migrates them into a New Relic test
account, checks every result against what [what-migrates.md](what-migrates.md) promises, and tears
everything down. A disagreement is a finding: either the tool is wrong or the docs are.

## The four levels

| Level | Question it answers | How it checks |
|---|---|---|
| 1 Sanity | Is the environment sound, and does the importer see what exists? | `importer doctor` passes; `extract-cloudwatch` reads exactly the alarms that were created |
| 2 Integration | Is every item classified the way the docs say? | Plan-only `migrate`; each item is migrated or not as documented, with a reason that names the cause and remediation steps |
| 3 End to end | Does `migrate --apply` really create the objects? | Every object in the manifest is read back from New Relic: conditions and workflows disabled, dashboards private, every condition's NRQL runs |
| 4 Regression on New Relic | Is it repeatable and reversible? | A second apply creates nothing new; converted output for the same alarm is identical in every iteration; rollback and teardown leave both accounts empty |

## Safety and cost (read first)

- Use test accounts only: an AWS account you can freely create and delete things in, and the New
  Relic account in `.env`. The suite stops if the signed-in AWS account isn't the one you named,
  or if the New Relic account isn't in `NEW_RELIC_ALLOWED_ACCOUNT_IDS`. It also refuses to start
  if `ckt-` resources already exist.
- AWS objects start with `ckt-` and carry the tag
  `owner=conquest-importer-suite`. Teardown removes only objects with that prefix.
- New Relic objects are created disabled or private, and each iteration rolls them back.
- The resources live a few minutes: alarms (some at high-resolution or anomaly pricing), five
  dashboards, custom metric data points, a log group, an X-Ray group, SNS topics and SQS queues.
  Expect cents per iteration and well under $10 for twelve. Report any cost, and don't add RDS, NAT
  gateways, load balancers or EC2 instances to the catalogue.
- The suite sends no notifications. It never subscribes an email address or an https endpoint.
- Don't print `.env` values. The API key is read from the environment and never logged.

## Run it

```sh
uv run python scripts/live-test/suite.py --aws-account-id <aws-account-id> --iterations 12 --yes
uv run python scripts/live-test/suite.py --aws-account-id <aws-account-id> --iterations 12 --only 4 --yes
```

`--yes` is required: it confirms that you accept the writes and the (small) cost. Twelve
iterations take about 30 to 40 minutes.

Results land in `out/suite/` (git-ignored):

| File | Content |
|---|---|
| `iteration-NN/result.json` | profile, number of resources, headline, timings, findings |
| `iteration-NN/logs/NN-command.log` | the full output of every importer command that ran |
| `iteration-NN/{plan,apply,again}/` | the real run folders, including `report.html` |
| `baseline.json` | converted-output hashes used to detect changes between iterations |
| `summary.json` | all iterations together |

## What each iteration builds

The profiles are in `PROFILES` in `scripts/live-test/suite.py`. Together they cover every area:

| # | Profile | Focus |
|---|---|---|
| 1, 12 | full | everything; iteration 12 repeats 1 to prove stability |
| 2 | std, pct | alarms on the supported services, percentile statistics |
| 3 | custom, logs, traces | custom metrics, a log metric filter with an alarm, an X-Ray group and trace |
| 4 | composite | OR, AND and nested composite alarms |
| 5 | dash | dashboards: line, stacked, bar, single value, text, log, alarm and math widgets |
| 6 | notify, actions | SNS topics (with and without an SQS subscription), non-SNS alarm actions |
| 7 | edge, math, notes | high-resolution, one-day period, anomaly, `SampleCount`, `RATE()`, 3-of-5, missing data |
| 8 to 11 | random | a random half of the catalogue, a different one each time |

## Where expectations come from

Each catalogue entry in `CATALOG` states what the docs say should happen (`migrated`,
`not_migrated`, `either` or `not_reported`) and the words the reason must contain. Do not derive
expectations from the converter's output; that would hide bugs. If the docs and the tool disagree,
decide which is right, then fix one and say so in the results file.

## Reading the results

Findings have a level and a place:

| Level | Meaning |
|---|---|
| `setup` | AWS refused to create a test resource. A problem with the suite, not the tool |
| `sanity`, `integration`, `e2e`, `regression` | A disagreement with the docs or a failed check. Treat as a tool or docs bug |
| `teardown` | Something was left behind. Clean it up first (below), then investigate |

Group findings by root cause before reporting; one bug usually shows up in many iterations. For
each, give the smallest reproduction, the log file, and whether it is the tool or the docs.
Write the dated summary to `docs/testing-results/YYYY-MM-DD.md`.

## If a run is interrupted

```sh
uv run python - <<'EOF'
import sys; sys.path.insert(0, "scripts/live-test")
import suite
print("left:", suite.teardown(suite.make_ctx("<aws-account-id>")))
EOF
```

For New Relic, roll back each iteration's manifest:
`importer rollback --manifest out/suite/iteration-NN/apply/manifest.json --account-id <id> --apply --confirm-account <id>`.
Check afterwards that no policy named `Imported from CloudWatch (<aws-account-id>)` and no `ckt-*`
dashboard remains.

## Add a scenario

Add an `Element` to `CATALOG` with a create function, the documented expectation and a tag, and
add the tag to a profile if it needs its own iteration. Keep it cheap and keep it prefixed `ckt-`.

## Known coverage gaps

- Email and https notification channels are not exercised live: subscribing sends a message to
  a real address or endpoint. They are covered only by the synthetic fixtures.
- Metric data ingestion (Metric Streams into New Relic) is not run here, so the suite checks
  that NRQL is valid and runs, not that it returns numbers. Use `importer validate` for that.
- RDS, S3, ECS, ElastiCache, EBS and load-balancer metric names are unconfirmed (see
  [what-migrates.md](what-migrates.md)); the suite checks that their alarms convert and apply.
- Logs and traces are not migrated by this tool. The suite checks that they are ignored
  cleanly and that alarms built on top of them are handled.
- One region and one account only.
