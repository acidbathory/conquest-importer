"""Emit Terraform for the newrelic provider.

Monitor text is untrusted, so every string goes through hcl_string(). No API
keys are written: the provider reads NEW_RELIC_API_KEY and NEW_RELIC_ACCOUNT_ID
from the environment.
"""

from importer.core.emit.common import (
    RESOURCE_PREFIXES,
    aggregation_delay,
    aggregation_timer,
    aggregation_window,
    condition_name,
    emit_defaults,
    emittable_window,
    fill_settings,
    nrql_for,
    source_description,
    streaming_method,
)
from importer.core.ir import IRMonitor

INCIDENT_PREFERENCES = ("PER_POLICY", "PER_CONDITION", "PER_CONDITION_AND_TARGET")
_HEADER_START = """terraform {
  required_providers {
    newrelic = {
      source = "newrelic/newrelic"
    }
  }
}

provider "newrelic" {}

resource "newrelic_alert_policy" "imported" {
  name = "Imported by Conquest Importer"
"""
HEADER = _HEADER_START + "}\n"


def header(incident_preference: str | None = None) -> str:
    """The provider and policy. Unless a preference is given the policy sets none, and New
    Relic's default (PER_POLICY, one open issue for the whole policy) applies."""
    if incident_preference is None:
        return HEADER
    if incident_preference not in INCIDENT_PREFERENCES:
        raise ValueError(f"unknown incident preference {incident_preference!r}")
    return _HEADER_START + f'  incident_preference = "{incident_preference}"\n}}\n'


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
    prefix = RESOURCE_PREFIXES.get(monitor.source_system, "src")
    baseline = monitor.signal.baseline
    method = streaming_method(monitor)
    timing = (
        f"  aggregation_timer  = {aggregation_timer(monitor)}"
        if method == "EVENT_TIMER"
        else f"  aggregation_delay  = {aggregation_delay(monitor)}"
    )
    lines = [
        f'resource "newrelic_nrql_alert_condition" "{prefix}_{monitor.source_id}" {{',
        "  policy_id = newrelic_alert_policy.imported.id",
        f"  name      = {hcl_string(condition_name(monitor.name))}",
        f"  description = {hcl_string(source_description(monitor))}",
        f'  type      = "{"baseline" if baseline else "static"}"',
        "  enabled   = false",
        f"  aggregation_window = {aggregation_window(monitor)}",
        f'  aggregation_method = "{method.lower()}"',
        timing,
        "",
        "  nrql {",
        f"    query = {hcl_string(nrql_for(monitor))}",
        "  }",
    ]
    if baseline:
        lines.insert(len(lines) - 4, f'  baseline_direction = "{baseline.direction}"')
    for t in monitor.thresholds:
        lines += [
            "",
            f"  {t.severity.value} {{",
            f"    operator              = {hcl_string('above' if baseline else t.operator.value)}",
            f"    threshold             = {t.value!r}",
            f"    threshold_duration    = {window}",
            f'    threshold_occurrences = "{monitor.signal.occurrences.value}"',
            "  }",
        ]
    if monitor.no_data.notify and monitor.no_data.timeout_minutes:
        lines += [
            "",
            f"  expiration_duration            = {monitor.no_data.timeout_minutes * 60}",
            "  open_violation_on_expiration   = true",
            "  close_violations_on_expiration = false",
        ]
    lines += _optional_lines(monitor)
    lines.append("}")
    return "\n".join(lines) + "\n"


def _setting(name: str, value: str) -> str:
    return f"  {name:<30} = {value}"


def _optional_lines(monitor: IRMonitor) -> list[str]:
    """Settings present only when the monitor (or a changed default) asks for them."""
    out: list[str] = []
    fill = fill_settings(monitor)
    if fill is not None:
        out.append(_setting("fill_option", f'"{fill[0].lower()}"'))
        if fill[1] is not None:
            out.append(_setting("fill_value", f"{fill[1]:g}"))
    options = monitor.emit
    if options is None:
        return ["", *out] if out else out
    if options.slide_by_seconds is not None:
        out.append(_setting("slide_by", str(options.slide_by_seconds)))
    if options.violation_time_limit_seconds is not None:
        out.append(
            _setting("violation_time_limit_seconds", str(options.violation_time_limit_seconds))
        )
    if options.title_template is not None:
        out.append(_setting("title_template", hcl_string(options.title_template)))
    if options.runbook_url is not None:
        out.append(_setting("runbook_url", hcl_string(options.runbook_url)))
    if options.ignore_on_expected_termination is not None:
        flag = "true" if options.ignore_on_expected_termination else "false"
        out.append(_setting("ignore_on_expected_termination", flag))
    return ["", *out] if out else out


def terraform_file(monitors: list[IRMonitor], incident_preference: str | None = None) -> str:
    """The provider, the policy and one condition per monitor. `incident_preference` falls back
    to EmitDefaults.incident_preference, which is None (no preference written) today."""
    chosen = incident_preference or emit_defaults().incident_preference
    return header(chosen) + "".join("\n" + resource_block(m) for m in monitors)
