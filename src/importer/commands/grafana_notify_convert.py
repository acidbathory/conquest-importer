"""Command: convert-grafana-notifications.

Reads the folder `extract-grafana-notifications` wrote and the output of `convert --source
grafana`, and writes New Relic notification plans, muting rules, Terraform and a report. Offline:
no network, no key, no secret. The plan folder can be read by `apply-notifications`; plans with
secrets or a secure URL are meant for the Terraform file (see the report).
"""

import json
from pathlib import Path
from typing import Annotated

import typer

from importer.core.emit.common import CannotEmit
from importer.core.ids import MAX_INPUT_BYTES
from importer.core.ir import IRMonitor
from importer.core.text import printable
from importer.sources.grafana.models import GrafanaRule, load_rules
from importer.sources.grafana.notifications.loader import load_export
from importer.sources.grafana.notify_convert.convert import Inputs, convert_notifications
from importer.sources.grafana.notify_convert.destmap import MapError, load_map
from importer.sources.grafana.notify_convert.outputs import output_files, write_files

MAX_IR_FILES = 50_000


def read_monitors(alerts: Path) -> list[IRMonitor]:
    """The saved IR of a `convert` run, with a cap on the number and size of files."""
    folder = alerts / "ir"
    if not folder.is_dir():
        raise ValueError(f"{alerts} has no ir/ folder; pass the output folder of `convert`")
    files = sorted(folder.glob("*.json"))[:MAX_IR_FILES]
    if any(f.stat().st_size > MAX_INPUT_BYTES for f in files):
        raise ValueError("an IR file is larger than the importer accepts")
    try:
        return [IRMonitor.model_validate_json(f.read_text()) for f in files]
    except (RecursionError, ValueError):  # a ValidationError would quote the file's text
        raise ValueError("an IR file in ir/ cannot be read; run `convert` again") from None


def _convert(
    source: Path,
    alerts: Path,
    out: Path,
    rules: Path | None,
    destinations_map: Path | None,
    prefix: str,
) -> None:
    export = load_export(source)
    loaded: list[GrafanaRule] = load_rules(rules) if rules else []
    dest_map = load_map(destinations_map) if destinations_map else None
    result = convert_notifications(
        Inputs(
            export=export,
            monitors=read_monitors(alerts),
            rules=loaded,
            dest_map=dest_map,
            prefix=prefix,
        )
    )
    write_files(output_files(result), out)
    n = result.counts
    typer.echo(
        f"Read {n.contact_points} contact points; routed {n.rules_routed} rules. Planned "
        f"{n.workflows} workflows, {n.destinations} destinations and {n.muting_rules} muting "
        f"rules (all disabled). {n.secrets} secrets must be supplied. "
        f"See {printable(str(out / 'report.md'), 200)}"
    )
    for problem in export.problems:
        typer.echo(f"  Warning: {printable(problem, 200)}", err=True)
    for violation in result.violations:
        typer.echo(f"  Problem: {printable(violation, 300)}", err=True)
    if result.violations:
        raise typer.Exit(code=2)


def convert_grafana_notifications(
    source: Annotated[
        Path,
        typer.Option("--in", help="Folder written by `extract-grafana-notifications`"),
    ] = Path("out/grafana-notifications"),
    alerts: Annotated[
        Path,
        typer.Option("--alerts", help="Output folder of `convert --source grafana` (has ir/)"),
    ] = Path("out/grafana-alerts"),
    out: Annotated[Path, typer.Option(help="New output folder")] = Path(
        "out/grafana-notification-plans"
    ),
    rules: Annotated[
        Path | None,
        typer.Option(
            help="The rules `convert` read (file or folder). Gives each rule's labels and "
            "folder, so a policy that matches on a label can be decided; without it such "
            "rules are reported as undecidable"
        ),
    ] = None,
    destinations_map: Annotated[
        Path | None,
        typer.Option(
            "--destinations-map",
            help="JSON map from a contact point (name or uid) to an email address list, the "
            "name of a webhook URL secret, the name of a PagerDuty key secret, or skip",
        ),
    ] = None,
    prefix: Annotated[str, typer.Option(help="Prefix for created object names")] = "Imported:",
) -> None:
    """Plan New Relic destinations, workflows and muting rules from Grafana notifications.

    Contact points become destinations (email, webhook, the webhook-template services,
    PagerDuty) or manual steps (Slack, Teams, Jira, SNS, Pushover) or are listed as having no
    equivalent. The policy tree becomes one workflow per route, filtered on the converted
    conditions and on the labels of the query. Mute timings that fit a New Relic muting rule
    become one. Nothing is created and no secret is read: URLs and keys are names that you
    supply when you apply the Terraform file. Offline.
    """
    try:
        _convert(source, alerts, out, rules, destinations_map, prefix)
    except (OSError, ValueError, MapError, CannotEmit, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {printable(str(exc), 300)}", err=True)
        raise typer.Exit(code=1) from None


def register(app: typer.Typer) -> None:
    app.command("convert-grafana-notifications")(convert_grafana_notifications)
