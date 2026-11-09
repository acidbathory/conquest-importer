"""Shared pieces of a data check: does the data behind a converted condition exist?

A condition on data that never arrives looks healthy and never fires. Each source builds its own
queries from names its converter produced; this module holds the result type, the single place
that runs a count query, and the report. Only read queries are sent.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from importer.core.apply import GuardError, NewRelicAPIError, NewRelicClient
from importer.core.ir import IRMonitor
from importer.core.report import md_cell
from importer.core.secrets import MissingSecretError
from importer.core.text import printable

NRQL_QUERY = (
    "query($a: Int!, $n: Nrql!) { actor { account(id: $a) { nrql(query: $n) { results } } } }"
)


NOTE_LIMIT = 300


class Status(StrEnum):
    DATA = "data arrives"
    NO_DATA = "no data"
    NOT_CHECKED = "not checked"


@dataclass
class Check:
    name: str
    status: Status
    points: int
    value: float | None
    note: str


def rows(client: NewRelicClient, account_id: int, query: str) -> list[dict[str, Any]]:
    data = client.run(NRQL_QUERY, {"a": account_id, "n": query}, is_write=False)
    found: list[dict[str, Any]] = data["actor"]["account"]["nrql"]["results"]
    return found


def first_number(result: list[dict[str, Any]]) -> float | None:
    if not result:
        return None
    value = next(iter(result[0].values()), None)
    return float(value) if isinstance(value, int | float) else None


def render(checks: list[Check], hours: int, intro: str) -> str:
    lines = [
        "# Data check",
        "",
        f"{intro} Window: the last {hours} hours.",
        "",
        "| Condition | Status | Points | Latest query value | Note |",
        "|---|---|---|---|---|",
    ]
    for c in checks:
        value = "" if c.value is None else f"{c.value:g}"
        note = md_cell(c.note, NOTE_LIMIT)
        lines.append(f"| {md_cell(c.name)} | {c.status.value} | {c.points} | {value} | {note} |")
    return "\n".join(lines) + "\n"


def has_blind_conditions(checks: list[Check]) -> bool:
    return any(c.status is Status.NO_DATA for c in checks)


MAX_HOURS = 168


class DataCheckSource[C](Protocol):
    """What a source supplies to the data-check command: a module with these three functions."""

    def check_all(
        self, client: NewRelicClient, account_id: int, monitors: list[IRMonitor], hours: int
    ) -> list[C]: ...

    def render_report(self, checks: list[C], hours: int) -> str: ...

    def has_blind_conditions(self, checks: list[C]) -> bool: ...


def read_monitors(alerts: Path) -> list[IRMonitor]:
    """The IR files that `convert` wrote under `alerts/ir`."""
    return [
        IRMonitor.model_validate_json(p.read_text()) for p in sorted((alerts / "ir").glob("*.json"))
    ]


def run_data_check[C](
    alerts: Path,
    nr_account_id: int,
    nr_region: str,
    hours: int,
    out: Path | None,
    source: DataCheckSource[C],
    echo: Callable[[str], None],
    echo_error: Callable[[str], None],
    *,
    open_client: Callable[[str], AbstractContextManager[NewRelicClient]],
    read_ir: Callable[[Path], list[IRMonitor]] = read_monitors,
) -> int:
    """The body of every data-check command: read the IR, ask New Relic, report.

    Returns the exit code: 0 when data arrives, 1 for a bad argument or a failure, 2 when a
    converted condition is blind. `open_client` and `read_ir` belong to the caller so a test (or a
    source with its own file limits) can replace them.
    """
    if nr_account_id <= 0 or not 1 <= hours <= MAX_HOURS:
        echo_error("Error: --nr-account-id is required and --hours must be 1 to 168")
        return 1
    try:
        monitors = read_ir(alerts)
        with open_client(nr_region) as client:
            checks = source.check_all(client, nr_account_id, monitors, hours)
    except (NewRelicAPIError, GuardError, MissingSecretError, ValueError, OSError) as exc:
        echo_error(f"Error: {printable(str(exc), 300)}")
        return 1
    text = source.render_report(checks, hours)
    echo(text)
    if out:
        out.write_text(text)
    return 2 if source.has_blind_conditions(checks) else 0
