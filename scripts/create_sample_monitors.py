"""Create the 20 synthetic sample monitors in a Datadog TRIAL account.

This is the only part of the project that WRITES to Datadog, so:
- It is a dry run unless --apply is passed.
- It needs a key with monitors_write. Use a separate key from the read-only
  one used by `importer extract`, and delete it when you are done.
- Monitors already present (same name, tag conquest-importer-sample) are skipped,
  so re-running is safe.
- Failures are reported per monitor and do not stop the run.

Usage:
    uv run python scripts/create_sample_monitors.py            # dry run
    uv run python scripts/create_sample_monitors.py --apply    # create them
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx

from importer.extract.datadog import ALLOWED_SITES, DatadogClient, InvalidSiteError
from importer.secrets import DatadogCredentials, MissingSecretError, redact
from importer.tls import system_tls_context

SAMPLE_TAG = "conquest-importer-sample"
DEFINITIONS = Path(__file__).parent / "sample_monitors.json"
REF_PATTERN = re.compile(r"\{ref:([a-z_]+)\}")


def build_payload(definition: dict[str, Any], ids: dict[str, int]) -> dict[str, Any]:
    query = REF_PATTERN.sub(lambda m: str(ids[m.group(1)]), definition["query"])
    return {
        "name": definition["name"],
        "type": definition["type"],
        "query": query,
        "message": definition.get("message", "Synthetic sample monitor. Safe to delete."),
        "tags": [SAMPLE_TAG],
        "options": definition.get("options", {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="actually create monitors")
    args = parser.parse_args()

    try:
        creds = DatadogCredentials.from_env()
        if creds.site not in ALLOWED_SITES:
            raise InvalidSiteError(f"Unsupported Datadog site {creds.site!r}")
    except (MissingSecretError, InvalidSiteError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    definitions: list[dict[str, Any]] = json.loads(DEFINITIONS.read_text())
    secrets = [creds.api_key, creds.app_key]

    with DatadogClient(creds) as reader:
        existing = {m.name: m.id for m in reader.list_monitors() if SAMPLE_TAG in m.tags}

    ids: dict[str, int] = {}
    created = skipped = failed = 0
    headers = {
        "DD-API-KEY": creds.api_key.get_secret_value(),
        "DD-APPLICATION-KEY": creds.app_key.get_secret_value(),
    }
    with httpx.Client(
        base_url=f"https://api.{creds.site}",
        headers=headers,
        timeout=30.0,
        follow_redirects=False,
        verify=system_tls_context(),
    ) as http:
        for definition in definitions:
            name = definition["name"]
            ref = definition.get("ref")
            if name in existing:
                print(f"skip    {name} (already exists)")
                if ref:
                    ids[ref] = existing[name]
                skipped += 1
                continue
            try:
                payload = build_payload(definition, ids)
            except KeyError as exc:
                print(f"FAILED  {name}: missing reference {exc}")
                failed += 1
                continue
            if not args.apply:
                print(f"would create  {name}")
                continue
            resp = http.post("/api/v1/monitor", json=payload)
            if resp.status_code == 200:
                if ref:
                    ids[ref] = resp.json()["id"]
                print(f"created {name}")
                created += 1
            else:
                detail = redact(resp.text[:300], secrets)
                print(f"FAILED  {name}: HTTP {resp.status_code} {detail}")
                failed += 1

    mode = "applied" if args.apply else "dry run (nothing created; use --apply)"
    print(f"\n{mode}: created={created} skipped={skipped} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
