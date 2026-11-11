"""The one place where dashboards ask for a KQL query to be turned into NRQL.

The KQL translator is built on another branch (a subset translator in its own modules). Until it
lands, `translate_kql` answers with the reason that the query stays a placeholder. To connect the
translator, replace the body of `translate_kql` with a call to it and return a `KqlNrql` on
success or the reason text on failure; nothing else in the dashboard code changes. Callers reach
it as `dashboard_kql.translate_kql(...)` (never `from ... import`), so a test can replace it.
"""

from collections.abc import Collection
from dataclasses import dataclass

from importer.sources.azure import dashboard_reasons as DR

LOG_FORWARDING = "needs-log-forwarding"


@dataclass(frozen=True)
class KqlNrql:
    """A KQL query as NRQL: one SELECT over a table the logs arrive in."""

    nrql: str
    timeseries: bool = False  # the query returns one value per time bucket
    notes: tuple[str, ...] = ()
    variables: tuple[str, ...] = ()  # New Relic dashboard variables the NRQL uses as {{name}}


def translate_kql(query: str, variables: Collection[str]) -> KqlNrql | str:  # noqa: ARG001
    """NRQL for a workbook or dashboard KQL query, or the reason it cannot be converted.

    `variables` are the names of New Relic dashboard variables this dashboard defines; the
    translator may use them as `{{name}}` and must list the ones it used.
    """
    return DR.KQL_PENDING
