"""Commands for the Datadog source: `check-datadog-data` and `scaffold-datadog-names`.

Both are offline or read-only. The source itself is file-first (`convert --source datadog`), so
there is no extractor and no Datadog credential. `check-datadog-data` asks New Relic with SELECT
queries whether the data behind the converted conditions arrives; `scaffold-datadog-names` lists the
Datadog metrics and tags an export uses and writes a name-map.json skeleton to fill in.
"""

import json
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from functools import partial
from pathlib import Path
from typing import Annotated

import typer

from importer.core.apply import NewRelicClient
from importer.core.data_check import run_data_check
from importer.core.secrets import require_env
from importer.core.text import printable
from importer.sources.datadog import data_check
from importer.sources.datadog.models import load_monitors
from importer.sources.datadog.names import scaffold


@contextmanager
def new_relic_client(region: str) -> Iterator[NewRelicClient]:
    """The read client for the data check; tests replace this with a fake."""
    with NewRelicClient(require_env("NEW_RELIC_API_KEY"), region) as client:
        yield client


def _open(region: str) -> AbstractContextManager[NewRelicClient]:
    return new_relic_client(region)


def _check(
    alerts: Annotated[Path, typer.Option(help="Output folder of `convert` (has ir/)")] = Path(
        "out/datadog-alerts"
    ),
    nr_account_id: Annotated[int, typer.Option(help="New Relic account id to query")] = 0,
    nr_region: Annotated[str, typer.Option(help="us or eu")] = "us",
    hours: Annotated[int, typer.Option(help="How far back to look (1 to 168)")] = 24,
    out: Annotated[Path | None, typer.Option(help="Also write the markdown report here")] = None,
) -> None:
    """Check that the data behind each converted Datadog condition reaches New Relic (read-only).

    Datadog metric and tag names are assumed to arrive unchanged and have not been seen arriving;
    this is how they get confirmed. When a metric is missing the report lists metric names that did
    arrive with the same start. Exits with code 2 if a converted condition has no data.
    """
    code = run_data_check(
        alerts,
        nr_account_id,
        nr_region,
        hours,
        out,
        data_check,
        typer.echo,
        partial(typer.echo, err=True),
        open_client=_open,
    )
    if code:
        raise typer.Exit(code=code)


def _scaffold(
    source: Annotated[
        Path, typer.Option("--in", help="File or folder of exported Datadog monitor JSON")
    ] = Path("datadog-monitors"),
    out: Annotated[Path, typer.Option(help="Where to write the name-map skeleton")] = Path(
        "name-map.json"
    ),
) -> None:
    """List the Datadog metrics and tags in an export that no name-map.json entry covers.

    Offline. Writes a skeleton in which every metric maps to the same name on the Metric event;
    edit the names, unit_scale and kind of each row, then keep the file next to the export.
    """
    if out.exists():
        typer.echo(f"Error: {printable(str(out), 200)} exists; choose a new file", err=True)
        raise typer.Exit(code=1)
    try:
        result = scaffold(load_monitors(source))
    except (OSError, ValueError) as exc:
        typer.echo(f"Error reading {source}: {printable(str(exc), 300)}", err=True)
        raise typer.Exit(code=1) from None
    out.write_text(json.dumps(result.skeleton, indent=2) + "\n")
    typer.echo(f"{len(result.metrics)} unmapped metrics and {len(result.tags)} tag keys listed.")
    typer.echo(f"Wrote {out}. Edit it, then run convert again with it beside the export.")


def register(app: typer.Typer) -> None:
    app.command("check-datadog-data")(_check)
    app.command("scaffold-datadog-names")(_scaffold)
