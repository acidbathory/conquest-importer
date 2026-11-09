"""Commands for Grafana notifications and the data path (read side; nothing is created).

- extract-grafana-notifications: read contact points, policies, mute timings, templates and
  ruler rules from a stack, read-only, with secrets dropped at read time.
- plan-grafana-datapath: write guidance files for sending Prometheus and Loki data to New Relic.
- check-grafana-data: ask New Relic (read-only) whether the data behind converted conditions
  arrives.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import Annotated

import typer

from importer.core.apply import NewRelicClient
from importer.core.dashboard_ir import IRDashboard
from importer.core.data_check import run_data_check
from importer.core.ids import MAX_INPUT_BYTES
from importer.core.ir import IRMonitor
from importer.core.secrets import require_env
from importer.core.text import printable
from importer.sources.grafana.datapath import check as data_check
from importer.sources.grafana.datapath import guide, needs
from importer.sources.grafana.datapath import plan as datapath
from importer.sources.grafana.extract import GrafanaError, grafana_url
from importer.sources.grafana.extract import api_token as grafana_api_token
from importer.sources.grafana.extract import validate_host as grafana_host
from importer.sources.grafana.notifications.extract import (
    extract_notifications,
    notifications_getter,
)
from importer.sources.grafana.notifications.loader import load_export

MAX_IR_FILES = 50_000


def _extract(
    out: Annotated[Path, typer.Option(help="New folder for the export")] = Path(
        "out/grafana-notifications"
    ),
    allow_host: Annotated[
        str | None,
        typer.Option(
            "--allow-host",
            help="Host name of a self-hosted Grafana (https only); must equal the host in "
            "GRAFANA_URL. Without it only https://<stack>.grafana.net is accepted.",
        ),
    ] = None,
) -> None:
    """Read contact points, policies, mute timings, templates and ruler rules from Grafana.

    Read-only: GET calls on a fixed list of paths, never with a query string, so contact points
    are never decrypted and Grafana returns [REDACTED] for every stored secret. Set GRAFANA_URL
    and GRAFANA_SA_TOKEN (a service account token that can read alert provisioning data and
    data sources) in .env or the environment.

    What is written: contact point type, name, uid, the names of the settings that hold a value,
    option values of a known type, the host of a non-secret URL, and for email the addresses
    (contact-points.json only); the policy tree and mute timings; template names, a hash and
    the names they define; data source type and uid; rule files with the expression and the
    labels, where a value that looks like a secret is replaced. Not written: secret values,
    header values, URL paths and queries, template text, rule annotations. Files are readable by
    the owner only. Nothing is written if the run fails. Use a new folder, not the one
    `extract-grafana` wrote.
    """
    try:
        token = grafana_api_token()
        base = grafana_host(grafana_url(), allow_host)
        with notifications_getter(token, base, allow_host=allow_host) as get:
            result = extract_notifications(get, out)
    except (GrafanaError, ValueError, OSError) as exc:
        typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        f"Read {result.contact_points} contact points, {result.mute_timings} mute timings, "
        f"{result.templates} templates, {result.datasources} data sources and "
        f"{result.rule_groups} rule groups."
    )
    for warning in result.warnings:
        typer.echo(f"  Warning: {printable(warning, 300)}", err=True)
    typer.echo(
        f"Written to {printable(str(out), 200)}. Email addresses are in contact-points.json."
    )


def _read_ir(alerts: Path) -> list[IRMonitor]:
    """The saved IR of a `convert` run, with a cap on the number and size of files."""
    files = sorted((alerts / "ir").glob("*.json"))[:MAX_IR_FILES]
    if any(f.stat().st_size > MAX_INPUT_BYTES for f in files):
        raise ValueError("an IR file is larger than the importer accepts")
    return [IRMonitor.model_validate_json(f.read_text()) for f in files]


def _read_dashboards(folder: Path) -> list[IRDashboard]:
    """The saved dashboard IR of `convert-grafana-dashboards`, with the same caps."""
    files = sorted((folder / "dashboard-ir").glob("*.json"))[:MAX_IR_FILES]
    if any(f.stat().st_size > MAX_INPUT_BYTES for f in files):
        raise ValueError("a dashboard IR file is larger than the importer accepts")
    return [IRDashboard.model_validate_json(f.read_text()) for f in files]


def _update_report(folder: Path, section: str) -> None:
    report = folder / "report.md"
    if not report.is_file() or report.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"{folder.name}/report.md is missing or too large to update")
    report.write_text(needs.replace_section(report.read_text(), section))


def _plan(
    region: Annotated[str, typer.Option(help="us, eu or jp: the New Relic data region")] = "us",
    out: Annotated[Path, typer.Option(help="New folder for the guide")] = Path(
        "out/grafana-datapath"
    ),
    prometheus_server: Annotated[
        str, typer.Option(help="Name that marks this sender's data (prometheus_server)")
    ] = "prometheus-1",
    export: Annotated[
        Path | None, typer.Option(help="Folder written by `extract-grafana-notifications`")
    ] = None,
    alerts: Annotated[
        Path | None, typer.Option(help="Output folder of `convert` (has ir/), to fill the queries")
    ] = None,
    hours: Annotated[int, typer.Option(help="Window in the listed queries (1 to 168)")] = 24,
    dashboards: Annotated[
        Path | None,
        typer.Option(help="Output folder of `convert-grafana-dashboards` (has dashboard-ir/)"),
    ] = None,
    update_report: Annotated[
        bool,
        typer.Option(
            "--update-report",
            help="Add a 'Data path coverage' section to report.md in --alerts and --dashboards",
        ),
    ] = False,
) -> None:
    """Write guidance for sending Prometheus and Loki data to New Relic. Sends nothing.

    Writes a Prometheus remote_write block, Grafana Alloy snippets, the naming consequences
    and the queries a data check would run. The license key is a placeholder or an environment
    variable name in every file: this command never reads or writes a key. With --alerts and
    --dashboards it also writes datapath-plan.md: the steps, the metric families and how they
    arrive in New Relic, and for each converted rule and tile the step after which it has data.
    """
    try:
        sources = load_export(export).datasources if export else []
        metrics, attributes = (
            data_check.targets_from_monitors(_read_ir(alerts)) if alerts else ([], [])
        )
        rule_list = needs.rule_items(_read_ir(alerts)) if alerts else []
        tile_list = needs.tile_items(_read_dashboards(dashboards)) if dashboards else []
        items = rule_list + tile_list
        metrics = sorted({*metrics, *(f.name for f in needs.families(items))})
        judged = needs.judge(items, sources)
        files = datapath.build_files(
            region,
            prometheus_server=prometheus_server,
            metrics=metrics,
            log_attributes=attributes,
            datasources=sources,
            hours=hours,
        )
        files |= guide.plan_files(region, prometheus_server, items, judged, sources)
        coverage = {
            noun: needs.coverage([j for j in judged if j.item.kind == kind])
            for noun, kind in (("rules", "rule"), ("tiles", "tile"))
        }
        if update_report:
            if alerts:
                _update_report(alerts, needs.coverage_section({"rules": coverage["rules"]}))
            if dashboards:
                _update_report(dashboards, needs.coverage_section({"tiles": coverage["tiles"]}))
        datapath.write_files(files, out)
    except (ValueError, OSError) as exc:
        typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"Wrote {len(files)} files to {printable(str(out), 200)}. No data was sent.")
    for noun, cov in coverage.items():
        if cov.total:
            typer.echo(
                f"{noun.capitalize()}: {cov.known} of {cov.converted} converted with a known "
                f"data path ({cov.total} read)."
            )
    typer.echo("Put the New Relic ingest license key where the files say; they hold a placeholder.")


@contextmanager
def new_relic_client(region: str) -> Iterator[NewRelicClient]:
    """The read client for the data check; tests replace this with a fake."""
    with NewRelicClient(require_env("NEW_RELIC_API_KEY"), region) as client:
        yield client


def _check(
    alerts: Annotated[Path, typer.Option(help="Output folder of `convert` (has ir/)")] = Path(
        "out/grafana-alerts"
    ),
    nr_account_id: Annotated[int, typer.Option(help="New Relic account id to query")] = 0,
    nr_region: Annotated[str, typer.Option(help="us or eu")] = "us",
    hours: Annotated[int, typer.Option(help="How far back to look (1 to 168)")] = 24,
    out: Annotated[Path | None, typer.Option(help="Also write the markdown report here")] = None,
) -> None:
    """Check that the data behind each converted Grafana condition reaches New Relic (read-only).

    A condition on a metric that never arrives looks healthy and never fires. Exits with code 2
    if any converted condition has no data (or a filter attribute that is missing).
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
        open_client=new_relic_client,
        read_ir=_read_ir,
    )
    if code:
        raise typer.Exit(code=code)


def register(app: typer.Typer) -> None:
    app.command("extract-grafana-notifications")(_extract)
    app.command("plan-grafana-datapath")(_plan)
    app.command("check-grafana-data")(_check)
