"""Commands for the Azure Monitor source: `extract-azure` and `check-azure-data`.

Both are read-only. `extract-azure` lists alert rules, action groups, alert processing rules and
Prometheus rule groups of one subscription through Azure Resource Manager (see
`sources/azure/extract.py`); `check-azure-data` asks New Relic with SELECT queries whether the data
behind the converted conditions arrives. `convert --source azure` is the shared `convert` command.
"""

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
from importer.sources.azure import data_check
from importer.sources.azure.extract import (
    AzureError,
    Getter,
    extract,
    open_client,
    subscription_id,
)


def _session() -> AbstractContextManager[Getter]:
    """The signed-in read session; tests replace this with a fake."""
    return open_client()


@contextmanager
def new_relic_client(region: str) -> Iterator[NewRelicClient]:
    """The read client for the data check; tests replace this with a fake."""
    with NewRelicClient(require_env("NEW_RELIC_API_KEY"), region) as client:
        yield client


def _extract(
    subscription: Annotated[
        str | None,
        typer.Option(help="Subscription id (a UUID); default: AZURE_SUBSCRIPTION_ID"),
    ] = None,
    out: Annotated[Path, typer.Option(help="New folder for the export")] = Path("out/azure"),
) -> None:
    """Read Azure Monitor alert rules, action groups and processing rules from a subscription.

    Read-only: GET requests to management.azure.com only. Credentials come from the environment
    (AZURE_TENANT_ID, AZURE_CLIENT_ID and AZURE_CLIENT_SECRET, or AZURE_ACCESS_TOKEN); they are
    never stored. Webhook URLs, callback URLs, secure webhook ids and passwords are dropped as the
    data is read and replaced by a reference name. The export still holds subscription ids,
    resource names and email addresses: review it before sharing.
    """
    try:
        sub = subscription_id(subscription)
        with _session() as get:
            result = extract(get, sub, out)
    except (AzureError, ValueError, OSError) as exc:
        typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
        raise typer.Exit(code=1) from None
    found = ", ".join(f"{n} {k}" for k, n in result.counts.items())
    typer.echo(f"Read subscription {result.subscription}: {found}.")
    typer.echo(f"{result.secrets_dropped} secret values were dropped while reading.")
    for warning in result.warnings:
        typer.echo(f"  Could not read {printable(warning, 200)}", err=True)
    typer.echo("Next:")
    typer.echo(f"  importer convert --source azure --in {out} --out out/azure-alerts")
    typer.echo(
        "  importer check-azure-data --alerts out/azure-alerts --nr-account-id <id>"
        "   (are the metric names right? put corrections in name-map.json)"
    )


def _check(
    alerts: Annotated[Path, typer.Option(help="Output folder of `convert` (has ir/)")] = Path(
        "out/azure-alerts"
    ),
    nr_account_id: Annotated[int, typer.Option(help="New Relic account id to query")] = 0,
    nr_region: Annotated[str, typer.Option(help="us or eu")] = "us",
    hours: Annotated[int, typer.Option(help="How far back to look (1 to 168)")] = 24,
    out: Annotated[Path | None, typer.Option(help="Also write the markdown report here")] = None,
) -> None:
    """Check that the data behind each converted Azure condition reaches New Relic (read-only).

    Azure metric names are derived from the documented naming rule and not yet seen arriving;
    this is how they get confirmed. When a metric is missing the report lists the Azure metric
    names that did arrive for the same resource type. Exits with code 2 if a converted condition
    has no data.
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
        open_client=lambda region: new_relic_client(region),
    )
    if code:
        raise typer.Exit(code=code)


def register(app: typer.Typer) -> None:
    app.command("extract-azure")(_extract)
    app.command("check-azure-data")(_check)
