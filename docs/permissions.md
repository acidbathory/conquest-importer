# Permissions

The importer uses two credentials, and each can be limited to the minimum.

## AWS: read-only

The importer only reads. Create an IAM user or role with this policy, or use AWS's managed
`ReadOnlyAccess`, which is broader:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "cloudwatch:DescribeAlarms",
        "cloudwatch:ListDashboards",
        "cloudwatch:GetDashboard",
        "cloudwatch:GetMetricStatistics",
        "sns:ListSubscriptionsByTopic"
      ],
      "Resource": "*"
    }
  ]
}
```

(`sts:GetCallerIdentity` needs no permission.) `GetMetricStatistics` is used only by
`importer validate`, so skip it if you don't run that.

Give the tool the keys the normal AWS way:

```sh
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...      # only for temporary keys
```

or name a profile from `~/.aws/credentials` with `--profile my-profile`. The region is
`--region` (default `us-east-1`); a run covers one region.

Use keys you can revoke afterwards. The tool never writes them to a file or log.

## New Relic: an API key, only to create things

A plan-only run (the default) needs no New Relic key. To create alerts, dashboards and workflows:

1. In New Relic, open API keys and create a User key (it starts with `NRAK-`).
2. The user behind it must be allowed to manage alerts, dashboards and notifications in the
   account you are migrating into. Try a test or sandbox account first.
3. Export it:

```sh
export NEW_RELIC_API_KEY=NRAK-...
export NEW_RELIC_ALLOWED_ACCOUNT_IDS=1234567     # the account(s) this tool may write to
```

`NEW_RELIC_ALLOWED_ACCOUNT_IDS` is a safety catch: the tool refuses to write to any account that
isn't listed. If your account is in the EU data centre, add `--nr-region eu`.

The data-ingestion Terraform also needs a New Relic license key. The importer never reads it; you
give it to Terraform yourself (see [getting started](getting-started.md#step-6-stream-the-metrics)).
