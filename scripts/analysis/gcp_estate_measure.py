"""Measure GCP conversion coverage on the estate corpus (tests/fixtures/gcp-estate), offline.

Usage:
    uv run python scripts/analysis/gcp_estate_measure.py [--corpus DIR] [--json OUT]
        [--markdown OUT] [--no-experiments]

What it does, in order:

1. Assembles the corpus the way `extract-gcp` would have written it: policies and dashboards get
   resource names when the sample has none (a real API export always has them), channels and
   uptime checks go through the extractor's own summary functions (labels, hosts and secrets are
   dropped there, exactly as in a real run).
2. Runs the project's commands in-process: `convert --source gcp`, `convert-gcp-notifications`,
   `convert-gcp-uptime`, `convert-dashboards --source gcp`, `plan-remediation`.
3. Reads the converted IR and counts, as the scoreboard does (docs/dev/overseer.md): an alert
   counts as converted when its confidence is exact or approximate; a widget counts as converted
   only when it is a real chart (`kind == "chart"`); a placeholder ("unsupported") is its own
   line and is never converted. Markdown text widgets are neither (the scoreboard ignores them).
   It also reports "data widgets", which leaves out structural widgets (section headers, groups,
   text, blanks), the way the Grafana estate note left out rows and text boxes.
4. Optional experiments that need no network: how many PromQL expressions the existing Grafana
   PromQL translator would convert, and what shapes the MQL queries have.

Nothing here talks to Google Cloud, New Relic or any other service.
"""

import argparse
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from importer.cli import app
from importer.core.ir import Confidence
from importer.core.scoreboard import widget_class
from importer.sources.gcp.dashboard_models import GcpDashboard
from importer.sources.gcp.dashboards import LAYOUTS, translate_dashboard
from importer.sources.gcp.extract import _channel_summary, _uptime_summary
from importer.sources.gcp.models import GcpAlertPolicy
from importer.sources.gcp.translate import NAMING_NOTE, translate_policy

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CORPUS = ROOT / "tests" / "fixtures" / "gcp-estate"
PROJECT = "projects/example-project"
CONVERTED = (Confidence.EXACT, Confidence.APPROXIMATE)
# widgets that only structure a dashboard; they carry no data to migrate
STRUCTURAL = frozenset({"sectionHeader", "collapsibleGroup", "singleViewGroup", "blank", "text"})
METRIC_TYPE = re.compile(r"""metric\.type\s*=\s*\\?"([^"\\]+)""")
SLO_SELECTOR = re.compile(r"select_slo_[a-z_]+")
NAMING_WORDS = ("did not appear in our live test", "never arrived in our live test")
QUERY_FAMILIES = {
    "conditionPrometheusQueryLanguage": "promql query",
    "conditionMonitoringQueryLanguage": "mql query",
    "conditionSql": "sql query",
    "conditionMatchedLog": "log filter",
}


def metric_family(text: str) -> str:
    """Where the metric in a filter or query comes from (decides whether New Relic can get it)."""
    if SLO_SELECTOR.search(text):
        return "slo"
    for marker, family in (
        ("prometheusQuery", "promql query"),
        ("conditionPrometheusQueryLanguage", "promql query"),
        ("timeSeriesQueryLanguage", "mql query"),
        ("opsAnalyticsQuery", "sql query"),
    ):
        if marker in text:
            return family
    found = METRIC_TYPE.search(text)
    if found is None:
        return "none"
    metric = found.group(1)
    for prefix, family in (
        ("workload.googleapis.com/", "ops-agent third-party (workload)"),
        ("prometheus.googleapis.com/", "managed prometheus"),
        ("agent.googleapis.com/", "ops-agent legacy (agent)"),
        ("custom.googleapis.com/", "custom"),
        ("external.googleapis.com/", "custom"),
        ("logging.googleapis.com/user/", "log-based"),
        ("monitoring.googleapis.com/uptime_check/", "uptime check"),
    ):
        if metric.startswith(prefix):
            return family
    return "google service"


# (pattern on the reason text, code). The first match wins; no match is "other: <reason>".
REASON_CODES: list[tuple[str, str]] = [
    (r"^MQL (conditions|queries)", "mql"),
    (r"^PromQL (conditions|queries)", "promql"),
    (r"^SQL (conditions|queries)", "sql"),
    (r"^ratio (conditions|queries|denominator)", "ratio"),
    (r"^forecast conditions", "forecast"),
    (r"not one of the Google Cloud service metrics", "uncollected metric family"),
    (r"uptime check alert", "uptime-check alert"),
    (r"Prometheus histogram metric arrives|histogram metric arrives", "prometheus histogram"),
    (r"percentile of a distribution metric is not expressible", "percentile on a sent metric"),
    (r"a trigger on several time series", "multi-series trigger"),
    (r"select_slo_", "slo selector filter"),
    (r"log filter comparison on", "log filter comparison"),
    (r"log filter not supported", "log filter syntax"),
    (r"does not make a valid New Relic attribute name", "quoted label name"),
    (r"filter term on 'metadata\.", "metadata label filter"),
    (r"filter term on", "filter term (other)"),
    (r"filter not supported", "filter syntax"),
    (r"aligner '", "aligner"),
    (r"reducer '", "reducer"),
    (r"second-stage", "second-stage aggregation"),
    (r"alignment period", "alignment period"),
    (r"over New Relic's limit", "New Relic limit"),
    (r"comparison '", "comparison"),
    (r"condition type is not recognised", "unknown condition kind"),
    (r"no metric\.type", "no metric.type"),
    (r"is not a recognised name", "metric name"),
    (r"dashboard widget type is not supported", "widget type"),
    (r"text widget has no content", "empty text widget"),
    (r"widget has no query", "widget without query"),
    (r"widget query is not recognised", "unrecognised query"),
]


def reason_code(reason: str) -> str:
    """A short stable name for the reason a condition or widget did not convert."""
    for pattern, code in REASON_CODES:
        if re.search(pattern, reason):
            return code
    return "other: " + reason[:70]


def primary_code(reasons: list[str]) -> str:
    """The code of the first reason that is a blocker (notes about channels are skipped)."""
    for reason in reasons:
        if not reason.startswith("notification channels must be") and "clamped to" not in reason:
            return reason_code(reason.removeprefix("a metric-absence condition could not be "))
    return "none"


def _load(folder: Path) -> list[tuple[str, dict[str, Any]]]:
    return [
        (str(p.relative_to(folder)), json.loads(p.read_text()))
        for p in sorted(folder.glob("*/*.json"))
    ]


class Corpus:
    """The files of the estate, as extract-gcp would have written them."""

    def __init__(self, root: Path, limit: int | None = None) -> None:
        self.root = root
        self.policies = _load(root / "policies")
        self.dashboards = _load(root / "dashboards")
        if limit:
            self.policies, self.dashboards = self.policies[:limit], self.dashboards[:limit]
        for n, (_, policy) in enumerate(self.policies, 1):
            policy.setdefault("name", f"{PROJECT}/alertPolicies/{n:05d}")
            for k, cond in enumerate(policy.get("conditions", []), 1):
                cond.setdefault("name", f"{policy['name']}/conditions/{k}")
        for n, (_, dash) in enumerate(self.dashboards, 1):
            dash.setdefault("name", f"{PROJECT}/dashboards/{n:05d}")
        self.channels = json.loads((root / "channels" / "channels.json").read_text())
        self.uptime = json.loads((root / "uptime-checks" / "uptime-checks.json").read_text())

    def write(self, work: Path) -> dict[str, Path]:
        """The extract-gcp folder layout under `work`; channels and checks are summarised."""
        files = {
            "policies": [p for _, p in self.policies],
            "channels": [_channel_summary(c) for c in self.channels],
            "uptime-checks": [_uptime_summary(c) for c in self.uptime],
            "dashboards": [d for _, d in self.dashboards],
        }
        for folder, data in files.items():
            (work / folder).mkdir(parents=True)
            (work / folder / f"{folder}.json").write_text(json.dumps(data))
        return {k: work / k for k in files}


def run(args: list[str]) -> str:
    done = CliRunner().invoke(app, args)
    if done.exit_code != 0:
        raise RuntimeError(f"importer {' '.join(args[:2])} failed: {done.output[-300:]}")
    return done.output


def run_commands(corpus: Corpus, work: Path) -> dict[str, Path]:
    src = corpus.write(work / "in")
    out = work / "out"
    run(["convert", "--source", "gcp", "--in", str(src["policies"]), "--out", str(out / "alerts")])
    run(
        [
            "convert-gcp-notifications",
            "--channels",
            str(src["channels"]),
            "--alerts",
            str(out / "alerts"),
            "--out",
            str(out / "notifications"),
        ]
    )
    run(["convert-gcp-uptime", "--checks", str(src["uptime-checks"]), "--out", str(out / "uptime")])
    run(
        [
            "convert-dashboards",
            "--source",
            "gcp",
            "--in",
            str(src["dashboards"]),
            "--out",
            str(out / "dashboards"),
        ]
    )
    (out / "to-plan").mkdir()
    for name in ("alerts", "dashboards"):
        (out / "to-plan" / name).symlink_to(out / name)
    run(["plan-remediation", "--in", str(out / "to-plan"), "--out", str(out / "remediation")])
    return {"out": out}


def _tally(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, int]]:
    table: dict[str, Counter[str]] = {}
    for row in rows:
        table.setdefault(row[key], Counter())[row["verdict"]] += 1
    return {k: dict(v) for k, v in sorted(table.items())}


def measure_conditions(corpus: Corpus) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, raw in corpus.policies:
        policy = GcpAlertPolicy.model_validate(raw)
        monitors = translate_policy(policy)
        for cond, monitor in zip(policy.conditions, monitors, strict=True):
            body = cond.body
            text = json.dumps(body)
            reasons = monitor.reasons
            converted = monitor.confidence in CONVERTED
            shape = cond.kind or "unknown"
            if cond.kind == "conditionThreshold":
                if body.get("denominatorFilter"):
                    shape = "conditionThreshold: ratio"
                elif body.get("forecastOptions"):
                    shape = "conditionThreshold: forecast"
                elif SLO_SELECTOR.search(str(body.get("filter", ""))):
                    shape = "conditionThreshold: SLO"
                elif "uptime_check" in str(body.get("filter", "")):
                    shape = "conditionThreshold: uptime check"
                else:
                    shape = "conditionThreshold: metric"
            rows.append(
                {
                    "file": path,
                    "origin": "synthetic" if path.startswith("synthetic/") else "public",
                    "kind": cond.kind or "unknown",
                    "shape": shape,
                    "family": QUERY_FAMILIES.get(cond.kind) or metric_family(text),
                    "data_path": (monitor.data_path or "native") if converted else "",
                    "verdict": "converted" if converted else monitor.confidence.value,
                    "code": "converted" if converted else primary_code(reasons),
                    "name_confirmed": converted
                    and monitor.data_path == "native"
                    and not any(w in r for r in reasons for w in NAMING_WORDS)
                    and NAMING_NOTE not in reasons,
                    "multi": len(policy.conditions) > 1,
                    "combiner": policy.combiner,
                    "channels": len(policy.notificationChannels),
                }
            )
    return rows


def measure_widgets(corpus: Corpus) -> list[dict[str, Any]]:
    """One row per widget the converter writes (the same list `convert` puts in the dashboard IR:
    group headers become pages and blanks are dropped), counted by the scoreboard's rule."""
    rows: list[dict[str, Any]] = []
    for path, raw in corpus.dashboards:
        dash = GcpDashboard.model_validate(raw)
        key, layout = dash.layout
        sources: dict[str, list[str]] = {}  # the source JSON of each widget, by title, in order
        for source, _lay in LAYOUTS[key](layout) if key else []:
            sources.setdefault(str(source.get("title") or ""), []).append(json.dumps(source))
        for result in translate_dashboard(dash).widgets:
            kind = result.source_type
            structural = kind in STRUCTURAL
            verdict = {"converted": "converted", "placeholder": "placeholder", "other": "markdown"}[
                widget_class(result.kind.value)
            ]
            queue = sources.get(result.title)
            text = queue.pop(0) if queue else result.model_dump_json()
            rows.append(
                {
                    "file": path,
                    "origin": "synthetic" if path.startswith("synthetic/") else "public",
                    "kind": kind,
                    "structural": structural,
                    "family": metric_family(text),
                    "data_path": (result.data_path or "native") if verdict == "converted" else "",
                    "verdict": verdict,
                    "code": primary_code(result.reasons)
                    if verdict == "placeholder"
                    else "converted",
                    "all_codes": sorted(
                        {reason_code(r) for r in result.reasons if "clamped to" not in r}
                    )
                    if verdict == "placeholder"
                    else [],
                    "partial": verdict == "converted"
                    and any("data sets converted" in r for r in result.reasons),
                }
            )
    return rows


def ranked(rows: list[dict[str, Any]], key: str = "code") -> list[tuple[str, int]]:
    return Counter(
        r[key] for r in rows if r["verdict"] not in ("converted", "markdown")
    ).most_common()


def blocker_detail(rows: list[dict[str, Any]], *, drop_structural: bool = False) -> dict[str, Any]:
    """Per blocker code: how many items, from which metric families, and three example files."""
    detail: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["verdict"] in ("converted", "markdown") or (
            drop_structural and row.get("structural")
        ):
            continue
        entry = detail.setdefault(
            row["code"], {"count": 0, "alone": 0, "families": Counter(), "examples": []}
        )
        entry["count"] += 1
        entry["alone"] += row.get("all_codes", [row["code"]]) in ([row["code"]], [])
        entry["families"][row["family"]] += 1
        if row["file"] not in entry["examples"] and len(entry["examples"]) < 3:
            entry["examples"].append(row["file"])
    return {
        code: {**e, "families": dict(e["families"].most_common())}
        for code, e in sorted(detail.items(), key=lambda kv: -kv[1]["count"])
    }


# ----- experiments ---------------------------------------------------------------------------

COMPARE_TAIL = re.compile(
    r"^(?P<expr>.*?)\s*(?:>=|<=|==|!=|>|<)\s*[-+]?[0-9.]+(?:e[-+]?[0-9]+)?\s*$", re.S
)
VARIABLE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


MATCHER_BLOCK = re.compile(r"\{([^{}]*)\}")
WHOLE_VARIABLE = re.compile(r"\s*@@[A-Za-z_]+@@\s*")


def _drop_variable_matchers(block: re.Match[str]) -> str:
    kept = [m for m in block.group(1).split(",") if m.strip() and not WHOLE_VARIABLE.fullmatch(m)]
    return "{" + ",".join(kept) + "}"


QUOTED_SELECTOR = re.compile(r'\{\s*"([^"]+)"\s*(?:,\s*([^{}]*?))?\s*\}')


def _plain_selector(found: re.Match[str]) -> str:
    name = re.sub(r"[^A-Za-z0-9_:]", "_", found.group(1))
    return f"{name}{{{found.group(2) or ''}}}"


def prepare_promql(expr: str) -> str:
    """What a GCP-specific pre-processor would do: write the quoted-name selector
    `{"metric.name", k="v"}` as `metric_name{k="v"}`, drop whole-matcher dashboard variables
    (`{${Cluster},${Location}}`) and give `${__interval}` a fixed window."""
    expr = expr.replace("${__interval}", "5m").replace("$__interval", "5m")
    hidden = re.sub(r"\$\{([A-Za-z_]+)\}", r"@@\1@@", expr)  # braces would end a matcher block
    kept = MATCHER_BLOCK.sub(_drop_variable_matchers, QUOTED_SELECTOR.sub(_plain_selector, hidden))
    return re.sub(r"@@([A-Za-z_]+)@@", r"${\1}", kept)


def promql_ceiling(corpus: Corpus) -> dict[str, Any]:
    """What the existing Grafana PromQL translator would do with the GCP PromQL expressions.

    This is a ceiling for 'reuse the Grafana translator', not a result: metric names in New Relic
    depend on how Prometheus data gets there, which Google Cloud's own export does not tell us.
    """
    from importer.sources.grafana.dashboard_convert.name_map import NameMap
    from importer.sources.grafana.dashboard_convert.promql import QueryOptions, convert_query
    from importer.sources.grafana.dashboard_convert.reuse import Unconvertible

    def attempt(expr: str, *, strip: bool, timeseries: bool) -> str:
        expr = prepare_promql(expr)
        names = dict.fromkeys(VARIABLE.findall(expr))
        if strip:
            match = COMPARE_TAIL.match(expr.strip())
            if match is None:
                return "no top-level comparison to a number"
            expr = match["expr"]
        opts = QueryOptions(variables=names, name_map=NameMap(), timeseries=timeseries)
        try:
            convert_query(expr, "prometheus", opts)
        except Unconvertible as stop:
            return f"blocked: {stop.reason[:80]}"
        except Exception as error:
            return f"error: {type(error).__name__}"
        return "converts"

    alerts: Counter[str] = Counter()
    widgets: Counter[str] = Counter()
    for _, raw in corpus.policies:
        for cond in raw.get("conditions", []):
            body = cond.get("conditionPrometheusQueryLanguage")
            if isinstance(body, dict):
                alerts[attempt(str(body.get("query", "")), strip=True, timeseries=False)] += 1
    for _, raw in corpus.dashboards:
        for query in _find_all(raw, "prometheusQuery"):
            widgets[attempt(str(query), strip=False, timeseries=True)] += 1
    return {"alerts": dict(alerts.most_common()), "widgets": dict(widgets.most_common())}


def _find_all(node: Any, key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                found.append(v)
            found += _find_all(v, key)
    elif isinstance(node, list):
        for item in node:
            found += _find_all(item, key)
    return found


MQL_FEATURES = {
    "fetch": r"\bfetch\b",
    "filter": r"\|\s*filter\b",
    "group_by": r"\|\s*group_by\b",
    "align": r"\|\s*align\b|\bevery\b",
    "condition": r"\|\s*condition\b",
    "join / ratio (two streams)": r"\|\s*join\b|\|\s*ratio\b|\{\s*fetch|;\s*fetch",
    "time_shift": r"\btime_shift\b",
    "absent_for": r"\babsent_for\b",
    "top / bottom": r"\|\s*(top|bottom)\b",
    "regex match": r"=~",
}


def mql_shapes(corpus: Corpus) -> dict[str, Any]:
    texts: list[str] = []
    for _, raw in corpus.policies:
        for cond in raw.get("conditions", []):
            body = cond.get("conditionMonitoringQueryLanguage")
            if isinstance(body, dict):
                texts.append(str(body.get("query", "")))
    for _, raw in corpus.dashboards:
        texts += [str(q) for q in _find_all(raw, "timeSeriesQueryLanguage")]
    features = {name: sum(bool(re.search(p, t)) for t in texts) for name, p in MQL_FEATURES.items()}
    # a 'simple' query is one stream: fetch, optional filter, group_by/align, optional condition
    hard = r"\|\s*join\b|\|\s*ratio\b|\{\s*fetch|;\s*fetch|\btime_shift\b|\babsent_for\b|\|\s*(top|bottom)\b"
    simple = sum(not re.search(hard, t) for t in texts)
    return {"total": len(texts), "single_stream": simple, "features": features}


def channel_table(corpus: Corpus, work: Path) -> dict[str, Any]:
    by_type = Counter(c.get("type", "") for c in corpus.channels)
    plans = list((work / "out" / "notifications" / "notification-plans").glob("*.json"))
    used = {h for _, p in corpus.policies for h in p.get("notificationChannels", [])}
    return {
        "channels": len(corpus.channels),
        "by_type": dict(sorted(by_type.items())),
        "routing_sets": len(plans),
        "channels_used_by_a_policy": len(used),
        "created_automatically": 0,
    }


def uptime_table(work: Path) -> dict[str, Any]:
    plan = json.loads((work / "out" / "uptime" / "uptime-plan.json").read_text())
    return {
        "checks": len(plan),
        "by_kind": dict(Counter(p["kind"] for p in plan)),
        "frequency_unknown": sum(p["frequency"].startswith("period unknown") for p in plan),
        "created_automatically": 0,
    }


def cli_cross_check(
    rows: list[dict[str, Any]], widgets: list[dict[str, Any]], work: Path
) -> dict[str, int]:
    """Counts straight from the converter's output files; they must equal the library counts."""
    ir = Counter(
        json.loads(p.read_text())["confidence"]
        for p in (work / "out" / "alerts" / "ir").glob("*.json")
    )
    cli_conditions = sum(ir[c.value] for c in CONVERTED)
    dash_ir = (work / "out" / "dashboards" / "dashboard-ir").glob("*.json")
    kinds: Counter[str] = Counter()
    for path in dash_ir:
        for w in json.loads(path.read_text())["widgets"]:
            kinds[w["kind"]] += 1
    lib_conditions = sum(r["verdict"] == "converted" for r in rows)
    checks = {
        "cli_conditions_total": sum(ir.values()),
        "cli_conditions_converted": cli_conditions,
        "lib_conditions_converted": lib_conditions,
        "cli_widgets_chart": kinds["chart"],
        "lib_widgets_chart": sum(w["verdict"] == "converted" for w in widgets),
        "cli_widgets_unsupported": kinds["unsupported"],
        "lib_widgets_unsupported": sum(w["verdict"] == "placeholder" for w in widgets),
    }
    if checks["cli_conditions_converted"] != checks["lib_conditions_converted"] or checks[
        "cli_conditions_total"
    ] != len(rows):
        raise RuntimeError(f"command output and library disagree: {checks}")
    if (
        checks["cli_widgets_chart"] != checks["lib_widgets_chart"]
        or checks["cli_widgets_unsupported"] != checks["lib_widgets_unsupported"]
    ):
        raise RuntimeError(f"command output and library disagree: {checks}")
    return checks


def by_data_path(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Converted items by the data path they need (placeholders and manual items never count)."""
    return dict(sorted(Counter(r["data_path"] for r in rows if r["data_path"]).items()))


def data_path_known(counts: dict[str, int]) -> int:
    """Converted items whose route to New Relic is known: every class except `unknown`."""
    return sum(n for path, n in counts.items() if path != "unknown")


def percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def summarise(corpus: Corpus, *, experiments: bool = True) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="gcp-estate-") as tmp:
        work = Path(tmp)
        run_commands(corpus, work)
        conditions = measure_conditions(corpus)
        widgets = measure_widgets(corpus)
        check = cli_cross_check(conditions, widgets, work)
        notifications = channel_table(corpus, work)
        uptime = uptime_table(work)
    total = len(conditions)
    converted = sum(r["verdict"] == "converted" for r in conditions)
    confirmed = sum(bool(r["name_confirmed"]) for r in conditions)
    data = [w for w in widgets if not w["structural"]]
    data_conv = sum(w["verdict"] == "converted" for w in data)
    chart = sum(w["verdict"] == "converted" for w in widgets)
    placeholders = sum(w["verdict"] == "placeholder" for w in widgets)
    summary: dict[str, Any] = {
        "policies": len(corpus.policies),
        "dashboards": len(corpus.dashboards),
        "conditions": {
            "total": total,
            "converted": converted,
            "converted_pct": percent(converted, total),
            "converted_with_confirmed_names": confirmed,
            "by_data_path": by_data_path(conditions),
            "converted_data_path_known": data_path_known(by_data_path(conditions)),
            "manual": total - converted,
            "by_shape": _tally(conditions, "shape"),
            "by_family": _tally(conditions, "family"),
            "by_origin": _tally(conditions, "origin"),
            "blockers": ranked(conditions),
            "blocker_detail": blocker_detail(conditions),
        },
        "widgets": {
            "total": len(widgets),
            "scoreboard_converted": chart,
            "scoreboard_placeholders": placeholders,
            "scoreboard_pct": percent(chart, chart + placeholders),
            "data_widgets": len(data),
            "data_converted": data_conv,
            "data_pct": percent(data_conv, len(data)),
            "by_data_path": by_data_path(data),
            "converted_data_path_known": data_path_known(by_data_path(data)),
            "manual": len(data) - data_conv,
            "partially_converted": sum(w["partial"] for w in widgets),
            "by_kind": _tally(widgets, "kind"),
            "by_family": _tally([w for w in data], "family"),
            "by_origin": _tally(widgets, "origin"),
            "blockers": ranked(data),
            "blocker_detail": blocker_detail(widgets, drop_structural=True),
        },
        "notifications": notifications,
        "uptime": uptime,
        "cross_check": check,
    }
    if experiments:
        summary["experiments"] = {
            "promql_through_grafana_translator": promql_ceiling(corpus),
            "mql_shapes": mql_shapes(corpus),
        }
    return summary


def render(summary: dict[str, Any]) -> str:
    c, w = summary["conditions"], summary["widgets"]
    lines = [
        f"corpus: {summary['policies']} policies, {c['total']} conditions, {summary['dashboards']} dashboards, {w['total']} widgets",
        f"conditions converted: {c['converted']} of {c['total']} ({c['converted_pct']}); with a confirmed metric name: {c['converted_with_confirmed_names']}",
        f"widgets (scoreboard): {w['scoreboard_converted']} charts, {w['scoreboard_placeholders']} placeholders ({w['scoreboard_pct']})",
        f"data widgets (no structural): {w['data_converted']} of {w['data_widgets']} ({w['data_pct']})",
        "",
        f"conditions: converted {c['converted']}, data path known {c['converted_data_path_known']}, "
        f"manual {c['manual']}; by data path {c['by_data_path']}",
        f"data widgets: converted {w['data_converted']}, data path known {w['converted_data_path_known']}, "
        f"manual (placeholders) {w['manual']}; by data path {w['by_data_path']}",
        "",
        "condition shape | " + " | ".join(["converted", "manual"]),
    ]
    for shape, counts in c["by_shape"].items():
        lines.append(
            f"  {shape}: {counts.get('converted', 0)} converted, {counts.get('manual', 0)} manual"
        )
    lines += ["", "condition blockers:"] + [f"  {n:4d}  {code}" for code, n in c["blockers"]]
    lines += ["", "widget type | converted placeholder markdown"]
    for kind, counts in w["by_kind"].items():
        lines.append(
            f"  {kind}: {counts.get('converted', 0)} {counts.get('placeholder', 0)} {counts.get('markdown', 0)}"
        )
    lines += ["", "widget blockers (data widgets):"] + [
        f"  {n:4d}  {code}" for code, n in w["blockers"]
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--json", type=Path, help="write the full numbers here")
    parser.add_argument("--no-experiments", action="store_true")
    args = parser.parse_args(argv)
    summary = summarise(Corpus(args.corpus), experiments=not args.no_experiments)
    print(render(summary))
    if args.json:
        args.json.write_text(json.dumps(summary, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
