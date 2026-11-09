"""Create, read back and delete a few test objects in a Dynatrace Platform tenant.

Purpose: get real JSON for the Dynatrace source's fixtures (the tenant held no alerting).
Nothing here can send a notification: every trigger is inactive and the email address is a
placeholder. Every object is named "ckg-..." and recorded in the manifest, and `delete` removes
only what the manifest lists.

Usage, from the repository root:
  python3 scripts/live-test/dynatrace/test_objects.py plan      # prints what would be created
  python3 scripts/live-test/dynatrace/test_objects.py create    # creates it (asks first)
  python3 scripts/live-test/dynatrace/test_objects.py read      # saves what Dynatrace stored
  python3 scripts/live-test/dynatrace/test_objects.py delete    # removes exactly the manifest

Needs DYNATRACE_ENV_URL and DYNATRACE_API_TOKEN in .env. The token needs write scopes for
Workflows (automation:workflows:write) and SLOs (slo:slos:write). It is passed to curl on stdin,
so it never shows in a process list, and curl uses the system certificate store (the corporate
network's TLS inspection breaks Python's own certificate bundle).
"""

import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "out" / "dynatrace-test"
MANIFEST = OUT / "manifest.json"
HOST = re.compile(r"^https://[a-z0-9]+\.apps\.dynatrace\.com$")

WORKFLOW_PROBLEM = {
    "title": "ckg-problem-email (test, inactive)",
    "description": "Conquest Importer test object. Safe to delete.",
    "trigger": {
        "eventTrigger": {
            "isActive": False,
            "filterQuery": 'event.kind == "DAVIS_PROBLEM" AND event.status == "ACTIVE" '
            'AND event.category == "AVAILABILITY"',
            "triggerConfiguration": {
                "type": "davis-problem",
                "value": {
                    "categories": {"availability": True},
                    "entityTags": {},
                    "customFilter": "",
                    "onProblemClose": False,
                },
            },
        }
    },
    "tasks": {
        "send_email": {
            "name": "send_email",
            "action": "dynatrace.email:send-email",
            "description": "Test email (never sent: the trigger is inactive)",
            "input": {
                "to": ["ckg-test@example.com"],
                "cc": [],
                "bcc": [],
                "subject": "ckg test problem",
                "content": "ckg test",
            },
            "position": {"x": 0, "y": 1},
            "predecessors": [],
        }
    },
}

WORKFLOW_SCHEDULED = {
    "title": "ckg-scheduled-error-check (test, inactive)",
    "description": "Conquest Importer test object. Runs a DQL check on a schedule. Safe to delete.",
    "trigger": {
        "schedule": {
            "isActive": False,
            "isFaulty": False,
            "rule": None,
            "timezone": "UTC",
            "filterParameters": {},
            "trigger": {"type": "interval", "intervalMinutes": 15},
        }
    },
    "tasks": {
        "count_errors": {
            "name": "count_errors",
            "action": "dynatrace.automations:execute-dql-query",
            "description": "Count error logs in the last 15 minutes",
            "input": {
                "query": 'fetch logs, from:now() - 15m | filter loglevel == "ERROR" '
                "| summarize errors = count()"
            },
            "position": {"x": 0, "y": 1},
            "predecessors": [],
        },
        "notify": {
            "name": "notify",
            "action": "dynatrace.email:send-email",
            "description": "Email when the error count is above the threshold",
            "input": {
                "to": ["ckg-test@example.com"],
                "cc": [],
                "bcc": [],
                "subject": "ckg test: error logs above threshold",
                "content": 'Errors: {{ result("count_errors")["records"][0]["errors"] }}',
            },
            "position": {"x": 0, "y": 2},
            "predecessors": ["count_errors"],
            "conditions": {
                "states": {"count_errors": "OK"},
                "custom": '{{ result("count_errors")["records"][0]["errors"] > 100 }}',
            },
        },
    },
}

SLO = {
    "name": "ckg-availability-slo (test)",
    "description": "Conquest Importer test object. Safe to delete.",
    "tags": ["ckg-test"],
    "criteria": [
        {"timeframeFrom": "now-7d", "timeframeTo": "now", "target": 99.0, "warning": 99.5}
    ],
    "customSli": {
        "indicator": "timeseries { total = sum(dt.service.request.count), "
        "failed = sum(dt.service.request.failure_count) }, from:-7d "
        "| fieldsAdd sli = (total[] - failed[]) / total[] * 100"
    },
}

# kind -> (path, body, field that holds the name)
OBJECTS = {
    "workflow-problem": ("/platform/automation/v1/workflows", WORKFLOW_PROBLEM, "title"),
    "workflow-scheduled": ("/platform/automation/v1/workflows", WORKFLOW_SCHEDULED, "title"),
    "slo": ("/platform/slo/v1/slos", SLO, "name"),
}


def load_env() -> tuple[str, str]:
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    base = os.environ.get("DYNATRACE_ENV_URL", "").rstrip("/")
    token = os.environ.get("DYNATRACE_API_TOKEN", "")
    if not HOST.match(base) or not token:
        sys.exit(
            "Set DYNATRACE_ENV_URL (https://<id>.apps.dynatrace.com) and "
            "DYNATRACE_API_TOKEN in .env"
        )
    return base, token


def call(base: str, token: str, method: str, path: str, body: object = None) -> tuple[int, object]:
    url = base + urllib.parse.quote(path, safe="/:")
    lines = [
        f'url = "{url}"',
        f'request = "{method}"',
        f'header = "Authorization: Bearer {token}"',
        'header = "Accept: application/json"',
        'header = "Content-Type: application/json"',
        "silent",
        "max-time = 60",
        'write-out = "\\n%{http_code}"',
    ]
    if body is not None:
        lines.append("data = " + json.dumps(json.dumps(body)))
    done = subprocess.run(
        ["curl", "-K", "-"],  # noqa: S607
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode:
        sys.exit(f"curl failed ({done.returncode}): {done.stderr.strip()[:200]}")
    text, _, code = done.stdout.rpartition("\n")
    try:
        return int(code), json.loads(text) if text else {}
    except ValueError:
        return int(code), text[:300]


def manifest() -> list[dict[str, str]]:
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []


def save(entries: list[dict[str, str]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(entries, indent=2) + "\n")


def plan() -> None:
    for kind, (path, body, name_key) in OBJECTS.items():
        print(f"{kind}: POST {path}  name={body[name_key]!r}")
    print("\nAll triggers are inactive; the only email address is ckg-test@example.com.")


def create(base: str, token: str) -> None:
    plan()
    if input("\nCreate these in the tenant? Type yes: ").strip() != "yes":
        sys.exit("Nothing created.")
    entries = manifest()
    for kind, (path, body, name_key) in OBJECTS.items():
        if any(e["kind"] == kind for e in entries):
            print(f"{kind}: already in the manifest, skipped")
            continue
        code, reply = call(base, token, "POST", path, body)
        object_id = reply.get("id") if isinstance(reply, dict) else None
        if code in (200, 201) and object_id:
            entries.append(
                {"kind": kind, "path": path, "id": str(object_id), "name": body[name_key]}
            )
            save(entries)
            print(f"{kind}: created ({code})")
        else:
            print(f"{kind}: HTTP {code} {json.dumps(reply)[:600]}")


def read(base: str, token: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for entry in manifest():
        code, reply = call(base, token, "GET", f"{entry['path']}/{entry['id']}")
        target = OUT / f"{entry['kind']}.json"
        target.write_text(json.dumps(reply, indent=2) + "\n")
        print(f"{entry['kind']}: HTTP {code}, saved to {target.relative_to(ROOT)}")


def delete(base: str, token: str) -> None:
    entries = manifest()
    if not entries:
        sys.exit("The manifest is empty; nothing to delete.")
    left = []
    for entry in entries:
        if not entry["name"].startswith("ckg-"):
            print(f"{entry['kind']}: name does not start with ckg-, refusing to delete")
            left.append(entry)
            continue
        code, _ = call(base, token, "DELETE", f"{entry['path']}/{entry['id']}")
        if code in (200, 202, 204, 404):
            print(f"{entry['kind']}: deleted ({code})")
        else:
            print(f"{entry['kind']}: HTTP {code}, kept in the manifest")
            left.append(entry)
    save(left)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) == 2 else ""
    if mode == "plan":
        plan()
        return
    if mode not in {"create", "read", "delete"}:
        sys.exit(__doc__)
    base, token = load_env()
    {"create": create, "read": read, "delete": delete}[mode](base, token)


if __name__ == "__main__":
    main()
