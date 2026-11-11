"""PromQL in Cloud Monitoring dashboard widgets, through the Grafana PromQL translator.

Managed Service for Prometheus keeps the Prometheus metric names, so the same translator that
reads Grafana panels writes the NRQL. This module only adapts the call: it builds the options,
catches the translator's refusal and returns the reason as text, as the other query readers here
do. Dashboard-level code stays in the GCP modules; nothing in the Grafana package is changed.
"""

import re

from importer.sources.grafana.dashboard_convert import reasons as r
from importer.sources.grafana.dashboard_convert.name_map import (
    NameMap,
    NameMapError,
    active_name_map,
)
from importer.sources.grafana.dashboard_convert.promql import QueryOptions, convert_query
from importer.sources.grafana.dashboard_convert.reuse import Unconvertible

MAX_PROMQL = 5000
DATA_NOTE = (
    "PromQL converted with the Prometheus translator: the metrics must reach New Relic by "
    "Prometheus remote write or OpenTelemetry under the same names"
)


WHOLE_MATCHER = re.compile(r"(?<=[{,])\s*\$\{(\w+)\}\s*(?=[,}])")


def expand_matchers(expr: str, variables: dict[str, str]) -> str:
    """A variable that is a whole matcher, `{${Cluster}}`, written as `cluster="${Cluster}"`.

    Cloud Monitoring fills the matcher in from the dashboard filter on that label; the filter
    names the label, so the matcher is written out. An unknown variable is left alone.
    """

    def one(match: re.Match[str]) -> str:
        name = match[1]
        label = variables.get(name)
        return f'{label}="${{{name}}}"' if label else match[0]

    return WHOLE_MATCHER.sub(one, expr)


def _plain(text: str) -> str:
    """The translator's fixed text without its Grafana prefix (meaningless in a GCP report)."""
    for prefix in (r.NOTE, r.PREFIX + ":"):
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def promql_nrql(
    expr: object, *, timeseries: bool, bucket_seconds: int | None, variables: dict[str, str]
) -> tuple[str, list[str], list[str]] | str:
    """(NRQL, notes, dashboard variables used) for a PromQL string, or the reason it failed."""
    if not isinstance(expr, str) or not expr.strip():
        return "widget has no query"
    if len(expr) > MAX_PROMQL:
        return "the PromQL query is too long to convert"
    try:
        name_map = active_name_map()
    except NameMapError:
        name_map = NameMap()
    opts = QueryOptions(
        variables=dict.fromkeys(variables),
        name_map=name_map,
        timeseries=timeseries,
        bucket_seconds=bucket_seconds,
        intervals=frozenset(),
    )
    try:
        done = convert_query(expand_matchers(expr, variables), "prometheus", opts)
    except Unconvertible as stop:
        return f"PromQL queries in widgets are not converted: {_plain(stop.reason)}"
    except (ValueError, RecursionError):
        return "PromQL queries in widgets are not converted: the query could not be read"
    return done.nrql, [DATA_NOTE, *(_plain(n) for n in done.notes)], done.variables
