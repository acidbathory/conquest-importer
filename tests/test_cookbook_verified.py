"""A status in the cookbook data needs a recorded run (docs/improvement/cookbook-verification.json).

The record is written by scripts/improve/verify_cookbook.py (read-only NerdGraph NRQL and an HTTPS
fetch of each documentation page). Nothing here touches the network.
"""

import hashlib
import json
import re
from pathlib import Path

from importer.core import cookbook

ROOT = Path(__file__).resolve().parent.parent
RECORD = json.loads((ROOT / "docs/improvement/cookbook-verification.json").read_text())
VARIABLE = re.compile(r"\{\{\s*(\w+)\s*\}\}")
HAS_RANGE = re.compile(r"\bSINCE\b", re.IGNORECASE)


def sent(example: cookbook.Example) -> str:
    """Must stay equal to `runnable` in scripts/improve/verify_cookbook.py."""
    subst = dict(example.subst)
    text = " ".join(VARIABLE.sub(lambda m: subst.get(m.group(1), "'x'"), example.text).split())
    if HAS_RANGE.search(text):
        return text
    fixed = re.search(r"TIMESERIES\s+\d", text, re.IGNORECASE)
    return f"{text} SINCE {'6 hours' if fixed else '1 day'} ago"


def test_every_nrql_example_has_a_passing_recorded_run() -> None:
    for b in cookbook.blockers():
        for m in b.methods:
            for e in m.examples:
                if e.kind in cookbook.NRQL_KINDS:
                    key = hashlib.sha256(sent(e).encode()).hexdigest()[:16]
                    run = RECORD["nrql"].get(key)
                    assert run and run["ok"], f"{b.key}/{m.id}: no passing run for {e.title}"


def test_verified_statuses_are_backed_by_evidence() -> None:
    for b in cookbook.blockers():
        for m in b.methods:
            where = f"{b.key}/{m.id}"
            nrql = [e for e in m.examples if e.kind in cookbook.NRQL_KINDS]
            if m.status.startswith("VERIFIED"):
                assert nrql, f"{where}: verified but has no NRQL"
            if m.status == "VERIFIED-VALUE":
                value = RECORD["values"].get(where)
                assert value and value["ok"], f"{where}: no passing value check"
            else:
                assert where not in RECORD["values"] or m.status != "DOC-ONLY"


def test_every_documentation_url_was_fetched_ok() -> None:
    for b in cookbook.blockers():
        for m in b.methods:
            for url in m.sources:
                assert RECORD["urls"].get(url) == 200, f"{b.key}/{m.id}: {url}"
