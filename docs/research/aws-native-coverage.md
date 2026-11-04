# AWS-native observability tools and what New Relic supports

Researched 2026-11-05. This note answers one question: which AWS-native observability services does
New Relic document support for, and which of their configuration does this importer migrate?

## How this was checked

The New Relic column comes from New Relic's documentation source (the `develop` branch of
`newrelic/docs-website`), read file by file. The folders searched were `infrastructure/amazon-integrations`,
`logs`, `opentelemetry`, `alerts`, `query-your-data`, `data-apis`, `synthetics`, `browser` and
`infrastructure/prometheus-integrations`. "Not found" means no match in those folders. The rendered
documentation site and New Relic's quickstart catalogue were not checked, so a service marked "not
found" may still have a quickstart.

The AWS-native list itself was written from memory by a research agent and is not checked against
live AWS pages, apart from four items that were: DevOps Guru, Incident Manager, the Chatbot rename
and the Trusted Advisor support-plan change. Recheck anything you depend on.

## Services New Relic supports

| AWS service | New Relic handles | How | This importer migrates its configuration |
|---|---|---|---|
| CloudWatch Metrics (any namespace) | Metrics | Metric Streams through Firehose (recommended); API polling is legacy | Generates the Metric Streams Terraform |
| EC2, ECS, EBS, Lambda, RDS, DynamoDB, S3, SQS, SNS, API Gateway, ElastiCache, Kinesis, load balancers, CloudFront, Redshift, Step Functions, WAF, VPC, MSK and many more | Metrics, with pre-built dashboards | Metric Streams | Through the alarms and dashboards that use them |
| Application Signals | Metrics | Metric Streams only | No. SLOs are listed with manual steps |
| Internet Monitor | Metrics and logs | Metric Streams, log Lambda | No |
| Route 53 health checks | Health-check metrics | Metric Streams or polling | Listed with manual steps |
| Billing and Budgets | Cost metrics, with dashboards | Polling | No |
| Trusted Advisor | Service-limit metrics | Polling; needs a Business or Enterprise support plan | No |
| CloudWatch Alarms | Only the metric behind them | n/a | Yes: single-metric, metric math and composite |
| CloudWatch Dashboards | Not as a source (New Relic has its own pre-built ones) | n/a | Yes: metric and text widgets |
| CloudWatch Logs | Logs | Firehose, or the `NewRelic-log-ingestion` Lambda | Log metric filters and the alarms built on them (simple patterns) |
| VPC Flow Logs, ELB and CloudFront access logs, Security Lake | Logs | Log Lambda, S3 forwarder or Firehose | No |
| Amazon Data Firehose | Its own metrics, and the transport for metrics and logs | HTTP endpoint | Generates the ingest Terraform |
| AWS X-Ray | Traces | Polling only. The documentation says it "should not be used for alerting" | No |
| Lambda | Metrics, logs, traces, events | Metric Streams plus the New Relic Lambda extension layer | No |
| CloudTrail | Events: errors and console logins only | Polling, 5 minutes | No |
| AWS Health | Events | Polling; needs a Business or Enterprise support plan | No |
| EventBridge | Outbound only: New Relic can send notifications to EventBridge | New Relic is a partner event source | Rules are listed with manual steps. No inbound ingestion is documented |
| SNS | Metrics, and an alert destination | Metric Streams | Email and https subscriptions |
| Amazon Q Developer in chat applications | Its own metrics | Metric Streams (us-east-1) | No |

## Services with no documented New Relic support

CloudWatch Logs Insights, Synthetics canaries, RUM, Application Insights, Container Insights,
Lambda Insights, Database and Performance Insights, ADOT, Managed Prometheus, Managed Grafana,
AWS Config (optional metadata only), Systems Manager OpsCenter and Incident Manager, DevOps Guru,
Cost Anomaly Detection and Evidently. Synthetics canaries, Contributor Insights rules and RUM
monitors have New Relic equivalents of their own (Synthetics, log queries, Browser), so the
importer lists them with manual steps instead of dropping them silently.

## Findings that shape the importer

1. New Relic documents ingesting the data of these services, not importing their configuration. No page
   describes importing CloudWatch alarms or dashboards, which is the gap this importer fills.
2. Some paths limit alerting: X-Ray is polling-only, CloudTrail is limited to errors and console
   logins, and Health and Trusted Advisor need a paid AWS support plan.
3. Logs in New Relic carry the attributes `logGroup` and `logStream` when they arrive through the
   Firehose or Lambda paths (documented on the App Runner page). The importer relies on `logGroup`
   to convert alarms built on log metric filters, and says so in the report.

## Recently changed AWS services

Source status: verified means read from the AWS page on 2026-11-05.

- Amazon DevOps Guru stops accepting new customers on 2026-10-29, with access ending 2027-09-30 (verified).
- Systems Manager Incident Manager no longer accepts new customers (verified).
- AWS Chatbot is now Amazon Q Developer in chat applications; the `chatbot` API namespace is unchanged (verified).
- The Trusted Advisor page announces support-plan changes on 2027-01-01 (verified).
- Reported but unconfirmed: Evidently end of support, the Performance Insights to Database
  Insights move, X-Ray SDK deprecation dates, and CloudWatch Alarm Mute Rules.
