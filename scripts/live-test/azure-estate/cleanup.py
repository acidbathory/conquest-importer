"""Delete exactly the objects in the manifest, newest first. Previews unless --yes.

Refuses any name that does not start with ckg- (storage accounts: ckgstore), any id outside the
named resource group, and anything not in the manifest. Treats 404 as already gone.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from arm import Arm, ArmError, load_env

RG_DEFAULT = "azure-signup-rg"


def allowed(entry: dict[str, Any], rg: str) -> bool:
    name = str(entry.get("name", ""))
    ok_name = name.startswith("ckg-") or (
        entry.get("kind") == "storage" and name.startswith("ckgstore")
    )
    ident = str(entry.get("id", ""))
    in_rg = f"/resourcegroups/{rg}/providers/".lower() in ident.lower()
    return ok_name and in_rg and ident.lower().endswith("/" + name.lower())


def cleanup(arm: Arm, manifest: Path, rg: str, yes: bool) -> dict[str, list[str]]:
    data = json.loads(manifest.read_text())
    out: dict[str, list[str]] = {"deleted": [], "gone": [], "refused": [], "failed": []}
    for entry in reversed(data.get("resources", [])):
        name = entry["name"]
        if not allowed(entry, rg):
            out["refused"].append(name)
            print(f"REFUSED {name}")
            continue
        if not yes:
            print(f"would delete {entry['kind']:20} {name}")
            continue
        path = entry["id"] + ("?forceDelete=true" if entry["kind"] == "workspace" else "")
        try:
            arm.delete(path, entry["api"])
            out["deleted"].append(name)
            print(f"deleted {name}")
        except ArmError as err:
            if err.status == 404:
                out["gone"].append(name)
                print(f"already gone {name}")
            else:
                out["failed"].append(name)
                print(f"FAILED {name}: {err}")
    if not yes:
        print("preview only; pass --yes to delete")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--rg", default=RG_DEFAULT)
    ap.add_argument("--env-file", type=Path, default=Path(".env"))
    ap.add_argument("--manifest", type=Path, default=Path("out/live-azure/manifest.json"))
    a = ap.parse_args(argv)
    arm = Arm(load_env(a.env_file))
    try:
        res = cleanup(arm, a.manifest, a.rg, a.yes)
    finally:
        arm.close()
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
