"""Emit Terraform for the newrelic provider.

Monitor text is untrusted, so every string goes through hcl_string(). No API
keys are written: the provider reads NEW_RELIC_API_KEY and NEW_RELIC_ACCOUNT_ID
from the environment.
"""

from importer.emit.common import (
    AGGREGATION_DELAY_SECONDS,
    AGGREGATION_WINDOW_SECONDS,
    condition_name,
    emittable_window,
    nrql_for,
)
from importer.models.ir import IRMonitor

HEADER = """terraform {
  required_providers {
    newrelic = {
      source = "newrelic/newrelic"
    }
  }
}

provider "newrelic" {}

resource "newrelic_alert_policy" "imported" {
  name = "Imported from Datadog"
}
"""


def hcl_string(value: str) -> str:
    """Quote text as an HCL string: escape quotes, backslashes, controls and interpolation."""
    out = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    text = "".join(out).replace("${", "$${").replace("%{", "%%{")
    return f'"{text}"'


def resource_block(monitor: IRMonitor) -> str:
    window = emittable_window(monitor)
    lines = [
        f'resource "newrelic_nrql_alert_condition" "dd_{monitor.source_id}" {{',
        "  policy_id = newrelic_alert_policy.imported.id",
        f"  name      = {hcl_string(condition_name(monitor.name))}",
        f"  description = {hcl_string(f'Imported from Datadog monitor {monitor.source_id}')}",
        '  type      = "static"',
        "  enabled   = false",
        f"  aggregation_window = {AGGREGATION_WINDOW_SECONDS}",
        '  aggregation_method = "event_flow"',
        f"  aggregation_delay  = {AGGREGATION_DELAY_SECONDS}",
        "",
        "  nrql {",
        f"    query = {hcl_string(nrql_for(monitor))}",
        "  }",
    ]
    for t in monitor.thresholds:
        lines += [
            "",
            f"  {t.severity.value} {{",
            f"    operator              = {hcl_string(t.operator.value)}",
            f"    threshold             = {t.value!r}",
            f"    threshold_duration    = {window}",
            '    threshold_occurrences = "all"',
            "  }",
        ]
    if monitor.no_data.notify and monitor.no_data.timeout_minutes:
        lines += [
            "",
            f"  expiration_duration            = {monitor.no_data.timeout_minutes * 60}",
            "  open_violation_on_expiration   = true",
            "  close_violations_on_expiration = false",
        ]
    lines.append("}")
    return "\n".join(lines) + "\n"


def terraform_file(monitors: list[IRMonitor]) -> str:
    return HEADER + "".join("\n" + resource_block(m) for m in monitors)
