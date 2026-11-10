"""Public documentation used by the playbooks. Each page was fetched on CHECKED_ON and the claim
named in `note` was read in it. `LEADS` were not read and are marked unverified in the plan."""

from importer.core.playbook import Link

D = "https://docs.newrelic.com/docs/"

NRQL = Link(D + "nrql/nrql-syntax-clauses-functions/", "NRQL clauses and functions")
SUBQUERY = Link(
    D + "nrql/using-nrql/subqueries-in-nrql/",
    "subqueries (not supported in NRQL alert conditions)",
)
NESTED = Link(
    D + "nrql/using-nrql/nested-aggregation-make-ordered-computations-single-query/",
    "nested aggregation",
)
LOOKUP = Link(
    D + "nrql/using-nrql/lookups/",
    "lookup tables (CSV upload; not usable in alert conditions)",
)
CONDITION = Link(
    D + "alerts/create-alert/create-alert-condition/create-nrql-alert-conditions/",
    "NRQL alert conditions: one aggregated value per condition, nested query limits",
)
STREAMING = Link(
    D + "alerts/create-alert/fine-tune/streaming-alerts-key-terms-concepts/",
    "aggregation window, delay or timer, loss of signal, gap filling",
)
ANOMALY = Link(
    D + "alerts/create-alert/set-thresholds/anomaly-detection", "anomaly (baseline) conditions"
)
MUTING = Link(
    D + "alerts/get-notified/muting-rules-suppress-notifications/",
    "muting rules: daily, weekly or monthly schedule with a time zone",
)
WORKFLOWS = Link(
    D + "alerts/get-notified/incident-workflows/", "workflows: issue filters and NRQL enrichments"
)
DESTINATIONS = Link(D + "alerts/get-notified/destinations/", "destinations")
INTEGRATIONS = Link(
    D + "alerts/get-notified/notification-integrations/",
    "notification integrations (Slack, Teams, Jira, ServiceNow, PagerDuty, EventBridge, webhook)",
)
TEMPLATES = Link(D + "alerts/get-notified/message-templates/", "message templates")
ALERT_LIMITS = Link(D + "alerts/admin/rules-limits-alerts", "alert limits")
PARSING = Link(D + "logs/ui-data/parsing/", "log parsing rules (Grok or regex, applied at ingest)")
CHANGE_TRACKING = Link(
    D + "change-tracking/change-tracking-introduction/",
    "change tracking: markers on charts, created through NerdGraph",
)
VARIABLES = Link(
    D + "query-your-data/explore-query-data/dashboards/dashboard-template-variables/",
    "dashboard variables (+ Add variable, replacement strategies)",
)
NESTED_VARIABLES = Link(
    D + "query-your-data/explore-query-data/dashboards/nested-variables/",
    "nested variables (Pro, Enterprise and Core Compute only)",
)
CHARTS = Link(D + "query-your-data/explore-query-data/use-charts/chart-types/", "chart types")
IMPORT_EXPORT = Link(
    D + "query-your-data/explore-query-data/dashboards/dashboards-charts-import-export-data/",
    "import and export a dashboard as JSON",
)
PROMQL_TRANSLATE = Link(
    D + "infrastructure/prometheus-integrations/view-query-data/translate-promql-queries-nrql/",
    "New Relic's PromQL to NRQL translation",
)
PROMQL_SUPPORTED = Link(
    D + "infrastructure/prometheus-integrations/view-query-data/supported-promql-features/",
    "supported PromQL features",
)
REMOTE_WRITE = Link(
    D
    + "infrastructure/prometheus-integrations/install-configure-remote-write/"
    + "set-your-prometheus-remote-write-integration/",
    "Prometheus remote write setup",
)
OTLP = Link(D + "opentelemetry/best-practices/opentelemetry-otlp/", "sending OTLP data")
SLM = Link(D + "service-level-management/create-slm/", "service levels (SLI and SLO)")
TF_CONDITION = Link(
    "https://registry.terraform.io/providers/newrelic/newrelic/latest/docs/resources/nrql_alert_condition",
    "Terraform newrelic_nrql_alert_condition",
)
TF_DASHBOARD = Link(
    "https://registry.terraform.io/providers/newrelic/newrelic/latest/docs/resources/one_dashboard",
    "Terraform newrelic_one_dashboard",
)
TF_MUTING = Link(
    "https://registry.terraform.io/providers/newrelic/newrelic/latest/docs/resources/alert_muting_rule",
    "Terraform newrelic_alert_muting_rule",
)
OTEL_TRANSFORM = Link(
    "https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/transformprocessor",
    "OpenTelemetry transform processor (rename or reshape metrics and attributes)",
)
OTEL_DELTA = Link(
    "https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/cumulativetodeltaprocessor",
    "OpenTelemetry cumulative-to-delta processor",
)
GRAFANA_MUTE = Link(
    "https://grafana.com/docs/grafana/latest/alerting/configure-notifications/mute-timings/",
    "mute timings in the Grafana documentation",
)
