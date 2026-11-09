"""Plan New Relic notifications from Google Cloud notification channels and alert policies.

The extractor keeps only each channel's type, name and display name: addresses, URLs and tokens
are in the channel labels, which are deliberately not exported. So no destination can be created
automatically. What this module produces is the routing (one workflow per set of channels, with
the converted conditions that used the set) and, for every channel, the reason it needs a person
and the steps. The plan reuses the shared notification IR, so a later change that keeps targets
would flow through `build_plan` and `apply-notifications` unchanged.
"""

import json
from pathlib import Path
from typing import Any

from importer.core.emit.notifications import converted_condition_names
from importer.core.ids import MAX_INPUT_BYTES
from importer.core.ir import IRMonitor
from importer.core.notification_ir import IRTopic, NotificationPlan, build_plan
from importer.core.report import md_cell
from importer.core.text import printable

CHANNELS_FILE = "channels.json"
MAX_CHANNELS = 10000

EMAIL = "email channel: the address is not exported, create an email destination and enter it"
WEBHOOK_BEARER = (
    "webhook channel: the URL and token are not exported, create a webhook destination and "
    "enter the token again"
)
BASIC = (
    "webhook channel: the URL, user name and password are not exported, create a webhook "
    "destination and enter the password again"
)
SLACK = "slack channel: connecting Slack needs a one-time OAuth in New Relic"
PAGERDUTY = (
    "pagerduty channel: the service key is not exported, create a PagerDuty destination and "
    "enter the key again"
)
NO_DESTINATION = "channel type {kind!r} has no New Relic destination"
NOT_EXPORTED = "channel is not in the export, so its type is unknown"

# Google channel type -> the reason it cannot be created automatically
CHANNEL_TYPES = {
    "email": EMAIL,
    "webhook_tokenauth": WEBHOOK_BEARER,
    "webhook_basicauth": BASIC,
    "slack": SLACK,
    "pagerduty": PAGERDUTY,
    "sms": NO_DESTINATION,
    "pubsub": NO_DESTINATION,
    "mobile": NO_DESTINATION,
    "campfire": NO_DESTINATION,
}


def reasons_for(kind: str) -> list[str]:
    """Why a channel of this Google type needs a person. Always at least one reason."""
    template = CHANNEL_TYPES.get(kind, NO_DESTINATION)
    return [template.format(kind=printable(kind, 30))]


def load_channels(path: Path) -> dict[str, dict[str, Any]]:
    """Channels from `channels.json` (a file, or a folder holding it), keyed by resource name."""
    file = path / CHANNELS_FILE if path.is_dir() else path
    if not file.is_file():
        raise ValueError(f"no {CHANNELS_FILE} found at {path}")
    if file.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"{file.name} is larger than {MAX_INPUT_BYTES // (1024 * 1024)} MB")
    data = json.loads(file.read_text())
    if not isinstance(data, list) or len(data) > MAX_CHANNELS:
        raise ValueError(f"{file.name}: expected a list of at most {MAX_CHANNELS} channels")
    return {str(c["name"]): c for c in data if isinstance(c, dict) and c.get("name")}


def channel_sets(
    monitors: list[IRMonitor], condition_names: dict[int, str]
) -> tuple[dict[tuple[str, ...], list[str]], list[str]]:
    """Channel set -> converted condition names, and the names of conditions with no channel."""
    routes: dict[tuple[str, ...], list[str]] = {}
    unrouted: list[str] = []
    for m in monitors:
        name = condition_names.get(m.source_id)
        if name is None:
            continue
        handles = tuple(sorted(set(m.notification.handles)))
        if handles:
            routes.setdefault(handles, []).append(name)
        else:
            unrouted.append(name)
    return {k: sorted(set(v)) for k, v in routes.items()}, sorted(set(unrouted))


def _display(handle: str, channels: dict[str, dict[str, Any]]) -> str:
    channel = channels.get(handle)
    shown = str(channel.get("displayName") or handle) if channel else handle
    return printable(shown, 60)


def plan_set(
    handles: tuple[str, ...],
    conditions: list[str],
    channels: dict[str, dict[str, Any]],
    prefix: str,
) -> tuple[NotificationPlan, list[str]]:
    """The shared plan for one channel set, and one reason line per channel."""
    lines: list[str] = []
    reasons: list[str] = []
    for handle in handles:
        channel = channels.get(handle)
        kind = str(channel.get("type", "")) if channel else ""
        found = [NOT_EXPORTED] if channel is None else reasons_for(kind)
        reasons += found
        shown = md_cell(_display(handle, channels), 60)
        suffix = f" ({md_cell(kind, 30)})" if channel else ""
        lines += [f"- {shown}{suffix}: {md_cell(r, 160)}" for r in found]
    name = " + ".join(_display(h, channels) for h in handles)
    topic = IRTopic(
        source_system="gcp",
        source_key="|".join(handles),
        name=name,
        channels=[],
        unsupported=reasons,
    )
    return build_plan(topic, conditions, prefix), lines


def write_outputs(
    channels: dict[str, dict[str, Any]], monitors: list[IRMonitor], out: Path, prefix: str
) -> int:
    """Write one plan file per channel set and a report. Returns the number of sets."""
    routes, unrouted = channel_sets(monitors, converted_condition_names(monitors))
    (out / "notification-plans").mkdir(parents=True, exist_ok=True)
    lines = [
        "# Google Cloud notification report",
        "",
        "Addresses, URLs and secrets are not exported, so nothing here can be created "
        "automatically. Each section is one workflow to create by hand: a workflow with an "
        "issue filter on the condition names listed, sending to the destinations described.",
        "",
    ]
    for n, (handles, conditions) in enumerate(sorted(routes.items()), start=1):
        plan, reason_lines = plan_set(handles, conditions, channels, prefix)
        (out / "notification-plans" / f"set-{n:04d}.json").write_text(
            plan.model_dump_json(indent=2) + "\n"
        )
        lines += [f"## {md_cell(plan.topic, 100)}", "", "Conditions it routes:"]
        lines += [f"- {md_cell(c, 100)}" for c in conditions]
        lines += ["", "Channels:", *reason_lines, ""]
    if unrouted:
        lines += ["## Conditions with no notification channel", ""]
        lines += [f"- {md_cell(c, 100)}" for c in unrouted] + [""]
    unused = sorted(set(channels) - {h for hs in routes for h in hs})
    if unused:
        lines += ["## Channels no converted condition uses", ""]
        lines += [f"- {md_cell(_display(h, channels), 60)}" for h in unused] + [""]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    return len(routes)
