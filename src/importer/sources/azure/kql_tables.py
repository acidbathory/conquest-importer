"""Which route brings the rows of a Log Analytics table to New Relic.

The query names a workspace table; New Relic has no tables, so the condition reads the `Log`
event and the table decides what has to be in place first (docs/research/azure-datapath.md):

- `needs-log-forwarding`: Azure writes the rows to a diagnostic setting or the activity log, and
  the Event Hub forwarder posts them to the Logs API. Only `plugin.type = 'azure'` is documented
  by New Relic (azure-log-forwarding.mdx); the attribute names of a forwarded record are not.
- `needs-agent`: the Azure Monitor agent (or the Application Insights SDK) writes the rows. New
  Relic receives equivalent data only from its own agent or an OpenTelemetry collector, under
  names nobody has checked.
- `unknown`: the workspace itself generates the table (Usage, Operation, Sentinel tables) or the
  name is not one we know; no route is documented.

Every attribute name and event type chosen from here on is UNVERIFIED.
"""

import re
from dataclasses import dataclass

LOG_FORWARDING = "needs-log-forwarding"
AGENT = "needs-agent"
UNKNOWN = "unknown"

_AGENT = re.compile(
    r"(Perf|InsightsMetrics|Heartbeat|Event|Syslog|WindowsEvent|SecurityEvent|CommonSecurityLog"
    r"|ConfigurationData|ConfigurationChange|Update.*|W3CIISLog|ContainerLog.*|Container[A-Z].*"
    r"|Kube[A-Z].*|VM[A-Z].*|ADX.*|Windows.*"
    r"|App(Requests|Exceptions|Traces|Dependencies|PageViews|Metrics|Events|AvailabilityResults"
    r"|PerformanceCounters|BrowserTimings|SystemEvents))"
)
_WORKSPACE = re.compile(
    r"(Usage|Operation|Alert|SecurityAlert|SecurityIncident|Watchlist|ThreatIntelligence.*"
    r"|SentinelHealth|LAQueryLogs)"
)
_FORWARDED = re.compile(
    r"(AzureDiagnostics|AzureActivity|AzureMetrics|SigninLogs|AuditLogs|AAD[A-Za-z]*"
    r"|AppService[A-Za-z]*|AGW[A-Za-z]*|AZFW[A-Za-z]*|AZKV[A-Za-z]*|AKS[A-Za-z]*|ADF[A-Za-z]*"
    r"|MicrosoftGraphActivityLogs|FunctionAppLogs|[A-Za-z]+Logs)"
)


@dataclass(frozen=True)
class TableInfo:
    table: str
    path: str  # LOG_FORWARDING, AGENT or UNKNOWN
    azure_filter: bool  # keep `plugin.type = 'azure'` (the forwarder's attribute) in the query


def classify(table: str) -> TableInfo:
    """The data path of a table name. Anything not recognised keeps the forwarder assumption."""
    if _WORKSPACE.fullmatch(table):
        return TableInfo(table, UNKNOWN, azure_filter=False)
    if _AGENT.fullmatch(table):
        return TableInfo(table, AGENT, azure_filter=False)
    if _FORWARDED.fullmatch(table):
        return TableInfo(table, LOG_FORWARDING, azure_filter=True)
    return TableInfo(table, UNKNOWN, azure_filter=True)
