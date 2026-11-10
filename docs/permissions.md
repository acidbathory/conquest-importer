# Permissions

The importer uses two credentials: a read-only one for the system you are leaving, and a New Relic
User API key to create things. Jump to your source: [AWS CloudWatch](#aws-cloudwatch),
[Google Cloud](#google-cloud-monitoring), [Grafana](#grafana), [Dynatrace](#dynatrace),
[New Relic](#new-relic-every-source). A section marked UNVERIFIED has not been seen working on a live
system; start read-only and add only what an error asks for.

## AWS CloudWatch

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

### Optional: the rest of your monitoring setup

These extra read-only actions let the importer find log metric filters, EventBridge rules and
other monitoring configuration, so the report can convert alarms built on log metrics and list
the rest with next steps. Leave them out and the run still works: each one that is denied shows
up as a "Could not read" warning, and alarms on log-derived metrics stay manual.

```json
{
  "Effect": "Allow",
  "Action": [
    "logs:DescribeMetricFilters",
    "events:ListRules",
    "events:ListTargetsByRule",
    "synthetics:DescribeCanaries",
    "cloudwatch:DescribeInsightRules",
    "rum:ListAppMonitors",
    "application-signals:ListServiceLevelObjectives",
    "route53:ListHealthChecks"
  ],
  "Resource": "*"
}
```

Add this statement to the policy above.

Give the tool the keys the normal AWS way:

```sh
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...      # only for temporary keys
```

or name a profile from `~/.aws/credentials` with `--profile my-profile`. The region is
`--region` (default `us-east-1`); a run covers one region.

Use keys you can revoke afterwards. The tool never writes them to a file or log.

## Google Cloud Monitoring

Read-only. The importer reads alert policies, notification channels, uptime checks and dashboards.

- Role: Monitoring Viewer (`roles/monitoring.viewer`) on the project. UNVERIFIED: that this is the
  minimum.
- Sign-in: `gcloud auth application-default login`, or set `GOOGLE_OAUTH_ACCESS_TOKEN` to a
  short-lived access token. The importer never stores credentials.
- To link the project for data (a person runs `terraform apply`), the deployer needs to create a
  service account and a workload identity pool and grant IAM roles. The generated Terraform grants New
  Relic's read-only service account `roles/viewer`, `roles/serviceusage.serviceUsageConsumer` and
  `roles/cloudasset.viewer`; review them. Narrowing the deployer's own permissions is an open to-do.

Details: [runbook-gcp](user/runbook-gcp.md#2-prerequisites).

## Grafana

Read-only. The importer sends only `GET` requests, on a fixed list of paths.

- A Grafana service account token, starting with the Viewer role, in `GRAFANA_SA_TOKEN`, with the
  stack address in `GRAFANA_URL` (`https://<stack>.grafana.net`; self-hosted needs `--allow-host`).
- It reads: alert rules, contact points, notification policies, mute timings, templates, data sources
  (type and uid), dashboards, folders and library panels.
- UNVERIFIED: which exact Grafana RBAC action each path needs on your version. If a command answers
  403, add only the missing read permission.
- Check the token before use (`GET /api/access-control/user/permissions`): a token requested as Viewer
  in our sandbox held alerting write permissions. Give it a short expiry and delete it afterwards.

Details: [runbook-grafana](user/runbook-grafana.md#2-prerequisites).

## Dynatrace

Read-only token in `DYNATRACE_API_TOKEN`, tenant address in `DYNATRACE_ENV_URL`
(`https://<env-id>.apps.dynatrace.com`).

| Command | Scopes (read only) |
|---|---|
| `extract-dynatrace` (workflows, SLOs) | `automation:workflows:read`, `slo:slos:read` (platform token) |
| `extract-dynatrace-classic` (metric events, Davis detectors) | `settings:objects:read`, `settings:schemas:read` (platform token), or an API token with `settings.read` |
| `extract-dynatrace-inventory` (dashboards, synthetics, entities) | UNVERIFIED. The code mentions `document:documents:read`, `entities.read` and `ReadConfig`; start read-only and add what a 403 asks for |

Details: [runbook-dynatrace](user/runbook-dynatrace.md#2-prerequisites).

## New Relic (every source)

Making a plan needs no New Relic key. To create alerts, dashboards and workflows:

1. In New Relic, open API keys and create a User key (it starts with `NRAK-`).
2. The user behind it must be allowed to manage alerts, dashboards and notifications in the
   account you are migrating into. Try a test or sandbox account first.
3. Export it:

Put these in a `.env` file in the folder you run `importer` from, or export them in your shell:

```sh
NEW_RELIC_API_KEY=NRAK-...
NEW_RELIC_ACCOUNT_ID=1234567
NEW_RELIC_ALLOWED_ACCOUNT_IDS=1234567     # the account(s) this tool may write to
```

`NEW_RELIC_ALLOWED_ACCOUNT_IDS` is a safety catch: the tool refuses to write to any account that
isn't listed. If your account is in the EU data centre, add `--nr-region eu`.

Two kinds of New Relic key exist, and they do different jobs:

- The **User API key** (`NRAK-...`, `NEW_RELIC_API_KEY`) is the only key the importer reads. It creates
  conditions, workflows and dashboards, deletes them on rollback, and runs the data checks.
- The **ingest licence key** sends data to New Relic. The importer never reads it. You give it to the
  Terraform or collector you deploy yourself (for AWS, see
  [getting started](getting-started.md#step-6-stream-the-metrics)).

Set `SSL_CERT_FILE` to the corporate root certificate (PEM) if your network inspects TLS.
