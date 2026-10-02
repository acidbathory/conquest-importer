"""Command line entry point. Commands are added as each pipeline stage lands."""

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from importer.apply import (
    GuardError,
    NewRelicAPIError,
    NewRelicClient,
    apply_plan,
    check_guards,
    get_account_name,
    load_plan,
    printable,
)
from importer.emit.common import CannotEmit
from importer.emit.nerdgraph import nerdgraph_payload
from importer.emit.terraform import terraform_file
from importer.extract.datadog import DatadogAPIError, DatadogClient, InvalidSiteError
from importer.models.cloudwatch import load_alarms
from importer.models.ir import Confidence, IRMonitor
from importer.models.source import DatadogMonitor
from importer.report import build_report
from importer.secrets import (
    DatadogCredentials,
    MissingSecretError,
    RedactingFilter,
    require_env,
)
from importer.translate.cloudwatch import load_namespace_map, translate_alarm, translate_composite
from importer.translate.monitors import load_metric_map, translate_monitor


def _load_datadog(path: Path) -> list[IRMonitor]:
    metric_map = load_metric_map()
    return [
        translate_monitor(DatadogMonitor.model_validate_json(p.read_text()), metric_map)
        for p in sorted(path.glob("*.json"))
    ]


def _load_cloudwatch(path: Path) -> list[IRMonitor]:
    prefixes = load_namespace_map()
    metric, composite = load_alarms(path)
    return [translate_alarm(a, prefixes) for a in metric] + [
        translate_composite(c) for c in composite
    ]


LOADERS: dict[str, Callable[[Path], list[IRMonitor]]] = {
    "datadog": _load_datadog,
    "cloudwatch": _load_cloudwatch,
}

app = typer.Typer(
    help="Convert Datadog monitors to New Relic alert conditions.", no_args_is_help=True
)


@app.command()
def version() -> None:
    """Print the tool version."""
    typer.echo("conquest-importer 0.1.0")


@app.command()
def convert(
    source: Annotated[
        Path, typer.Option("--in", help="Directory of extracted monitor JSON")
    ] = Path("out/datadog"),
    out: Annotated[Path, typer.Option(help="Output directory")] = Path("out/converted"),
    source_system: Annotated[
        str, typer.Option("--source", help="datadog or cloudwatch")
    ] = "datadog",
) -> None:
    """Convert extracted monitors or alarms to IR, NerdGraph payloads, Terraform and a report.

    For CloudWatch, --in is a file (or folder) holding the JSON from
    `aws cloudwatch describe-alarms`. Offline and read-only: nothing is sent anywhere.
    """
    if source_system not in LOADERS:
        typer.echo(f"Error: --source must be one of {sorted(LOADERS)}", err=True)
        raise typer.Exit(code=1)
    try:
        monitors = LOADERS[source_system](source)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error reading {source}: {printable(str(exc), 300)}", err=True)
        raise typer.Exit(code=1) from None
    if not monitors:
        typer.echo(f"No monitors or alarms found in {source}", err=True)
        raise typer.Exit(code=1)

    (out / "ir").mkdir(parents=True, exist_ok=True)
    (out / "nerdgraph").mkdir(parents=True, exist_ok=True)
    final: list[IRMonitor] = []
    emitted: list[IRMonitor] = []
    for m in monitors:
        try:
            payload = nerdgraph_payload(m)
            terraform_file([m])  # fail here, not halfway through the combined file
        except (CannotEmit, ValueError) as exc:
            if m.confidence in (Confidence.EXACT, Confidence.APPROXIMATE):
                # Converted in principle but New Relic cannot take it: say so in the label.
                m = m.model_copy(
                    update={
                        "confidence": Confidence.MANUAL,
                        "reasons": [*m.reasons, f"not emitted: {exc}"],
                    }
                )
            final.append(m)
            (out / "ir" / f"{m.source_id}.json").write_text(m.model_dump_json(indent=2) + "\n")
            continue
        final.append(m)
        emitted.append(m)
        (out / "ir" / f"{m.source_id}.json").write_text(m.model_dump_json(indent=2) + "\n")
        (out / "nerdgraph" / f"{m.source_id}.json").write_text(json.dumps(payload, indent=2) + "\n")
    (out / "main.tf").write_text(terraform_file(emitted))
    report = build_report(final, {m.source_id for m in emitted}, source_system)
    (out / "report.md").write_text(report)
    typer.echo(f"Read {len(monitors)} monitors, emitted {len(emitted)}. See {out / 'report.md'}")


@app.command()
def apply(
    source: Annotated[
        Path, typer.Option("--in", help="Converted output directory (contains ir/)")
    ] = Path("out/converted"),
    account_id: Annotated[int, typer.Option(help="New Relic TEST account id")] = 0,
    region: Annotated[str, typer.Option(help="us or eu")] = "us",
    policy_name: Annotated[str, typer.Option(help="Alert policy to create or reuse")] = (
        "Imported from Datadog"
    ),
    do_apply: Annotated[
        bool, typer.Option("--apply", help="Actually create conditions (default: dry run)")
    ] = False,
    confirm_account: Annotated[
        int | None, typer.Option(help="Repeat the account id to confirm --apply")
    ] = None,
) -> None:
    """Create the converted conditions in a New Relic account.

    Dry run by default: prints the plan and sends nothing. To write, you need --apply,
    --confirm-account, and the account listed in NEW_RELIC_ALLOWED_ACCOUNT_IDS.
    Conditions are created disabled.
    """
    if account_id <= 0:
        typer.echo("Error: --account-id is required", err=True)
        raise typer.Exit(code=1)
    try:
        plan = load_plan(source / "ir")
    except (OSError, ValueError) as exc:
        typer.echo(f"Error reading {source / 'ir'}: {printable(str(exc), 200)}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(f"Account {account_id} ({region}), policy {printable(policy_name)!r}")
    typer.echo(f"{len(plan.items)} conditions to create, {plan.skipped} monitors not emittable")
    for item in plan.items:
        typer.echo(f"  {item.source_id}  {printable(item.name, 50):50}  {printable(item.nrql, 90)}")
    if not do_apply:
        typer.echo("\nDRY RUN: nothing was sent. Add --apply --confirm-account to create them.")
        return

    try:
        check_guards(account_id, confirm_account, os.environ.get("NEW_RELIC_ALLOWED_ACCOUNT_IDS"))
        key = require_env("NEW_RELIC_API_KEY")
        with NewRelicClient(key, region) as client:
            typer.echo(f"Account name: {printable(get_account_name(client, account_id))}")
            result = apply_plan(client, account_id, policy_name, plan)
    except (GuardError, MissingSecretError, NewRelicAPIError) as exc:
        typer.echo(f"Error: {printable(str(exc), 300)}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(
        f"\nCreated {len(result.created)}, already existed {len(result.skipped_existing)}, "
        f"failed {len(result.failed)}. Conditions are DISABLED: review, then enable."
    )
    for name, reason in result.failed:
        typer.echo(f"  FAILED {printable(name, 50)}: {printable(reason, 150)}", err=True)
    if result.failed:
        raise typer.Exit(code=1)


@app.command()
def extract(
    out: Annotated[Path, typer.Option(help="Directory for monitor JSON files")] = Path(
        "out/datadog"
    ),
) -> None:
    """Download all monitors from Datadog (read-only) and save one JSON file per monitor."""
    try:
        creds = DatadogCredentials.from_env()
        logging.basicConfig(level=logging.INFO)
        for handler in logging.getLogger().handlers:
            handler.addFilter(RedactingFilter([creds.api_key, creds.app_key]))
        out.mkdir(parents=True, exist_ok=True)
        count = 0
        with DatadogClient(creds) as client:
            for monitor in client.list_monitors():
                # File names use the numeric id only: monitor names are untrusted text.
                path = out / f"{monitor.id}.json"
                path.write_text(json.dumps(monitor.model_dump(mode="json"), indent=2) + "\n")
                count += 1
    except (MissingSecretError, InvalidSiteError, DatadogAPIError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"Saved {count} monitors to {out}")
