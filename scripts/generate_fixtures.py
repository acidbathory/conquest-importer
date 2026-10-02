"""Generate hand-written Datadog monitor fixtures in the shape of the Datadog v1 API.

These are SYNTHETIC. They follow Datadog's documented monitor format but have not been
compared with a live account yet. Replace or extend them with real `importer extract`
output once Datadog access is available.

Usage:  uv run python scripts/generate_fixtures.py
"""

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
OUT = HERE.parent / "tests" / "fixtures" / "datadog"
FIRST_ID = 9000001
NOW = "2026-10-01T12:00:00.000000+00:00"

DEFAULT_OPTIONS: dict[str, Any] = {
    "notify_audit": False,
    "locked": False,
    "timeout_h": 0,
    "include_tags": True,
    "require_full_window": True,
    "new_host_delay": 300,
    "notify_no_data": False,
    "renotify_interval": 0,
    "escalation_message": "",
    "silenced": {},
}

EXTRAS: list[dict[str, Any]] = [
    {
        "name": 'Hostile "name" ${evil} %{if true} with\nnewline',
        "type": "metric alert",
        "query": "avg(last_5m):avg:system.cpu.user{*} by {host} > 90",
        "message": 'Hostile text: ${evil} and "quotes" and `backticks` | pipes',
        "options": {"thresholds": {"critical": 90}},
    },
    {
        "name": "Scope value with a quote",
        "type": "metric alert",
        "query": "avg(last_5m):avg:system.cpu.user{env:pr'od} > 90",
        "options": {"thresholds": {"critical": 90}},
    },
    {
        "name": "Process monitor",
        "type": "process alert",
        "query": "processes('nginx').over('*').rollup('count').last('5m') < 1",
        "options": {"thresholds": {"critical": 1}},
    },
    {
        "name": "Logs with negation wildcard and grouping",
        "type": "log alert",
        "query": (
            'logs("-service:web* status:warn").index("main").rollup("count")'
            '.by("service").last("15m") > 200'
        ),
        "options": {"thresholds": {"critical": 200}},
    },
    {
        "name": "Memory used percent with warning",
        "type": "metric alert",
        "query": "avg(last_10m):(avg:system.mem.used{*} / avg:system.mem.total{*}) * 100 > 90",
        "options": {"thresholds": {"critical": 90, "warning": 80}},
    },
    {
        "name": "One day window",
        "type": "metric alert",
        "query": "avg(last_1d):avg:system.cpu.user{*} > 70",
        "options": {"thresholds": {"critical": 70}},
    },
    {
        "name": "Two day window (too long for New Relic)",
        "type": "metric alert",
        "query": "avg(last_2d):avg:system.cpu.user{*} > 70",
        "options": {"thresholds": {"critical": 70}},
    },
    {
        "name": "Unknown service check",
        "type": "service check",
        "query": '"http.can_connect".over("*").by("host").last(3).count_by_status()',
        "options": {"thresholds": {"critical": 2, "ok": 1}},
    },
    {
        "name": "Priority and renotify settings",
        "type": "metric alert",
        "query": "avg(last_5m):avg:system.load.1{*} > 8",
        "priority": 2,
        "options": {
            "thresholds": {"critical": 8},
            "renotify_interval": 60,
            "evaluation_delay": 900,
        },
    },
    {
        "name": "SLO alert",
        "type": "slo alert",
        "query": 'error_budget("abc123").over("7d") > 75',
        "options": {"thresholds": {"critical": 75}},
    },
]


def envelope(monitor_id: int, definition: dict[str, Any]) -> dict[str, Any]:
    options = {**DEFAULT_OPTIONS, **definition.get("options", {})}
    return {
        "id": monitor_id,
        "org_id": 1234567,
        "name": definition["name"],
        "type": definition["type"],
        "query": definition["query"],
        "message": definition.get("message", "Synthetic sample monitor. Safe to delete."),
        "tags": ["conquest-importer-sample"],
        "options": options,
        "multi": " by {" in definition["query"] or ".by(" in definition["query"],
        "priority": definition.get("priority"),
        "created": NOW,
        "created_at": 1790856000000,
        "modified": NOW,
        "deleted": None,
        "creator": {"name": "Sample User", "handle": "sample@example.com", "id": 1},
        "overall_state": "No Data",
        "overall_state_modified": NOW,
        "matching_downtimes": [],
        "restricted_roles": None,
    }


def main() -> None:
    definitions: list[dict[str, Any]] = json.loads((HERE / "sample_monitors.json").read_text())
    ids: dict[str, int] = {}
    for offset, definition in enumerate(definitions):
        if "ref" in definition:
            ids[definition["ref"]] = FIRST_ID + offset
    for old in OUT.glob("*.json"):
        old.unlink()
    all_definitions = [*definitions, *EXTRAS]
    for offset, definition in enumerate(all_definitions):
        query = definition["query"]
        for ref, value in ids.items():
            query = query.replace("{ref:" + ref + "}", str(value))
        monitor_id = FIRST_ID + offset
        payload = envelope(monitor_id, {**definition, "query": query})
        (OUT / f"{monitor_id}.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote {len(all_definitions)} fixtures to {OUT}")


if __name__ == "__main__":
    main()
