"""Read `verified-labels.json` so a converter report can say "verified on <date>"."""

import json
import re
from pathlib import Path

from importer.core.dashboard_ir import IRWidget
from importer.core.ids import MAX_INPUT_BYTES
from importer.core.text import printable
from importer.parity.model import ParityError
from importer.parity.report import LABELS_VERSION, nrql_digest

MAX_LABELS = 100_000
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
DIGEST = re.compile(r"[0-9a-f]{16}")
Labels = dict[tuple[str, str, str], str]  # (dashboard key, widget title, nrql digest) -> date


def load_labels(path: Path) -> Labels:
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise ParityError(f"{printable(str(path), 120)} is too large to be a labels file")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ParityError(f"cannot read the parity file: {printable(str(exc), 200)}") from None
    entries = data.get("verified") if isinstance(data, dict) else None
    if not isinstance(data, dict) or data.get("version") != LABELS_VERSION:
        raise ParityError("the parity file is not a verified-labels.json written by verify-parity")
    if not isinstance(entries, list) or len(entries) > MAX_LABELS:
        raise ParityError("the parity file has no usable 'verified' list")
    found: Labels = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = (entry.get("dashboard"), entry.get("widget"), entry.get("nrql_sha256"))
        date = entry.get("verified_on")
        if (
            all(isinstance(k, str) for k in key)
            and isinstance(date, str)
            and DATE.fullmatch(date)
            and DIGEST.fullmatch(str(key[2]))
        ):
            found[(str(key[0]), str(key[1]), str(key[2]))] = date
    return found


def verified_on(labels: Labels, dashboard_key: str, widget: IRWidget) -> str:
    """The date every query of the widget was verified, or "" (any query not verified: none)."""
    dates = [
        labels.get((dashboard_key, widget.title, nrql_digest(s.nrql))) for s in widget.raw_series
    ]
    if not dates or None in dates:
        return ""
    return min(d for d in dates if d)
