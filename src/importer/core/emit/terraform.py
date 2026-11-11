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
    expiration_actions,
    fill_settings,
    has_expiration,
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
    return (
        _HEADER_START.replace('  name = "Imported', '  name                = "Imported')
        + f'  incident_preference = "{incident_preference}"\n}}\n'
    )


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


def hcl_attrs(pairs: list[tuple[str, str]], indent: str) -> list[str]:
    """`key = value` lines with the `=` signs aligned the way `terraform fmt` does."""
    width = max((len(key) for key, _ in pairs), default=0)
    return [f"{indent}{key.ljust(width)} = {value}" for key, value in pairs]


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
            *_prediction_lines(monitor),
            "  }",
        ]
    if has_expiration(monitor) and monitor.no_data.timeout_minutes:
        lines += [
            "",
            f"  expiration_duration            = {monitor.no_data.timeout_minutes * 60}",
            f"  open_violation_on_expiration   = {str(expiration_actions(monitor)[0]).lower()}",
            f"  close_violations_on_expiration = {str(expiration_actions(monitor)[1]).lower()}",
        ]
    lines += _optional_lines(monitor)
    lines.append("}")
    return "\n".join(lines) + "\n"


def _prediction_lines(monitor: IRMonitor) -> list[str]:
    options = monitor.emit
    if options is None or options.predict_by_seconds is None:
        return []
    return ["    prediction {", f"      predict_by = {options.predict_by_seconds}", "    }"]


def _optional_lines(monitor: IRMonitor) -> list[str]:
    """Settings present only when the monitor (or a changed default) asks for them."""
    pairs: list[tuple[str, str]] = []
    fill = fill_settings(monitor)
    if fill is not None:
        pairs.append(("fill_option", f'"{fill[0].lower()}"'))
        if fill[1] is not None:
            pairs.append(("fill_value", f"{fill[1]:g}"))
    options = monitor.emit
    if options is not None:
        if options.slide_by_seconds is not None:
            pairs.append(("slide_by", str(options.slide_by_seconds)))
        if options.violation_time_limit_seconds is not None:
            pairs.append(
                ("violation_time_limit_seconds", str(options.violation_time_limit_seconds))
            )
        if options.title_template is not None:
            pairs.append(("title_template", hcl_string(options.title_template)))
        if options.runbook_url is not None:
            pairs.append(("runbook_url", hcl_string(options.runbook_url)))
        if options.ignore_on_expected_termination is not None:
            flag = "true" if options.ignore_on_expected_termination else "false"
            pairs.append(("ignore_on_expected_termination", flag))
    return ["", *hcl_attrs(pairs, "  ")] if pairs else []


def terraform_file(monitors: list[IRMonitor], incident_preference: str | None = None) -> str:
    """The provider, the policy and one condition per monitor. `incident_preference` falls back
    to EmitDefaults.incident_preference, which is None (no preference written) today."""
    chosen = incident_preference or emit_defaults().incident_preference
    return header(chosen) + "".join("\n" + resource_block(m) for m in monitors)
