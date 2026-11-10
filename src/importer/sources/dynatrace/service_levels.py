"""Dynatrace SLOs to service level plans that a person finishes.

An SLO's indicator is a DQL query over Dynatrace metrics, and those metrics do not exist in New
Relic, so there is no honest good-events or valid-events query to generate. This keeps what does
carry over (target, period, name) in a structured plan, leaves the two event queries as TODO and
says why. There is no New Relic data path here: choosing one is the human step.

Names and indicator text are untrusted. They go to the plan file as JSON, to the report through
md_cell, and to Terraform comments through hcl_string only.

UNVERIFIED (written offline, not checked against New Relic): the 1, 7 and 28 day periods, that a
service level has no warning level, and every attribute name in the commented Terraform.
"""

import hashlib
import math
import re
from typing import Any

from pydantic import BaseModel

from importer.core.emit.terraform import hcl_string
from importer.core.report import md_cell
from importer.core.report_brand import header_lines
from importer.core.text import printable
from importer.sources.dynatrace.models import DtSlo

MAX_NAME = 120
MAX_INDICATOR = 2000
SCAN_LIMIT = 5000  # the indicator text scanned for metric names; longer text is cut
MAX_METRICS = 20
NR_PERIOD_DAYS = (1, 7, 28)
DAYS_PER_UNIT = {"d": 1, "w": 7}
TODO = "TODO"
RANGE_START = re.compile(r"now-(\d{1,4})([dw])")
# `alias = sum(metric.key)` inside a timeseries; every part is length-bounded
METRIC = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]{0,30}\s{0,10}=\s{0,10}(?:sum|avg|min|max|count)\(\s{0,10}"
    r"([A-Za-z_][A-Za-z0-9_.:-]{0,99})\s{0,10}\)"
)


class ServiceLevelPlan(BaseModel):
    name: str
    source_key: str
    target: float | None
    warning: float | None
    period_days: int | None
    nr_period: str  # "7 DAY" and so on, or "" when New Relic has no such period
    source_metrics: list[str]  # Dynatrace metric keys the indicator reads (not in New Relic)
    indicator: str  # the Dynatrace indicator text, cut; untrusted
    good_events_nrql: str = TODO
    valid_events_nrql: str = TODO
    needs_review: bool = True
    notes: list[str]


def _percent(value: object) -> float | None:
    """A finite number above 0 and at most 100, else None (bool and text are not numbers)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and 0 < number <= 100 else None


def _period(criterion: dict[str, Any]) -> int | None:
    start, end = criterion.get("timeframeFrom"), criterion.get("timeframeTo")
    match = RANGE_START.fullmatch(start) if isinstance(start, str) else None
    if match is None or end != "now":
        return None
    return int(match[1]) * DAYS_PER_UNIT[match[2]]


def _metrics(indicator: str) -> list[str]:
    if not indicator.lstrip().startswith("timeseries"):
        return []
    found = METRIC.findall(indicator[:SCAN_LIMIT])
    return list(dict.fromkeys(found))[:MAX_METRICS]


def _plan(slo: DtSlo, key: str) -> ServiceLevelPlan:
    notes: list[str] = []
    criterion = slo.criteria[0] if slo.criteria and isinstance(slo.criteria[0], dict) else {}
    if len(slo.criteria) > 1:
        notes.append("more than one criterion: only the first was read")
    target = _percent(criterion.get("target"))
    if target is None:
        notes.append(
            "the target is missing or not a number from 0 to 100, so it was not carried over"
        )
    warning = _percent(criterion.get("warning"))
    if warning is not None:
        notes.append(
            "a warning level has no equivalent in a New Relic service level; not carried over"
        )
    days = _period(criterion)
    nr_period = f"{days} DAY" if days in NR_PERIOD_DAYS else ""
    if not nr_period:
        notes.append(
            "the time frame has no New Relic service level period (1, 7 or 28 days); choose one"
        )
    indicator = str(slo.customSli.get("indicator", ""))
    metrics = _metrics(indicator)
    if metrics:
        notes.append(
            "the indicator reads Dynatrace metrics that do not exist in New Relic; write the "
            "good and valid events over data New Relic has (transactions, spans, synthetic "
            "results or logs)"
        )
    else:
        notes.append("the indicator is not the total and failed counts pattern; read it by hand")
    return ServiceLevelPlan(
        name=printable(slo.name, MAX_NAME),
        source_key=key,
        target=target,
        warning=warning,
        period_days=days,
        nr_period=nr_period,
        source_metrics=metrics,
        indicator=printable(indicator, MAX_INDICATOR),
        notes=notes,
    )


def service_level_plans(slos: list[DtSlo]) -> list[ServiceLevelPlan]:
    """One plan per SLO. A repeated id and name gets `#n`, so the keys stay distinct."""
    seen: dict[str, int] = {}
    plans: list[ServiceLevelPlan] = []
    for slo in slos:
        base = f"{slo.id}/{slo.name}"
        seen[base] = seen.get(base, 0) + 1
        plans.append(_plan(slo, base if seen[base] == 1 else f"{base}#{seen[base]}"))
    return plans


def _skeleton(plan: ServiceLevelPlan) -> str:
    slug = hashlib.sha256(plan.source_key.encode()).hexdigest()[:10]
    target = f"{plan.target:.10g}" if plan.target is not None else TODO
    count, unit = plan.nr_period.split(" ") if plan.nr_period else (TODO, TODO)
    return (
        f'# resource "newrelic_service_level" "sl_{slug}" {{\n'
        f'#   guid = "{TODO} entity guid"\n'
        f"#   name = {hcl_string(plan.name)}\n"
        f"#   events {{\n"
        f"#     account_id = 0 # {TODO}\n"
        f'#     valid_events {{\n#       from  = "{TODO}"\n#       where = "{TODO}"\n#     }}\n'
        f'#     good_events {{\n#       from  = "{TODO}"\n#       where = "{TODO}"\n#     }}\n'
        f"#   }}\n"
        f"#   objective {{\n"
        f"#     target = {target}\n"
        f"#     time_window {{\n#       rolling {{\n"
        f'#         count = {count}\n#         unit  = "{unit}"\n'
        f"#       }}\n#     }}\n#   }}\n"
        f"# }}\n"
    )


def service_levels_terraform(plans: list[ServiceLevelPlan]) -> str:
    """Commented skeletons only. Nothing here can be applied until a person fills it in."""
    head = (
        "# Service level plans from Dynatrace SLOs. Everything is commented out on purpose.\n"
        "# UNVERIFIED: the newrelic_service_level attribute names below are from memory, not\n"
        "# from the provider documentation. Check them with `terraform validate` first.\n"
        f"# The good and valid events are {TODO}: Dynatrace metrics do not exist in New Relic.\n"
    )
    return head + "".join("\n" + _skeleton(p) for p in plans)


def render_report(plans: list[ServiceLevelPlan]) -> str:
    lines = [
        *header_lines("Service level plan (Dynatrace SLOs)", "dynatrace"),
        "",
        "Every row needs review. There is no data path from the Dynatrace metrics to New Relic, "
        "so the good and valid events are left as TODO for a person to write.",
        "",
        "| SLO | Target | Warning | New Relic period | Indicator metrics | Notes |",
        "|---|---|---|---|---|---|",
    ]
    for p in plans:
        target = "" if p.target is None else f"{p.target:.10g}"
        warning = "" if p.warning is None else f"{p.warning:.10g}"
        lines.append(
            f"| {md_cell(p.name)} | {target} | {warning} | {md_cell(p.nr_period or 'none')} "
            f"| {md_cell(', '.join(p.source_metrics) or 'not read')} "
            f"| {md_cell('; '.join(p.notes), 400)} |"
        )
    return "\n".join(lines) + "\n"
