# Measuring on a real CloudWatch account (customer export)

Goal: get one number, "how many of this customer's alarms convert, and why the rest fail", without
the tool ever holding the customer's credentials. Nothing here writes to AWS or New Relic.

## What the customer (or whoever has access) runs
Needs the AWS CLI signed in to the target account with read access (`ReadOnlyAccess` is enough).
The command runs only `describe-alarms`, `list-dashboards`, `get-dashboard`,
`sns list-subscriptions-by-topic` and `sts get-caller-identity`, and stops if the signed-in
account differs from `--account-id`.

```sh
uv run importer extract-cloudwatch --account-id <12-digit id> --region <region> --out out/cloudwatch
# Optional: --profile <name>; --no-dashboards; --no-notifications
```
One region per run; repeat with `--out` set to a new folder for each extra region.

Without this repo, the minimum is: `aws cloudwatch describe-alarms --alarm-types MetricAlarm
CompositeAlarm --output json > describe-alarms.json`.

## Review before sharing
The export can contain account ids, ARNs, alarm and resource names, and SNS email addresses.
Check `out/cloudwatch/` by eye; replace anything sensitive; do not send it over chat with secrets.
Never include credentials or `.env`.

## What we run on it (read-only, local files only)
```sh
uv run importer convert --source cloudwatch --in out/cloudwatch/alarms --out out/cw
uv run importer convert-dashboards --in out/cloudwatch/dashboards --out out/cw-dash
uv run importer stats out/cw --out out/stats.md
```
`out/cw/report.md` lists each alarm as exact / approximate / manual / unsupported with a reason;
`stats` gives the conversion rate and the most common failure reasons.

## Reading the result
- Synthetic rates (15/23, 6/9 real-export) are not customer results; do not quote them.
- Report the new number with the sample size and the top three failure categories.
- Ask the manager: what failed in the earlier attempt, and what conversion rate counts as success.
