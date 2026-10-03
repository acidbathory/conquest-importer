"""Command line entry point. Commands are added as each pipeline stage lands."""

import json
import logging
import os
from pathlib import Path
from typing import Annotated

import typer

from importer.core.apply import (
    GuardError,
    NewRelicAPIError,
    NewRelicClient,
    apply_plan,
    check_guards,
    get_account_name,
    load_plan,
)
from importer.core.dashboard_ir import IRDashboard
from importer.core.dashboard_report import build_dashboard_report
from importer.core.emit.common import CannotEmit
from importer.core.emit.dashboard import dashboard_id, dashboards_terraform, nerdgraph_dashboard
from importer.core.emit.nerdgraph import nerdgraph_payload
from importer.core.emit.terraform import terraform_file
from importer.core.ir import Confidence, IRMonitor
from importer.core.report import build_report
from importer.core.secrets import (
    DatadogCredentials,
    MissingSecretError,
    RedactingFilter,
    require_env,
)
from importer.core.stats import build_stats, load_run, render_markdown
from importer.core.text import printable
from importer.sources import DASHBOARD_REGISTRY, REGISTRY
from importer.sources.cloudwatch.ingest import IngestConfig, ingest_files
from importer.sources.cloudwatch.models import load_alarms
from importer.sources.cloudwatch.translate import alarm_namespaces, load_namespace_map
from importer.sources.datadog.client import DatadogAPIError, DatadogClient, InvalidSiteError

app = typer.Typer(
    help="Convert observability alerts from other platforms to New Relic alert conditions.",
    no_args_is_help=True,
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
        str, typer.Option("--source", help="Source platform: " + ", ".join(sorted(REGISTRY)))
    ] = "datadog",
) -> None:
    """Convert extracted monitors or alarms to IR, NerdGraph payloads, Terraform and a report.

    For CloudWatch, --in is a file (or folder) holding the JSON from
    `aws cloudwatch describe-alarms`. Offline and read-only: nothing is sent anywhere.
    """
    if source_system not in REGISTRY:
        typer.echo(f"Error: --source must be one of {sorted(REGISTRY)}", err=True)
        raise typer.Exit(code=1)
    try:
        monitors = REGISTRY[source_system].convert(source)
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
def stats(
    runs: Annotated[
        list[Path], typer.Argument(help="One or more `convert` output folders (each has ir/)")
    ],
    top: Annotated[int, typer.Option(help="How many reasons to list per table")] = 10,
    out: Annotated[Path | None, typer.Option(help="Also write the markdown here")] = None,
) -> None:
    """Summarise conversion rates and the most common reasons across one or more runs.

    Read-only and offline. Pass every export once: the same export given twice is counted twice.
    """
    try:
        loaded = [load_run(folder) for folder in runs]
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {printable(str(exc), 300)}", err=True)
        raise typer.Exit(code=1) from None
    text = render_markdown(loaded, build_stats(loaded), top)
    typer.echo(text, nl=False)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)


@app.command("convert-dashboards")
def convert_dashboards(
    source: Annotated[
        Path, typer.Option("--in", help="File or folder of exported dashboard JSON")
    ] = Path("out/cloudwatch-dashboards"),
    out: Annotated[Path, typer.Option(help="Output directory")] = Path("out/dashboards"),
    source_system: Annotated[
        str,
        typer.Option("--source", help="Source platform: " + ", ".join(sorted(DASHBOARD_REGISTRY))),
    ] = "cloudwatch",
) -> None:
    """Convert exported dashboards to IR, NerdGraph payloads, Terraform and a report.

    Offline and read-only: nothing is sent anywhere. Use a different --out than `convert`:
    both write a Terraform provider block, which must not share a folder.
    """
    if source_system not in DASHBOARD_REGISTRY:
        typer.echo(f"Error: --source must be one of {sorted(DASHBOARD_REGISTRY)}", err=True)
        raise typer.Exit(code=1)
    try:
        dashboards = DASHBOARD_REGISTRY[source_system].convert(source)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error reading {source}: {printable(str(exc), 300)}", err=True)
        raise typer.Exit(code=1) from None
    if not dashboards:
        typer.echo(f"No dashboards found in {source}", err=True)
        raise typer.Exit(code=1)

    (out / "dashboard-ir").mkdir(parents=True, exist_ok=True)
    (out / "nerdgraph").mkdir(parents=True, exist_ok=True)
    emitted: list[tuple[int, IRDashboard]] = []
    for d in dashboards:
        ident = dashboard_id(d)
        (out / "dashboard-ir" / f"{ident}.json").write_text(d.model_dump_json(indent=2) + "\n")
        try:
            payload = nerdgraph_dashboard(d)
        except CannotEmit:
            continue
        emitted.append((ident, d))
        (out / "nerdgraph" / f"{ident}.json").write_text(json.dumps(payload, indent=2) + "\n")
    (out / "dashboards.tf").write_text(dashboards_terraform(emitted))
    keys = {d.source_key for _, d in emitted}
    (out / "report.md").write_text(build_dashboard_report(dashboards, keys))
    typer.echo(
        f"Read {len(dashboards)} dashboards, emitted {len(emitted)}. See {out / 'report.md'}"
    )


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


@app.command("ingest-setup")
def ingest_setup(
    out: Annotated[Path, typer.Option(help="Output directory")] = Path("out/ingest"),
    aws_region: Annotated[str, typer.Option(help="AWS region to stream from")] = "us-east-1",
    nr_region: Annotated[str, typer.Option(help="New Relic region: us or eu")] = "us",
    name_prefix: Annotated[str, typer.Option(help="Prefix for created resources")] = (
        "newrelic-metrics"
    ),
    namespaces: Annotated[
        str | None, typer.Option(help="Comma-separated CloudWatch namespaces to stream")
    ] = None,
    alarms: Annotated[
        Path | None, typer.Option(help="describe-alarms JSON: stream only the namespaces it uses")
    ] = None,
) -> None:
    """Generate Terraform that streams CloudWatch metrics into New Relic (Metric Streams).

    Writes files only; nothing is deployed. The New Relic license key is never written to a
    file: pass it when applying with TF_VAR_newrelic_license_key.
    """
    try:
        chosen: list[str] = []
        if namespaces:
            chosen += [n.strip() for n in namespaces.split(",") if n.strip()]
        if alarms:
            metric, _ = load_alarms(alarms)
            chosen += sorted(alarm_namespaces(metric))
        if not chosen:
            chosen = sorted(load_namespace_map())
        config = IngestConfig.model_validate(
            {
                "name_prefix": name_prefix,
                "aws_region": aws_region,
                "nr_region": nr_region,
                "namespaces": chosen,
            }
        )
        files = ingest_files(config)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
        raise typer.Exit(code=1) from None
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text)
    typer.echo(f"Wrote {', '.join(files)} to {out}; streaming {len(config.namespaces)} namespaces")
    typer.echo("To deploy (review the plan first):")
    typer.echo("  export TF_VAR_newrelic_license_key=...   # never commit this")
    typer.echo(
        "  export TF_VAR_newrelic_account_id=...    # and NEW_RELIC_API_KEY for the provider"
    )
    typer.echo("  terraform init && terraform plan")
    typer.echo("Note: Terraform state will contain the license key. Protect it.")


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
