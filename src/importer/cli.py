"""Command line entry point. Commands are added as each pipeline stage lands."""

import json
import os
import sys
import webbrowser
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from importer.core import improve
from importer.core.apply import (
    GuardError,
    NewRelicAPIError,
    NewRelicClient,
    apply_plan,
    check_guards,
    get_account_name,
    load_plan,
)
from importer.core.apply_dashboards import apply_dashboards, load_dashboard_plan
from importer.core.apply_notifications import apply_notifications
from importer.core.dashboard_ir import IRDashboard
from importer.core.dashboard_report import build_dashboard_report
from importer.core.doctor import passed, render, run_checks
from importer.core.dotenv import load_dotenv_file
from importer.core.emit.common import CannotEmit
from importer.core.emit.dashboard import (
    TERRAFORM_HEADER,
    dashboard_id,
    dashboards_terraform,
    nerdgraph_dashboard,
)
from importer.core.emit.nerdgraph import nerdgraph_payload
from importer.core.emit.notifications import notifications_terraform
from importer.core.emit.terraform import terraform_file
from importer.core.ir import Confidence, IRMonitor
from importer.core.manifest import CreatedObject, Kind, Manifest, load_manifest, save_manifest
from importer.core.migration_summary import Kind as AssetKind
from importer.core.migration_summary import Summary, load_summary
from importer.core.notification_ir import NotificationPlan, build_plan
from importer.core.report import build_report, md_cell
from importer.core.report_html import PROFILES, render_report
from importer.core.rollback import rollback
from importer.core.secrets import (
    MissingSecretError,
    require_env,
)
from importer.core.stats import build_stats, load_run, render_markdown
from importer.core.text import printable
from importer.sources import DASHBOARD_REGISTRY, REGISTRY
from importer.sources.cloudwatch import validate as cw_validate
from importer.sources.cloudwatch.dashboard_models import load_dashboards
from importer.sources.cloudwatch.extract import AwsError, Reader, caller_account
from importer.sources.cloudwatch.extract import extract as extract_cloudwatch
from importer.sources.cloudwatch.ingest import IngestConfig, ingest_files
from importer.sources.cloudwatch.log_ingest import (
    LogIngestConfig,
    log_groups_from_ir,
    log_ingest_files,
)
from importer.sources.cloudwatch.logfilter import attach_log_filters, load_metric_filters
from importer.sources.cloudwatch.models import load_alarms
from importer.sources.cloudwatch.notifications import load_topics, routing, translate_topic
from importer.sources.cloudwatch.streaming import streaming_inputs
from importer.sources.cloudwatch.translate import alarm_namespaces, load_namespace_map
from importer.sources.gcp import data_check as gcp_data
from importer.sources.gcp.extract import GcpError, access_token, http_getter
from importer.sources.gcp.extract import extract as extract_gcp
from importer.sources.gcp.link_terraform import link_terraform, services_for

app = typer.Typer(
    help="Convert observability alerts from other platforms to New Relic alert conditions.",
    no_args_is_help=True,
)


@app.callback()
def _load_settings() -> None:
    """Pick up settings from a .env file in the current folder (never overrides the environment)."""
    if os.environ.get("IMPORTER_NO_DOTENV"):
        return
    names = load_dotenv_file()
    if names:
        typer.echo(f"Loaded {len(names)} setting(s) from .env", err=True)


@app.command()
def version() -> None:
    """Print the tool version."""
    typer.echo("conquest-importer 0.1.0")


@app.command()
def convert(
    source: Annotated[
        Path, typer.Option("--in", help="Directory of extracted monitor JSON")
    ] = Path("out/cloudwatch"),
    out: Annotated[Path, typer.Option(help="Output directory")] = Path("out/converted"),
    source_system: Annotated[
        str, typer.Option("--source", help="Source platform: " + ", ".join(sorted(REGISTRY)))
    ] = "cloudwatch",
) -> None:
    """Convert extracted alarms to IR, NerdGraph payloads, Terraform and a report.

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
def doctor(
    region: Annotated[str, typer.Option(help="AWS region to check")] = "us-east-1",
    profile: Annotated[str | None, typer.Option(help="AWS profile name")] = None,
    source: Annotated[str, typer.Option(help="cloudwatch or gcp: which sign-in to check")] = (
        "cloudwatch"
    ),
) -> None:
    """Check that the tools and sign-in a run needs are in place. Read-only."""
    if source not in ("cloudwatch", "gcp"):
        typer.echo("Error: --source must be cloudwatch or gcp", err=True)
        raise typer.Exit(code=1)
    try:
        checks = run_checks(region, profile, source=source)
    except ValueError as exc:
        typer.echo(f"Error: {printable(str(exc), 200)}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(render(checks), nl=False)
    if not passed(checks):
        raise typer.Exit(code=1)


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
        "Imported by Conquest Importer"
    ),
    do_apply: Annotated[
        bool, typer.Option("--apply", help="Actually create conditions (default: dry run)")
    ] = False,
    confirm_account: Annotated[
        int | None, typer.Option(help="Repeat the account id to confirm --apply")
    ] = None,
    manifest: Annotated[
        Path | None, typer.Option(help="Record of what was created (default: <in>/manifest.json)")
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
        _record(
            manifest or source / "manifest.json",
            account_id,
            region,
            result.objects,
            ("condition", result.failed),
            len(result.skipped_existing),
        )
    except (GuardError, MissingSecretError, NewRelicAPIError, ValueError, OSError) as exc:
        typer.echo(f"Error: {printable(str(exc), 300)}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(
        f"\nCreated {len(result.created)}, already existed {len(result.skipped_existing)}, "
        f"failed {len(result.failed)}. Conditions are DISABLED: review, then enable."
    )
    for name, reason in result.failed:
        typer.echo(f"  FAILED {printable(name, 50)}: {printable(reason, 150)}", err=True)
    if result.failed:
        _partial_failure()


@app.command("gcp-link-terraform")
def gcp_link_terraform_command(
    project: Annotated[str, typer.Option(help="Google Cloud project id to link")],
    nr_account_id: Annotated[int, typer.Option(help="New Relic account id to link it to")],
    alerts: Annotated[
        Path | None,
        typer.Option(help="Output folder of `convert` (has ir/); enables only the services used"),
    ] = None,
    services: Annotated[
        list[str] | None,
        typer.Option(help="Extra service blocks to enable, such as run or sql (repeatable)"),
    ] = None,
    pool_id: Annotated[str, typer.Option(help="Workload identity pool id")] = "newrelic-pool",
    out: Annotated[Path, typer.Option(help="New folder for main.tf")] = Path("out/gcp-link"),
) -> None:
    """Write Terraform that links a Google Cloud project to New Relic. Offline: runs nothing.

    You review the file and run `terraform apply` yourself; the apply creates a read-only service
    account and grants it IAM roles. Services come from the converted conditions in --alerts.
    """
    try:
        monitors = (
            [
                IRMonitor.model_validate_json(p.read_text())
                for p in sorted((alerts / "ir").glob("*.json"))
            ]
            if alerts
            else []
        )
        blocks, unknown = services_for(monitors)
        text = link_terraform(project, nr_account_id, [*blocks, *(services or [])], pool_id)
    except (ValueError, OSError) as exc:
        typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
        raise typer.Exit(code=1) from None
    out.mkdir(parents=True, exist_ok=True)
    (out / "main.tf").write_text(text)
    typer.echo(f"Wrote {out / 'main.tf'} with services: {', '.join(blocks) or 'none'}.")
    for prefix in unknown:
        typer.echo(f"  No New Relic service block known for {printable(prefix, 80)}.", err=True)
    typer.echo("Next: review it, set NEW_RELIC_API_KEY, then `terraform init && terraform apply`.")


@app.command("check-gcp-data")
def check_gcp_data_command(
    alerts: Annotated[Path, typer.Option(help="Output folder of `convert` (has ir/)")] = Path(
        "out/gcp-alerts"
    ),
    nr_account_id: Annotated[int, typer.Option(help="New Relic account id to query")] = 0,
    nr_region: Annotated[str, typer.Option(help="us or eu")] = "us",
    hours: Annotated[int, typer.Option(help="How far back to look (1 to 168)")] = 24,
    out: Annotated[Path | None, typer.Option(help="Also write the markdown report here")] = None,
) -> None:
    """Check that the data behind each converted Google condition reaches New Relic (read-only).

    A condition on a metric that never arrives would look healthy and never fire. Exits with
    code 2 if any converted condition has no data.
    """
    if not nr_account_id or not 1 <= hours <= 168:
        typer.echo("Error: --nr-account-id is required and --hours must be 1 to 168", err=True)
        raise typer.Exit(code=1)
    try:
        monitors = [
            IRMonitor.model_validate_json(p.read_text())
            for p in sorted((alerts / "ir").glob("*.json"))
        ]
        with NewRelicClient(require_env("NEW_RELIC_API_KEY"), nr_region) as client:
            checks = gcp_data.check_all(client, nr_account_id, monitors, hours)
    except (NewRelicAPIError, ValueError, OSError) as exc:
        typer.echo(f"Error: {printable(str(exc), 300)}", err=True)
        raise typer.Exit(code=1) from None
    text = gcp_data.render(checks, hours)
    typer.echo(text)
    if out:
        out.write_text(text)
    if gcp_data.has_blind_conditions(checks):
        raise typer.Exit(code=2)


@app.command("improve-measure", hidden=True)
def improve_measure_command(
    root: Annotated[Path, typer.Option(help="Repository root")] = Path("."),
    baseline: Annotated[
        Path | None, typer.Option(help="Approved baseline; exit 2 if anything got worse")
    ] = None,
    record_to: Annotated[
        Path | None, typer.Option("--record", help="Append the result to this history file")
    ] = None,
    out: Annotated[Path | None, typer.Option(help="Write the result JSON here")] = None,
) -> None:
    """Measure conversion on the repository fixtures, offline (used by the improvement loop)."""
    result = improve.measure(root)
    typer.echo(improve.render(result))
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if record_to:
        improve.record(record_to, result)
    if baseline:
        problems = improve.compare(json.loads(baseline.read_text()), result)
        for problem in problems:
            typer.echo(f"REGRESSION {printable(str(problem), 300)}", err=True)
        if problems:
            raise typer.Exit(code=2)


@app.command("extract-gcp")
def extract_gcp_command(
    project: Annotated[str, typer.Option(help="Google Cloud project id to read")],
    out: Annotated[Path, typer.Option(help="New folder for the export")] = Path("out/gcp"),
) -> None:
    """Read alert policies, notification channels and uptime checks from a Google Cloud project.

    Read-only: lists alertPolicies, notificationChannels and uptimeCheckConfigs through the Cloud
    Monitoring API. Credentials come from GOOGLE_OAUTH_ACCESS_TOKEN or `gcloud auth login`; they
    are never stored. Channels are saved as type and name only. The export can contain project
    ids and resource names: review it before sharing.
    """
    try:
        with http_getter(access_token()) as get:
            result = extract_gcp(get, project, out)
    except (GcpError, ValueError, OSError) as exc:
        typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        f"Read project {result.project}: {result.policies} alert policies "
        f"({result.conditions} conditions), {result.channels} notification channels, "
        f"{result.uptime_checks} uptime checks."
    )
    for warning in result.warnings:
        typer.echo(f"  Could not read {printable(warning, 200)}", err=True)
    typer.echo("Next:")
    typer.echo(f"  importer convert --source gcp --in {out / 'policies'} --out out/gcp-alerts")


@app.command("extract-cloudwatch")
def extract_cloudwatch_command(
    account_id: Annotated[str, typer.Option(help="AWS account id you expect (12 digits)")],
    region: Annotated[str, typer.Option(help="AWS region to read")] = "us-east-1",
    profile: Annotated[
        str | None,
        typer.Option(help="AWS profile name (default: your AWS keys or default profile)"),
    ] = None,
    out: Annotated[Path, typer.Option(help="New folder for the export")] = Path("out/cloudwatch"),
    dashboards: Annotated[bool, typer.Option(help="Also export dashboards")] = True,
    notifications: Annotated[
        bool, typer.Option(help="Also export the SNS subscriptions behind alarm actions")
    ] = True,
) -> None:
    """Read CloudWatch alarms and dashboards from an AWS account (read-only) with boto3.

    Runs only describe-alarms, list-dashboards, get-dashboard and sts get-caller-identity.
    Stops before reading anything if the signed-in account is not --account-id. The export can
    contain account ids, ARNs and resource names: review it before sharing.
    """
    try:
        result = extract_cloudwatch(
            Reader(region, profile), account_id, out, dashboards, notifications
        )
    except (AwsError, ValueError, OSError) as exc:
        typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        f"Read account {result.account} ({region}): {result.metric_alarms} metric alarms, "
        f"{result.composite_alarms} composite alarms, {result.dashboards} dashboards, "
        f"{result.topics} SNS topics."
    )
    if result.inventory:
        found = ", ".join(f"{n} {label}" for label, n in result.inventory.items())
        typer.echo(f"Also found: {found}.")
    for warning in result.warnings:
        typer.echo(f"  Could not read {printable(warning, 200)}", err=True)
    typer.echo("Next:")
    typer.echo(f"  importer convert --source cloudwatch --in {out / 'alarms'} --out out/cw")
    if dashboards:
        typer.echo(f"  importer convert-dashboards --in {out / 'dashboards'} --out out/cw-dash")
    typer.echo("  importer stats out/cw")
    for failure in result.failed_dashboards:
        typer.echo(f"  FAILED dashboard {failure}", err=True)
    for failure in result.failed_topics:
        typer.echo(f"  FAILED topic {failure}", err=True)
    if result.failed_dashboards or result.failed_topics:
        raise typer.Exit(code=1)


def _record(
    path: Path,
    account_id: int,
    region: str,
    objects: list[CreatedObject],
    failed: tuple[Kind, list[tuple[str, str]]] | None = None,
    existing: int = 0,
) -> None:
    """Add what this run created, what New Relic refused and what already existed."""
    manifest = (
        load_manifest(path) if path.exists() else Manifest(account_id=account_id, region=region)
    )
    if manifest.account_id != account_id:
        raise ValueError(f"{path} belongs to account {manifest.account_id}, not {account_id}")
    manifest.add(objects)
    if failed:
        manifest.add_failed(*failed)
        manifest.existing[failed[0]] = existing
    save_manifest(manifest, path)


@app.command("apply-dashboards")
def apply_dashboards_command(
    source: Annotated[
        Path, typer.Option("--in", help="Output folder of convert-dashboards (has dashboard-ir/)")
    ] = Path("out/dashboards"),
    account_id: Annotated[int, typer.Option(help="New Relic TEST account id")] = 0,
    region: Annotated[str, typer.Option(help="us or eu")] = "us",
    do_apply: Annotated[
        bool, typer.Option("--apply", help="Actually create dashboards (default: dry run)")
    ] = False,
    confirm_account: Annotated[
        int | None, typer.Option(help="Repeat the account id to confirm --apply")
    ] = None,
    manifest: Annotated[
        Path | None,
        typer.Option(help="Where to record what was created (default: <in>/manifest.json)"),
    ] = None,
) -> None:
    """Create the converted dashboards in a New Relic account (private; existing names skipped).

    Dry run by default. Writing needs --apply, --confirm-account and the account listed in
    NEW_RELIC_ALLOWED_ACCOUNT_IDS, exactly like `apply`.
    """
    if account_id <= 0:
        typer.echo("Error: --account-id is required", err=True)
        raise typer.Exit(code=1)
    try:
        plan = load_dashboard_plan(source / "dashboard-ir")
    except (OSError, ValueError) as exc:
        typer.echo(f"Error reading {source / 'dashboard-ir'}: {printable(str(exc), 200)}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"Account {account_id} ({region})")
    typer.echo(f"{len(plan.items)} dashboards to create, {plan.skipped} not emittable")
    for item in plan.items:
        typer.echo(f"  {printable(item.name, 60):60} {item.widgets} widgets")
    if not do_apply:
        typer.echo("\nDRY RUN: nothing was sent. Add --apply --confirm-account to create them.")
        return
    try:
        check_guards(account_id, confirm_account, os.environ.get("NEW_RELIC_ALLOWED_ACCOUNT_IDS"))
        key = require_env("NEW_RELIC_API_KEY")
        with NewRelicClient(key, region) as client:
            typer.echo(f"Account name: {printable(get_account_name(client, account_id))}")
            result = apply_dashboards(client, account_id, plan)
        _record(
            manifest or source / "manifest.json",
            account_id,
            region,
            result.objects,
            ("dashboard", result.failed),
            len(result.skipped_existing),
        )
    except (GuardError, MissingSecretError, NewRelicAPIError, ValueError, OSError) as exc:
        typer.echo(f"Error: {printable(str(exc), 300)}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        f"\nCreated {len(result.created)}, already existed {len(result.skipped_existing)}, "
        f"failed {len(result.failed)}. Dashboards are PRIVATE."
    )
    for name, reason in result.failed:
        typer.echo(f"  FAILED {printable(name, 50)}: {printable(reason, 150)}", err=True)
    if result.failed:
        _partial_failure()


@app.command("convert-notifications")
def convert_notifications(
    topics: Annotated[
        Path, typer.Option("--topics", help="Folder of exported SNS topics (extract-cloudwatch)")
    ] = Path("out/cloudwatch/notifications"),
    alerts: Annotated[
        Path, typer.Option("--alerts", help="Output folder of `convert` (has ir/)")
    ] = Path("out/cw"),
    out: Annotated[Path, typer.Option(help="Output directory")] = Path("out/notifications"),
    prefix: Annotated[str, typer.Option(help="Prefix for created object names")] = "Imported:",
) -> None:
    """Plan New Relic destinations, channels and workflows from exported SNS topics.

    One workflow per topic routes the converted conditions that used it. Offline and read-only.
    Email and https subscriptions migrate; everything else is reported with a reason.
    """
    try:
        raw_topics = load_topics(topics)
        monitors = [
            IRMonitor.model_validate_json(p.read_text())
            for p in sorted((alerts / "ir").glob("*.json"))
        ]
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {printable(str(exc), 300)}", err=True)
        raise typer.Exit(code=1) from None
    names: dict[int, str] = {}
    for m in monitors:
        try:
            names[m.source_id] = nerdgraph_payload(m)["variables"]["condition"]["name"]
        except (CannotEmit, ValueError):
            continue
    routes = routing(monitors, names)
    (out / "notification-plans").mkdir(parents=True, exist_ok=True)
    plans: list[NotificationPlan] = []
    lines = ["# Notification conversion report", ""]
    for n, raw in enumerate(raw_topics, start=1):
        topic = translate_topic(raw)
        plan = build_plan(topic, routes.get(topic.source_key, []), prefix)
        plans.append(plan)
        (out / "notification-plans" / f"topic-{n:04d}.json").write_text(
            plan.model_dump_json(indent=2) + "\n"
        )
        status = "workflow planned" if plan.workflow else "no workflow"
        lines += [
            f"## {md_cell(printable(topic.name, 80))}",
            "",
            f"{status}: {len(topic.channels)} channels migrate, {len(topic.unsupported)} do not; "
            f"{len(routes.get(topic.source_key, []))} converted conditions used this topic.",
        ]
        lines += [f"- not migrated: {md_cell(printable(r, 120))}" for r in topic.unsupported]
        if not routes.get(topic.source_key):
            lines.append("- no converted condition used this topic, so no workflow is created")
        lines.append("")
    unrouted = sorted(set(routes) - {t.get("TopicArn") for t in raw_topics})
    for arn in unrouted:
        lines.append(
            f"- topic {md_cell(printable(arn, 100))} was not exported; its alarms get no workflow"
        )
    (out / "report.md").write_text("\n".join(lines) + "\n")
    tf = TERRAFORM_HEADER + "\n" + notifications_terraform(plans)
    (out / "notifications.tf").write_text(tf)
    made = sum(p.workflow is not None for p in plans)
    typer.echo(f"Read {len(plans)} topics, planned {made} workflows. See {out / 'report.md'}")


@app.command("apply-notifications")
def apply_notifications_command(
    source: Annotated[
        Path, typer.Option("--in", help="Output folder of convert-notifications")
    ] = Path("out/notifications"),
    account_id: Annotated[int, typer.Option(help="New Relic TEST account id")] = 0,
    region: Annotated[str, typer.Option(help="us or eu")] = "us",
    do_apply: Annotated[
        bool, typer.Option("--apply", help="Actually create the objects (default: dry run)")
    ] = False,
    confirm_account: Annotated[
        int | None, typer.Option(help="Repeat the account id to confirm --apply")
    ] = None,
    manifest: Annotated[
        Path | None, typer.Option(help="Record of what was created (default: <in>/manifest.json)")
    ] = None,
) -> None:
    """Create the planned destinations, channels and (disabled) workflows in New Relic.

    Dry run by default; writing needs the same three guards as `apply`.
    """
    if account_id <= 0:
        typer.echo("Error: --account-id is required", err=True)
        raise typer.Exit(code=1)
    try:
        plans = [
            NotificationPlan.model_validate_json(p.read_text())
            for p in sorted((source / "notification-plans").glob("*.json"))
        ]
    except (OSError, ValueError) as exc:
        typer.echo(f"Error reading {source}: {printable(str(exc), 200)}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"Account {account_id} ({region})")
    typer.echo(f"{sum(p.workflow is not None for p in plans)} workflows to create (disabled)")
    for plan in plans:
        if plan.workflow:
            typer.echo(
                f"  {printable(plan.workflow.name, 50):50} {len(plan.destinations)} channels, "
                f"{len(plan.workflow.condition_names)} conditions"
            )
    if not do_apply:
        typer.echo("\nDRY RUN: nothing was sent. Add --apply --confirm-account to create them.")
        return
    try:
        check_guards(account_id, confirm_account, os.environ.get("NEW_RELIC_ALLOWED_ACCOUNT_IDS"))
        key = require_env("NEW_RELIC_API_KEY")
        with NewRelicClient(key, region) as client:
            typer.echo(f"Account name: {printable(get_account_name(client, account_id))}")
            result = apply_notifications(client, account_id, plans)
        _record(
            manifest or source / "manifest.json",
            account_id,
            region,
            result.objects,
            ("workflow", result.failed),
            len(result.skipped_existing),
        )
    except (GuardError, MissingSecretError, NewRelicAPIError, ValueError, OSError) as exc:
        typer.echo(f"Error: {printable(str(exc), 300)}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        f"\nCreated {len(result.created)}, already existed {len(result.skipped_existing)}, "
        f"failed {len(result.failed)}. Workflows are DISABLED."
    )
    for name, reason in result.failed:
        typer.echo(f"  FAILED {printable(name, 50)}: {printable(reason, 150)}", err=True)
    if result.failed:
        _partial_failure()


@app.command("validate")
def validate_command(
    alarms: Annotated[
        Path, typer.Option(help="describe-alarms JSON (from extract-cloudwatch)")
    ] = Path("out/cloudwatch/alarms"),
    alerts: Annotated[Path, typer.Option(help="Output folder of `convert` (has ir/)")] = Path(
        "out/cw"
    ),
    account_id: Annotated[str, typer.Option(help="AWS account id you expect (12 digits)")] = "",
    region: Annotated[str, typer.Option(help="AWS region to read")] = "us-east-1",
    profile: Annotated[
        str | None,
        typer.Option(help="AWS profile name (default: your AWS keys or default profile)"),
    ] = None,
    nr_account_id: Annotated[int, typer.Option(help="New Relic account id to query")] = 0,
    nr_region: Annotated[str, typer.Option(help="us or eu")] = "us",
    since: Annotated[
        str | None, typer.Option(help="Window start, UTC (default: 30 minutes ago)")
    ] = None,
    until: Annotated[str | None, typer.Option(help="Window end, UTC (default: now)")] = None,
    out: Annotated[Path | None, typer.Option(help="Also write the markdown report here")] = None,
) -> None:
    """Compare each converted query in New Relic with CloudWatch's own numbers (read-only).

    Runs the converted NRQL over the window and reads the same window from CloudWatch. Use a
    window that starts after ingestion started; New Relic has no earlier data.
    """
    from datetime import UTC, datetime, timedelta

    if not nr_account_id:
        typer.echo("Error: --nr-account-id is required", err=True)
        raise typer.Exit(code=1)
    try:
        end = (
            cw_validate.utc(until) if until else datetime.now(UTC).replace(second=0, microsecond=0)
        )
        start = cw_validate.utc(since) if since else end - timedelta(minutes=30)
        if start >= end:
            raise ValueError("--since must be earlier than --until")
        reader = Reader(region, profile)
        if caller_account(reader) != account_id:
            raise AwsError("the AWS credentials belong to a different account than --account-id")
        with NewRelicClient(require_env("NEW_RELIC_API_KEY"), nr_region) as client:
            checks = cw_validate.validate(
                reader, client, nr_account_id, alarms, alerts / "ir", start, end
            )
    except (AwsError, MissingSecretError, NewRelicAPIError, ValueError, OSError) as exc:
        typer.echo(f"Error: {printable(str(exc), 300)}", err=True)
        raise typer.Exit(code=1) from None
    text = cw_validate.render(checks, start, end)
    typer.echo(text, nl=False)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    if cw_validate.has_problems(checks):
        raise typer.Exit(code=1)


@app.command("rollback")
def rollback_command(
    manifest: Annotated[
        Path | None,
        typer.Option(help="manifest.json written by apply commands (default: the latest run)"),
    ] = None,
    account_id: Annotated[
        int, typer.Option(help="New Relic account id (default: NEW_RELIC_ACCOUNT_ID)")
    ] = 0,
    do_apply: Annotated[
        bool, typer.Option("--apply", help="Actually delete (default: preview, then ask)")
    ] = False,
    confirm_account: Annotated[
        int | None, typer.Option(help="Repeat the account id to confirm --apply")
    ] = None,
) -> None:
    """Delete exactly what a migration created, as recorded in its manifest.

    Without arguments it undoes the latest `migrate` run: it lists what would be deleted and asks
    before deleting. Each object is re-read and deleted only if its id still has its recorded name.
    A policy is kept if it still holds conditions that are not ours.
    """
    if manifest is None:
        manifest = _latest_manifest()
        if manifest is None or not manifest.exists():
            typer.echo("Error: no recent run found; pass --manifest.", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"Using the latest run: {manifest}")
    if account_id <= 0 and os.environ.get("NEW_RELIC_ACCOUNT_ID", "").strip().isdigit():
        account_id = int(os.environ["NEW_RELIC_ACCOUNT_ID"])
    if account_id <= 0:
        typer.echo("Error: --account-id is required", err=True)
        raise typer.Exit(code=1)
    try:
        record = load_manifest(manifest)
        if record.account_id != account_id:
            raise ValueError(f"manifest belongs to account {record.account_id}, not {account_id}")
        if do_apply:
            check_guards(
                account_id, confirm_account, os.environ.get("NEW_RELIC_ALLOWED_ACCOUNT_IDS")
            )
        with NewRelicClient(require_env("NEW_RELIC_API_KEY"), record.region) as client:
            result = rollback(client, account_id, record, apply=do_apply)
    except (GuardError, MissingSecretError, NewRelicAPIError, ValueError, OSError) as exc:
        typer.echo(f"Error: {printable(str(exc), 300)}", err=True)
        raise typer.Exit(code=1) from None
    verb = "Deleted" if do_apply else "Would delete"
    for obj in result.deleted:
        typer.echo(f"  {verb} {obj.kind:11} {obj.id}  {printable(obj.name, 60)}")
    for obj, why in result.skipped:
        typer.echo(f"  Left {obj.kind:11} {obj.id}  {printable(obj.name, 40)}: {why}")
    for obj, why in result.failed:
        typer.echo(f"  FAILED {obj.kind} {obj.id}: {printable(why, 150)}", err=True)
    if not do_apply:
        if (
            result.deleted
            and _interactive()
            and typer.confirm(
                f"\nDelete these {len(result.deleted)} objects from New Relic "
                f"account {account_id}?",
                default=False,
            )
        ):
            rollback_command(
                manifest=manifest, account_id=account_id, do_apply=True, confirm_account=account_id
            )
            return
        typer.echo("\nPREVIEW: nothing was deleted. Add --apply --confirm-account to delete.")
    elif result.failed:
        raise typer.Exit(code=1)


# Set when an apply command created what it could but some items failed (as opposed to a refusal
# or an API error that stopped it), so `migrate` can carry on with the other kinds of object.
_PARTIAL = {"failed": False}


def _partial_failure() -> NoReturn:
    _PARTIAL["failed"] = True
    raise typer.Exit(code=1)


def _step(title: str, run: Callable[[], None], *, keep_going: bool = False) -> bool:
    """Run one stage of `migrate`. Returns False if it had partial failures it carried on past.

    A stage that fails outright stops the whole run. With keep_going, a stage that only failed on
    some items does not: dashboards and notifications are still created, and the caller reports
    the failures at the end.
    """
    typer.echo(f"\n== {title}")
    _PARTIAL["failed"] = False
    try:
        run()
    except typer.Exit as exc:
        if exc.exit_code:
            if keep_going and _PARTIAL["failed"]:
                typer.echo(f"\n{title}: some items failed; carrying on with the rest.", err=True)
                return False
            typer.echo(f"\nStopped at: {title}. Nothing later was run.", err=True)
            raise typer.Exit(code=1) from None
    return True


def _plan_text(
    aws_account: str,
    nr_account: int,
    region: str,
    out: Path,
    stats_md: str,
    counts: dict[str, int],
    stream: tuple[int, int],
    headline: str = "",
) -> str:
    """The reviewable summary written by `migrate` (plain text, no untrusted names)."""
    ingest = out / "ingest"
    lines = [
        "# Migration plan",
        "",
        headline,
        "",
        f"AWS account {aws_account} ({region}) to New Relic account {nr_account}.",
        "",
        stats_md,
        "## Dashboards",
        "",
        f"{counts['dashboards']} read; see {out / 'dashboards' / 'report.md'} for widget detail.",
        "",
        "## Notifications",
        "",
        f"{counts['topics']} SNS topics; see {out / 'notifications' / 'report.md'}.",
        "",
        "## Data ingestion (you deploy this: it needs the New Relic license key)",
        "",
        f"Streams {stream[0]} namespaces and {stream[1]} percentile setting(s).",
        f"  cd {ingest} && terraform init && terraform plan   # apply after review",
        "  needs TF_VAR_newrelic_license_key, TF_VAR_newrelic_account_id, TF_VAR_newrelic_api_key",
        "",
        "## Afterwards",
        "",
        f"  importer validate --alarms {out / 'export' / 'alarms'} --alerts {out / 'alerts'} "
        f"--account-id {aws_account} --nr-account-id {nr_account} --since <ingestion start, UTC>",
        f"  importer rollback --manifest {out / 'manifest.json'} --account-id {nr_account}",
        "",
        "Conditions and workflows are created DISABLED, dashboards PRIVATE: review, then enable.",
    ]
    return "\n".join(lines) + "\n"


def _write_html_report(
    run: Path,
    aws_account: str,
    nr_account: int | None,
    region: str,
    target: Path | None = None,
    source: str = "cloudwatch",
) -> Path:
    path = target or run / "report.html"
    summary = load_summary(run)
    typer.echo(summary.headline())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_report(
            summary,
            aws_account=aws_account,
            nr_account=nr_account,
            region=region,
            source=source,
        )
    )
    return path


@app.command("report")
def report_command(
    run: Annotated[Path, typer.Argument(help="The --out folder of a `migrate` run")],
    out: Annotated[
        Path | None, typer.Option(help="Where to write (default: RUN/report.html)")
    ] = None,
    aws_account_id: Annotated[
        str, typer.Option(help="AWS account id or Google Cloud project, shown in the header")
    ] = "",
    nr_account_id: Annotated[int | None, typer.Option(help="Shown in the header")] = None,
    region: Annotated[str, typer.Option(help="Shown in the header")] = "",
    source: Annotated[str, typer.Option(help="cloudwatch or gcp")] = "cloudwatch",
) -> None:
    """Write the post-migration HTML report: what migrated, what did not, and how to fix it.

    Read-only and offline. The file holds alarm and resource names: review it before sharing.
    """
    try:
        if source not in PROFILES:
            raise ValueError(f"unknown source {source!r}; choose one of {', '.join(PROFILES)}")
        target = _write_html_report(run, aws_account_id, nr_account_id, region, out, source)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {printable(str(exc), 300)}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"Report written to {target}")


def _resolve_nr_account(given: int | None) -> int:
    if given is not None:
        return given
    text = os.environ.get("NEW_RELIC_ACCOUNT_ID", "").strip()
    if text.isdigit():
        return int(text)
    typer.echo(
        "Error: pass --nr-account-id, or set NEW_RELIC_ACCOUNT_ID (a .env file works).", err=True
    )
    raise typer.Exit(code=1)


def _resolve_aws_account(given: str | None, region: str, profile: str | None) -> str:
    """The AWS account to read: the one named, or the one you are signed in to (after asking)."""
    if given is not None:
        return given
    if not _interactive():
        typer.echo(
            "Error: pass --aws-account-id (nobody is here to confirm the account).", err=True
        )
        raise typer.Exit(code=1)
    try:
        signed_in = caller_account(Reader(region, profile))
    except AwsError as exc:
        typer.echo(f"Error: {printable(str(exc), 300)}", err=True)
        raise typer.Exit(code=1) from None
    if not typer.confirm(
        f"You are signed in to AWS account {signed_in}. Migrate this account?", default=True
    ):
        typer.echo("Stopped. Sign in to the right account, or pass --aws-account-id.", err=True)
        raise typer.Exit(code=1)
    return signed_in


LATEST = Path("out") / ".latest"


def _remember_latest(out: Path) -> None:
    """Note the newest run folder so `rollback` can find it without a path."""
    LATEST.parent.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(str(out) + "\n")


def _latest_manifest() -> Path | None:
    try:
        return Path(LATEST.read_text().strip()) / "manifest.json"
    except OSError:
        return None


def _interactive() -> bool:
    """Whether a person can answer a question (a terminal, not a script or a pipe)."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _wants_to_create(
    summary: Summary, nr_account: int, yes: bool, report_path: Path | None = None
) -> bool:
    """After the plan: check creating is possible, then ask (or accept --yes)."""
    try:
        check_guards(nr_account, nr_account, os.environ.get("NEW_RELIC_ALLOWED_ACCOUNT_IDS"))
        require_env("NEW_RELIC_API_KEY")
    except (GuardError, MissingSecretError) as exc:
        typer.echo(
            f"\nTo create this in New Relic you need NEW_RELIC_API_KEY set and account "
            f"{nr_account} listed in NEW_RELIC_ALLOWED_ACCOUNT_IDS ({printable(str(exc), 200)}).",
            err=True,
        )
        return False
    if yes:
        return True
    if not _interactive():
        return False
    if report_path is not None:
        webbrowser.open(report_path.resolve().as_uri())
        typer.echo(f"\nOpened the report: {report_path}")
    shown: tuple[tuple[str, AssetKind], ...] = (("alarms", "alarm"), ("dashboards", "dashboard"))
    parts = [
        f"{n} {label}" for label, kind in shown if (n := sum(a.migrated for a in summary.of(kind)))
    ]
    what = " and ".join(parts) or "the converted objects"
    return typer.confirm(
        f"\nCreate {what} in New Relic account {nr_account} now? They are created disabled or "
        "private, and one command undoes it",
        default=False,
    )


@app.command("migrate")
def migrate_command(
    aws_account_id: Annotated[
        str | None,
        typer.Option(
            help="AWS account id you expect (12 digits); default: the one you are signed in to"
        ),
    ] = None,
    nr_account_id: Annotated[
        int | None,
        typer.Option(help="New Relic account id to migrate into; default: NEW_RELIC_ACCOUNT_ID"),
    ] = None,
    region: Annotated[str, typer.Option(help="AWS region to read")] = "us-east-1",
    profile: Annotated[
        str | None,
        typer.Option(help="AWS profile name (default: your AWS keys or default profile)"),
    ] = None,
    nr_region: Annotated[str, typer.Option(help="New Relic region: us or eu")] = "us",
    out: Annotated[
        Path | None,
        typer.Option(
            help="New folder for everything this run writes (default: out/migration-<time>)"
        ),
    ] = None,
    do_apply: Annotated[
        bool, typer.Option("--apply", help="Create everything in New Relic without asking")
    ] = False,
    confirm_account: Annotated[
        int | None, typer.Option(help="Repeat the New Relic account id to confirm --apply")
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", help="Create without asking at the end of the plan")
    ] = False,
    open_report: Annotated[
        bool, typer.Option("--open/--no-open", help="Open the report in a browser before asking")
    ] = True,
) -> None:
    """Migrate CloudWatch into New Relic in one go: read, convert, plan, and create.

    It reads AWS (read-only), writes a plan and a report, opens the report, then asks whether to
    create everything in New Relic. Answer y to create alert conditions, dashboards and
    notification workflows, all DISABLED or PRIVATE, with a manifest so `rollback` can undo exactly
    that. Use --yes to skip the question, or --apply with --confirm-account for scripts. Creating
    needs NEW_RELIC_API_KEY and the account in NEW_RELIC_ALLOWED_ACCOUNT_IDS; both, and
    NEW_RELIC_ACCOUNT_ID, can live in a .env file. Metric data still needs the ingestion Terraform
    (it holds a secret key, so it is generated for you to deploy, not run).
    """
    aws_account_id = _resolve_aws_account(aws_account_id, region, profile)
    nr_account_id = _resolve_nr_account(nr_account_id)
    out = out or Path("out") / f"migration-{datetime.now(UTC):%Y%m%d-%H%M%S}"
    _remember_latest(out)
    from importer.core.stats import build_stats, load_run, render_markdown

    export, alerts, dash = out / "export", out / "alerts", out / "dashboards"
    notes, manifest_path = out / "notifications", out / "manifest.json"

    _step(
        "1. Read the AWS account (read-only)",
        lambda: extract_cloudwatch_command(
            account_id=aws_account_id,
            region=region,
            profile=profile,
            out=export,
            dashboards=True,
            notifications=True,
        ),
    )
    metric_alarms, composite_alarms = load_alarms(export / "alarms")
    attach_log_filters(metric_alarms, load_metric_filters(export / "logs"))
    has_alarms = bool(metric_alarms or composite_alarms)
    has_dashboards = any((export / "dashboards").glob("*.json"))
    if not (has_alarms or has_dashboards):
        typer.echo(
            f"\nNothing to migrate: account {aws_account_id} has no CloudWatch alarms or "
            f"dashboards in {region}. Check the region (--region) and the account.",
            err=True,
        )
        raise typer.Exit(code=1)
    if has_alarms:
        _step(
            "2. Convert alarms",
            lambda: convert(source=export / "alarms", out=alerts, source_system="cloudwatch"),
        )
    else:
        typer.echo("\n== 2. Convert alarms\nNo alarms found in this region; skipping.")
    if has_dashboards:
        _step(
            "3. Convert dashboards",
            lambda: convert_dashboards(
                source=export / "dashboards", out=dash, source_system="cloudwatch"
            ),
        )
    has_topics = any((export / "notifications").glob("*.json"))
    if has_topics:
        _step(
            "4. Plan notifications",
            lambda: convert_notifications(
                topics=export / "notifications", alerts=alerts, out=notes, prefix="Imported:"
            ),
        )

    dashboards_in = load_dashboards(export / "dashboards") if has_dashboards else []
    namespaces, specs = streaming_inputs(metric_alarms, dashboards_in)
    percentile_args = [
        f"{sp['namespace']}:{sp['metric_name']}:{','.join(map(str, sp['statistics']))}"  # type: ignore[call-overload]
        for sp in specs
    ]
    if namespaces:
        _step(
            "5. Generate the data-ingestion Terraform (not deployed)",
            lambda: ingest_setup(
                out=out / "ingest",
                aws_region=region,
                nr_region=nr_region,
                name_prefix="cw-import",
                namespaces=",".join(namespaces),
                alarms=None,
                percentile=percentile_args or None,
            ),
        )

    log_groups = log_groups_from_ir(alerts / "ir") if has_alarms else []
    if log_groups:
        _step(
            "5b. Generate the log-forwarding Terraform (not deployed)",
            lambda: ingest_logs_setup(
                out=out / "logs-ingest",
                aws_region=region,
                nr_region=nr_region,
                name_prefix="cw-import-logs",
                log_group=log_groups,
                alerts=None,
            ),
        )
    stats_md = ""
    if has_alarms:
        run = load_run(alerts)
        stats_md = render_markdown([run], build_stats([run]), top=15).split("\n", 2)[2]
    counts = {
        "dashboards": len(list((dash / "dashboard-ir").glob("*.json"))) if has_dashboards else 0,
        "topics": len(list((export / "notifications").glob("*.json"))),
    }
    plan = _plan_text(
        aws_account_id,
        nr_account_id,
        region,
        out,
        stats_md,
        counts,
        (len(namespaces), len(specs)),
        load_summary(out).headline(),
    )
    (out / "plan.md").write_text(plan)
    typer.echo(f"\nPlan written to {out / 'plan.md'}")

    def report() -> Path:
        path = _write_html_report(out, aws_account_id, nr_account_id, region)
        typer.echo(f"Report: {path}  (contains names; review before sharing)")
        return path

    if not do_apply:
        shown = report()
        if not _wants_to_create(
            load_summary(out), nr_account_id, yes, shown if open_report else None
        ):
            typer.echo(
                "\nNothing was created in New Relic. Run the command again with --yes to create "
                "it, or answer y when asked."
            )
            return
        do_apply, confirm_account = True, nr_account_id
    policy = f"Imported from CloudWatch ({aws_account_id})"
    clean = True
    if has_alarms:
        clean = _step(
            "6. Create alert conditions",
            lambda: apply(
                source=alerts,
                account_id=nr_account_id,
                region=nr_region,
                policy_name=policy,
                do_apply=True,
                confirm_account=confirm_account,
                manifest=manifest_path,
            ),
            keep_going=True,
        )
    if has_topics:
        clean &= _step(
            "7. Create notification workflows (disabled)",
            lambda: apply_notifications_command(
                source=notes,
                account_id=nr_account_id,
                region=nr_region,
                do_apply=True,
                confirm_account=confirm_account,
                manifest=manifest_path,
            ),
            keep_going=True,
        )
    if has_dashboards:
        clean &= _step(
            "8. Create dashboards (private)",
            lambda: apply_dashboards_command(
                source=dash,
                account_id=nr_account_id,
                region=nr_region,
                do_apply=True,
                confirm_account=confirm_account,
                manifest=manifest_path,
            ),
            keep_going=True,
        )
    report()
    undo = f"importer rollback --manifest {manifest_path} --account-id {nr_account_id} --apply"
    typer.echo(f"\nDone. To undo exactly this run: {undo} --confirm-account {nr_account_id}")
    if not clean:
        typer.echo("Some items could not be created (see FAILED lines above).", err=True)
        raise typer.Exit(code=1)


@app.command("ingest-logs-setup")
def ingest_logs_setup(
    out: Annotated[Path, typer.Option(help="Output directory")] = Path("out/logs-ingest"),
    aws_region: Annotated[str, typer.Option(help="AWS region of the log groups")] = "us-east-1",
    nr_region: Annotated[str, typer.Option(help="New Relic region: us or eu")] = "us",
    name_prefix: Annotated[str, typer.Option(help="Prefix for created resources")] = (
        "newrelic-logs"
    ),
    log_group: Annotated[
        list[str] | None, typer.Option(help="Log group to forward (repeatable)")
    ] = None,
    alerts: Annotated[
        Path | None,
        typer.Option(
            help="Output folder of `convert`: forward the log groups its log conditions use"
        ),
    ] = None,
) -> None:
    """Generate Terraform that forwards CloudWatch log groups to New Relic (Firehose).

    Converted log-metric alarms return data only after their log group reaches New Relic. Writes
    files only; nothing is deployed. The license key is never written to a file: pass it when
    applying with TF_VAR_newrelic_license_key.
    """
    try:
        groups = list(log_group or [])
        if alerts:
            groups += log_groups_from_ir(alerts / "ir")
        config = LogIngestConfig.model_validate(
            {
                "name_prefix": name_prefix,
                "aws_region": aws_region,
                "nr_region": nr_region,
                "log_groups": groups,
            }
        )
        files = log_ingest_files(config)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
        raise typer.Exit(code=1) from None
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text)
    typer.echo(f"Wrote {', '.join(files)} to {out}; forwarding {len(config.log_groups)} log groups")
    typer.echo("To deploy (review the plan first):")
    typer.echo("  export TF_VAR_newrelic_license_key=...   # never commit this")
    typer.echo(f"  cd {out} && terraform init && terraform plan")


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
    percentile: Annotated[
        list[str] | None,
        typer.Option(help="Also stream percentiles, as NAMESPACE:METRIC:p99[,p95] (repeatable)"),
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
        specs = []
        for item in percentile or []:
            namespace, _, rest = item.rpartition(":")
            namespace, _, metric_name = namespace.rpartition(":")
            if not (namespace and metric_name and rest):
                raise ValueError("--percentile must look like NAMESPACE:METRIC:p99[,p95]")
            specs.append(
                {"namespace": namespace, "metric_name": metric_name, "statistics": rest.split(",")}
            )
        config = IngestConfig.model_validate(
            {
                "name_prefix": name_prefix,
                "aws_region": aws_region,
                "nr_region": nr_region,
                "namespaces": chosen,
                "percentiles": specs,
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
