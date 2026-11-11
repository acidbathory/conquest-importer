"""Playbooks for the Azure remediation rules, same titles as `remediation_rules.py`.

All text is fixed; none of it comes from an export. Effort figures are rough planning numbers.
"""

from importer.core.playbook import Playbook, register_playbook
from importer.core.playbooks import links as L


def _reg(title: str, **fields: object) -> None:
    register_playbook(title, Playbook(**fields))  # type: ignore[arg-type]


CREATE = (
    "Create the condition: Alerts > Alert conditions > + New alert condition > Write your own "
    "query. Create it disabled, compare it with the Azure rule for a day, then enable it."
)

_reg(
    "Azure metric alert with several criteria",
    source="An Azure metric alert that fires only when all of its criteria are true.",
    newrelic="No composite condition exists. The nearest is one condition on the criterion "
    "that matters most.",
    steps=(
        "Read the per-criterion NRQL in the report.",
        "Pick the criterion that should page and create it alone.",
        "Keep the others as warning conditions or dashboard charts.",
        CREATE,
    ),
    differs="The rule can now fire on one criterion without the others.",
    effort_minutes=30,
    no_equivalent=True,
    links=(L.CONDITION,),
)
_reg(
    "Azure dynamic threshold",
    source="A metric alert whose threshold Azure learns from history.",
    newrelic="A baseline condition on the same metric, tuned by hand.",
    steps=(
        "Create a baseline condition on the metric query.",
        "Compare a week of history and tune the standard deviations; there is no published "
        "mapping from Azure's sensitivity levels.",
        "Or set a static threshold from the recent range.",
    ),
    differs="Different model; the learning period and seasonality handling are not the same.",
    effort_minutes=30,
    links=(
        L.ANOMALY,
        L.CONDITION,
    ),
)
_reg(
    "Azure availability test alert",
    source="An alert on the number of failing locations of an availability test.",
    newrelic="A Synthetics monitor with an alert condition on its failures.",
    steps=(
        "Recreate the check as a Synthetics monitor from the same locations.",
        "Add a condition on failed checks and choose how many locations must fail.",
    ),
    differs="Check scheduling and location names differ.",
    effort_minutes=45,
    links=(L.CONDITION,),
)
_reg(
    "Azure rule of an unknown type",
    source="A rule type or kind that was added to Azure after this importer was written.",
    newrelic="Decided case by case.",
    steps=("Read the rule in the portal.", CREATE),
    differs="Unknown.",
    effort_minutes=30,
    links=(L.CONDITION,),
)
_reg(
    "Azure data that the integration does not collect",
    source="A metric or log table whose data is not a platform metric.",
    newrelic="The same signal from an agent or the OpenTelemetry collector.",
    steps=(
        "Send the data to New Relic.",
        "Find the name it arrives under in the data explorer.",
        "Write the condition on that name and add it to name-map.json for the next run.",
    ),
    differs="The names and attributes differ from anything the importer can derive.",
    effort_minutes=60,
    links=(L.OTLP,),
)
_reg(
    "Azure rule field the importer cannot read",
    source="A rule with a value outside what the importer understands.",
    newrelic="A condition written by hand.",
    steps=("Open the rule and check the value named in the reason.", CREATE),
    differs="Depends on the field.",
    effort_minutes=20,
    links=(L.CONDITION,),
)
_reg(
    "Azure window outside the supported range",
    source="A rule evaluated over a window longer than an hour or shorter than a minute.",
    newrelic="A condition with a window the importer accepts, or one built by hand.",
    steps=(
        "Check the longest aggregation window your account accepts.",
        "Use a one hour window with a longer duration only if the meaning is acceptable.",
    ),
    differs="One long window is not the same as many short ones that must all breach.",
    effort_minutes=20,
    links=(
        L.STREAMING,
        L.ALERT_LIMITS,
    ),
)
_reg(
    "Azure scope that one condition cannot cover",
    source="A rule scoped to several resource groups or subscriptions.",
    newrelic="One condition for each prefix.",
    steps=("Create one condition for each prefix listed in the report.", CREATE),
    differs="Several conditions replace one rule.",
    effort_minutes=15,
    links=(L.CONDITION,),
)
_reg(
    "Azure KQL join or union",
    source="A KQL query that joins, looks up or unions two tables.",
    newrelic="One condition per data set, or an IN list for a short fixed lookup.",
    steps=(
        "Work out what the join was for.",
        "Write a short fixed lookup as `IN ('a', 'b')` in the WHERE.",
        "Otherwise create one condition per table and compare them on a dashboard.",
        CREATE,
    ),
    differs="The correlation between the two tables is not evaluated by New Relic.",
    effort_minutes=45,
    no_equivalent=True,
    links=(L.NRQL, L.CONDITION),
)
_reg(
    "Azure KQL parsing or expansion",
    source="A KQL query that parses, extracts or expands a column inside the query.",
    newrelic="A log parsing rule at ingest, then a condition on the parsed attribute.",
    steps=(
        "Add a New Relic log parsing rule so the captured value is an attribute.",
        "Write the condition on that attribute, or use RLIKE for a plain pattern match.",
        CREATE,
    ),
    differs="Records ingested before the parsing rule existed do not have the attribute.",
    effort_minutes=45,
    links=(L.NRQL, L.CONDITION),
)
_reg(
    "Azure KQL time series or absence check",
    source="A KQL query with series functions, or a last-seen check against ago().",
    newrelic="A baseline condition, or loss of signal with the same duration.",
    steps=(
        "Series functions: create a baseline condition on the aggregate and tune it.",
        "Absence check: create a condition with loss of signal set to the age in the query.",
        CREATE,
    ),
    differs="Azure computes the anomaly or the last-seen time inside the query.",
    effort_minutes=30,
    links=(L.ANOMALY, L.CONDITION),
)
_reg(
    "Azure KQL cross-resource query or function",
    source="A KQL query over another workspace, or with a let function or materialize.",
    newrelic="One condition per workspace, with the let body written in place.",
    steps=(
        "Send each workspace's logs to New Relic.",
        "Write one condition per workspace with the let body in place.",
        CREATE,
    ),
    differs="One Azure query becomes several New Relic conditions.",
    effort_minutes=60,
    no_equivalent=True,
    links=(L.NRQL, L.CONDITION),
)
_reg(
    "Azure KQL query",
    source="A KQL query that the importer does not translate.",
    newrelic="An NRQL query over forwarded logs, written by hand.",
    steps=(
        "Work out what the query counts or measures.",
        "Look at one forwarded log record for the attribute names.",
        "Write `SELECT count(*) FROM Log WHERE plugin.type = 'azure' AND ...`.",
        CREATE,
    ),
    differs="KQL operators such as join, let and regular expressions have no NRQL form.",
    effort_minutes=45,
    template="SELECT count(*) FROM Log WHERE plugin.type = 'azure'",
    links=(
        L.NRQL,
        L.CONDITION,
    ),
)
_reg(
    "Azure log table from an agent or service",
    source="A query over a table filled by the Azure Monitor agent or an Azure service.",
    newrelic="The matching New Relic agent data.",
    steps=(
        "Perf and InsightsMetrics: use the infrastructure agent or Kubernetes data.",
        "Heartbeat: use loss of signal on an infrastructure signal.",
        "Application tables: use APM or OpenTelemetry data.",
    ),
    differs="Different agent, different attribute names.",
    effort_minutes=60,
    links=(L.CONDITION,),
)
_reg(
    "Azure log alert shape",
    source="A log alert with several conditions, an old format or a measure column.",
    newrelic="One condition per query.",
    steps=("Split the rule into single-condition queries.", CREATE),
    differs="Several conditions replace one rule.",
    effort_minutes=30,
    links=(L.CONDITION,),
)
_reg(
    "Azure activity log alert",
    source="A rule that fires for every matching activity event.",
    newrelic="A count of forwarded activity logs above 0, or keep the rule in Azure.",
    steps=(
        "Deploy the Event Hub log forwarder with the matching activity log flag.",
        "Look at one forwarded record to learn the attribute names.",
        "Create the count condition over a 60 second window, or keep the Azure rule.",
    ),
    differs="Events arrive minutes late and the attribute names are not confirmed.",
    effort_minutes=60,
    no_equivalent=True,
    links=(
        L.NRQL,
        L.CONDITION,
    ),
)
_reg(
    "Azure smart detector rule",
    source="A machine-learning detector rule.",
    newrelic="A baseline condition on the signal it watched.",
    steps=("Name the signal the detector watched.", "Create a baseline condition on it."),
    differs="Different model.",
    effort_minutes=45,
    no_equivalent=True,
    links=(L.ANOMALY,),
)
_reg(
    "Azure rule group without alerts",
    source="A Prometheus rule group with recording rules only.",
    newrelic="Nothing to convert; write recorded expressions in place.",
    steps=("Find the alert expressions that use the recorded names.",),
    differs="No stored recording rules.",
    effort_minutes=10,
    no_equivalent=True,
    links=(L.PROMQL_TRANSLATE,),
)
_reg(
    "Azure Prometheus rule that could not be read",
    source="A Prometheus rule with a placeholder or an unreadable duration.",
    newrelic="A condition on the fixed expression.",
    steps=("Fix the rule in Azure and export again.",),
    differs="None once fixed.",
    effort_minutes=10,
    links=(L.PROMQL_SUPPORTED,),
)
