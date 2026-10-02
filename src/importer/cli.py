"""Command line entry point. Commands are added as each pipeline stage lands."""

import json
import logging
from pathlib import Path
from typing import Annotated

import typer

from importer.emit.common import CannotEmit
from importer.emit.nerdgraph import nerdgraph_payload
from importer.emit.terraform import terraform_file
from importer.extract.datadog import DatadogAPIError, DatadogClient, InvalidSiteError
from importer.models.ir import Confidence, IRMonitor
from importer.models.source import DatadogMonitor
from importer.report import build_report
from importer.secrets import DatadogCredentials, MissingSecretError, RedactingFilter
from importer.translate.monitors import load_metric_map, translate_monitor

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
) -> None:
    """Convert extracted monitors to IR, NerdGraph payloads, Terraform and a gap report.

    Offline and read-only: nothing is sent to New Relic.
    """
    metric_map = load_metric_map()
    monitors = []
    for path in sorted(source.glob("*.json")):
        monitors.append(
            translate_monitor(DatadogMonitor.model_validate_json(path.read_text()), metric_map)
        )
    if not monitors:
        typer.echo(f"No monitor files found in {source}", err=True)
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
    report = build_report(final, {m.source_id for m in emitted})
    (out / "report.md").write_text(report)
    typer.echo(f"Read {len(monitors)} monitors, emitted {len(emitted)}. See {out / 'report.md'}")


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
