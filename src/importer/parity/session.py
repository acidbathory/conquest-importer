"""One `verify-parity` run: read the pairs, open both sides, judge, write; or watch."""

import re
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from importer.core.apply import GuardError, NewRelicAPIError, NewRelicClient
from importer.core.data_check import rows
from importer.core.secrets import MissingSecretError, require_env
from importer.core.text import printable
from importer.parity.model import Item, Pair, ParityError, Verdict
from importer.parity.newrelic import NewRelicReader, Rows, check_buckets
from importer.parity.pairs import build_pairs
from importer.parity.report import write_all
from importer.parity.runner import NrFetch, Settings, SourceFetch, run_once, watch
from importer.parity.source import open_reader
from importer.sources.grafana.dashboard_convert.name_map import (
    NameMap,
    NameMapError,
    active_name_map,
    load_name_map,
    merge_hints,
)

VAR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
EVERY = re.compile(r"(\d{1,4})([smh])")
EVERY_SECONDS = {"s": 1, "m": 60, "h": 3600}
MIN_EVERY = 60
MAX_HOURS = 168
OpenSource = Callable[[str], AbstractContextManager[SourceFetch]]
OpenNewRelic = Callable[[str, int], AbstractContextManager[Rows]]


@dataclass
class Options:
    converted: Path
    source_prom: str
    nr_account_id: int
    export: Path | None = None
    source_loki: str | None = None
    nr_region: str = "us"
    hours: float = 6
    step: int = 60
    tolerance: float = 0.05
    out: Path = Path("parity")
    watch: bool = False
    every: str = "10m"
    variables: list[str] = field(default_factory=list)
    name_map: Path | None = None
    hints: list[Path] = field(default_factory=list)
    allow_host: str | None = None


def parse_every(text: str) -> int:
    found = EVERY.fullmatch(text.strip().lower())
    seconds = int(found[1]) * EVERY_SECONDS[found[2]] if found else 0
    if seconds < MIN_EVERY:
        raise ParityError("--every must look like 10m or 1h and be at least 1m")
    return seconds


def parse_variables(entries: list[str]) -> dict[str, tuple[str, ...]]:
    found: dict[str, list[str]] = {}
    for entry in entries:
        name, sep, value = entry.partition("=")
        if not sep or not VAR_NAME.fullmatch(name):
            raise ParityError(f"--var must look like name=value, not {printable(entry, 60)!r}")
        found.setdefault(name, []).append(value)
    return {k: tuple(v) for k, v in found.items()}


def check_options(o: Options) -> None:
    if o.nr_account_id <= 0:
        raise ParityError("--nr-account-id is required")
    if not 0 < o.hours <= MAX_HOURS:
        raise ParityError(f"--hours must be above 0 and at most {MAX_HOURS}")
    if not 10 <= o.step <= 3600:
        raise ParityError("--step must be 10 to 3600 seconds")
    if not 0 < o.tolerance < 1:
        raise ParityError("--tolerance must be between 0 and 1, for example 0.05")
    check_buckets(o.hours, o.step)


def _names(o: Options) -> NameMap:
    try:
        names = load_name_map(o.name_map) if o.name_map else active_name_map()
        for hint in o.hints:
            names = merge_hints(names, load_name_map(hint))
    except NameMapError as exc:
        raise ParityError(printable(str(exc), 300)) from None
    return names


@contextmanager
def _source(spec: str, allow_host: str | None) -> Iterator[SourceFetch]:
    with open_reader(spec, allow_host=allow_host) as reader:
        yield reader


@contextmanager
def _new_relic(region: str, account: int) -> Iterator[Rows]:
    try:
        client = NewRelicClient(require_env("NEW_RELIC_API_KEY"), region)
    except (MissingSecretError, GuardError) as exc:
        raise ParityError(printable(str(exc), 300)) from None
    with client:

        def run(query: str) -> list[dict[str, Any]]:
            try:
                return rows(client, account, query)
            except (NewRelicAPIError, KeyError, TypeError) as exc:
                raise ParityError(f"New Relic: {printable(str(exc), 250)}") from None

        yield run


def verify(
    o: Options,
    echo: Callable[[str], None],
    *,
    open_source: OpenSource | None = None,
    open_nr: OpenNewRelic = _new_relic,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    max_cycles: int | None = None,
) -> int:
    """Returns 0 when nothing mismatched, 2 when something did, 1 for a failure (raises)."""
    check_options(o)
    every = parse_every(o.every) if o.watch else 0
    opener: OpenSource = open_source or (lambda spec: _source(spec, o.allow_host))
    pairs = build_pairs(o.converted, o.export, _names(o), parse_variables(o.variables))
    if not pairs:
        raise ParityError("no converted query with an original was found to compare")
    settings = Settings(o.hours, o.step, o.tolerance, variables=parse_variables(o.variables))
    last: list[Item] = []

    def cycle(now: int) -> None:
        with opener(o.source_prom) as prom, open_nr(o.nr_region, o.nr_account_id) as rows_fn:
            sources: dict[str, SourceFetch | None] = {"prometheus": prom, "loki": None}
            if o.source_loki:
                with opener(o.source_loki) as loki:
                    sources["loki"] = loki
                    last[:] = _run(pairs, now, settings, sources, rows_fn)
            else:
                last[:] = _run(pairs, now, settings, sources, rows_fn)
        stamp = datetime.fromtimestamp(now, UTC)
        paths = write_all(o.out, last, o.hours, o.step, o.tolerance, stamp)
        counts = {v: sum(i.outcome.verdict is v for i in last) for v in Verdict}
        line = ", ".join(f"{n} {v.value}" for v, n in counts.items() if n)
        echo(f"{stamp:%H:%M:%S} UTC: {len(last)} items: {line}. Report: {paths[1]}")

    if o.watch:
        echo(
            f"Watching every {o.every}; the report in {o.out} is rewritten each time. Ctrl-C stops."
        )
        watch(cycle, every, sleep=sleep, clock=clock, max_cycles=max_cycles)
        echo("Stopped.")
    else:
        cycle(int(clock()))
    echo(
        f"Read {o.out / 'parity.html'}. It means something only where both sides hold the same data."
    )
    return 2 if any(i.outcome.verdict is Verdict.MISMATCH for i in last) else 0


def _run(
    pairs: list[Pair],
    now: int,
    settings: Settings,
    sources: dict[str, SourceFetch | None],
    rows_fn: Rows,
) -> list[Item]:
    nr: NrFetch = NewRelicReader(rows_fn)
    return run_once(pairs, now, settings, sources, nr)
