"""Playbooks for the Datadog dashboard remediation rules (same titles as `rules.py`).

All text is fixed; none of it comes from an export. Effort figures are rough planning numbers.
"""

from importer.core.playbook import Playbook, register_playbook
from importer.core.playbooks import links as L
from importer.sources.datadog.dashboards.rules import RULES

# title -> (what it is in Datadog, the closest New Relic construct, what differs, minutes, steps)
BOOKS: dict[str, tuple[str, str, str, int]] = {
    "Datadog dashboard widget type not converted": (
        "A widget of a type that is not a plain metric chart or a note.",
        "A New Relic chart of the matching type, or an entity page, service level or alert.",
        "Heatmaps, distributions and treemaps are drawn differently; some types have no "
        "counterpart.",
        30,
    ),
    "Datadog dashboard widget without a query": (
        "A widget with no readable query: decoration, or a request shape not read.",
        "A markdown widget, or a chart rebuilt from the widget editor.",
        "Images and embedded pages are not carried over.",
        10,
    ),
    "Datadog dashboard widget on logs or another data source": (
        "A widget over logs, RUM, APM, processes, cost or network data.",
        "NRQL over Log or the matching event type, after the data is sent to New Relic.",
        "Attribute names and query syntax differ; the data must arrive first.",
        45,
    ),
    "Datadog dashboard widget with several queries": (
        "A chart that draws several queries as separate series.",
        "One widget per query, or one NRQL query with several functions.",
        "A single chart with several series becomes several charts.",
        15,
    ),
    "Datadog dashboard formula": (
        "A formula or arithmetic that combines queries point by point.",
        "Arithmetic between functions in one NRQL SELECT, or a subquery.",
        "NRQL combines window aggregates, not points.",
        30,
    ),
    "Datadog dashboard query function": (
        "A function applied to a query: top, rollup, as_rate, timeshift, fill and others.",
        "FACET with LIMIT, TIMESERIES, rate(), derivative(), COMPARE WITH, filter().",
        "Bucket sizes and null handling are chosen differently.",
        20,
    ),
    "Datadog dashboard query not readable": (
        "A query this converter does not read as a single metric query.",
        "Hand-written NRQL.",
        "Nothing is converted from the unreadable part.",
        20,
    ),
    "Datadog dashboard single value": (
        "A single-value widget whose aggregator or grouping has no direct NRQL form.",
        "A billboard with average, sum, min or max, or latest() for one series.",
        "latest() is one raw point, not the newest point of a combined series.",
        15,
    ),
    "Datadog dashboard template variable not usable": (
        "A template variable that is undefined or whose name New Relic rejects.",
        "A dashboard variable created by hand.",
        "Variable names are letters, digits and underscores.",
        15,
    ),
    "Datadog dashboard template variable form": (
        "A template variable used in a way other than a tag filter.",
        "An NRQL dashboard variable used in WHERE ... IN ({{name}}).",
        "Variables cannot be used in FACET, LIMIT or TIMESERIES.",
        20,
    ),
    "Datadog dashboard template variable default": (
        "A variable with no tag key whose default is a whole scope.",
        "The scope written into the query, or a tag variable.",
        "A scope variable cannot be switched at view time in New Relic.",
        15,
    ),
    "Datadog dashboard widget refused by the dashboard model": (
        "A widget the converted model could not hold.",
        "The query builder.",
        "None once rebuilt.",
        15,
    ),
}

for _pattern, _title, _steps in RULES:
    _source, _newrelic, _differs, _minutes = BOOKS[_title]
    register_playbook(
        _title,
        Playbook(
            source=_source,
            newrelic=_newrelic,
            steps=tuple(_steps),
            differs=_differs,
            effort_minutes=_minutes,
            links=(L.NRQL, L.TF_DASHBOARD, L.VARIABLES),
        ),
    )
