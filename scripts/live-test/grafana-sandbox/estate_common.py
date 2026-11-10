"""Shared helpers for the ckg- Grafana test estate scripts. Never prints the token."""

import json
import os
import subprocess
from pathlib import Path

URL = os.environ["GRAFANA_URL"].rstrip("/")
if not URL.startswith("http"):
    URL = "https://" + URL
TOKEN = os.environ["GRAFANA_SA_TOKEN"]
PROM, LOKI = "grafanacloud-prom", "grafanacloud-logs"
ROOT = "ckg-importer-test"
MANIFEST = Path("out/live-estate/manifest.json")


def call(
    method: str, path: str, body: dict | None = None, extra: list[str] | None = None
) -> tuple[int, str]:
    """curl keeps the corporate CA handling (SSL_CERT_FILE) simple; TLS verification stays on."""
    cmd = [
        "curl",
        "-sS",
        "-m",
        "60",
        "-X",
        method,
        "-w",
        "\n%{http_code}",
        "-H",
        f"Authorization: Bearer {TOKEN}",
        "-H",
        "Content-Type: application/json",
    ]
    cmd += extra or []
    cmd += [URL + path]
    if body is not None:
        cmd += ["-d", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False).stdout  # noqa: S603
    text, _, code = out.rpartition("\n")
    return int(code or 0), text


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}


def save_manifest(m: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=2))
