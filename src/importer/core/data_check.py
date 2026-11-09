"""Shared pieces of a data check: does the data behind a converted condition exist?

A condition on data that never arrives looks healthy and never fires. Each source builds its own
queries from names its converter produced; this module holds the result type, the single place
that runs a count query, and the report. Only read queries are sent.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from importer.core.apply import NewRelicClient
from importer.core.report import md_cell

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
