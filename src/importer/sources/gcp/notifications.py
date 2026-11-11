"""Plan New Relic notifications from Google Cloud notification channels and alert policies.

The extractor keeps only each channel's type, name and display name: addresses, URLs and tokens
are in the channel labels, which are deliberately not exported. So no destination can be created
automatically. What this module produces is the routing (one workflow per set of channels, with
the converted conditions that used the set) and, for every channel, the reason it needs a person
and the steps. The plan reuses the shared notification IR, so a later change that keeps targets
would flow through `build_plan` and `apply-notifications` unchanged.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from importer.core.emit.notifications import converted_condition_names
from importer.core.ids import MAX_INPUT_BYTES
from importer.core.ir import IRMonitor
from importer.core.notification_ir import (
    DestinationType,
    IRTopic,
    ManualDestination,
    NotificationPlan,
    SecretRef,
    build_plan,
)
from importer.core.redact import safe_name
from importer.core.report import md_cell
from importer.core.report_brand import header_lines

CHANNELS_FILE = "channels.json"
MAX_CHANNELS = 10000
MAX_NAMED = 5  # channels named in a plan's topic text
RESOURCE = re.compile(r"^projects/[a-z][a-z0-9-]{0,40}/notificationChannels/[A-Za-z0-9_-]{1,40}$")
KIND = re.compile(r"^[a-z_]{1,30}$")
SAFE_TAIL = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

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
GOOGLE_CHAT = (
    "google_chat channel: New Relic has no Google Chat destination, use a webhook destination "
    "with the space's incoming webhook URL (the URL holds a key and is not exported)"
)
SMS = "sms channel: New Relic has no SMS destination and the phone number is not exported"
PUBSUB = (
    "pubsub channel: the topic is not exported and New Relic has no Pub/Sub destination, "
    "forward through a webhook"
)
MOBILE = (
    "mobile channel (unverified type): the name is not in Google's channel descriptor list "
    "(using-channels-api, checked 2026-11-11); if it is a Cloud Mobile App channel, use a "
    "New Relic mobile push destination"
)
DEPRECATED = (
    "{kind} channel (deprecated): Google marks this type deprecated and says do not create new "
    "channels of it, so its details are not exported and nothing is migrated; choose another "
    "destination"
)
NO_DESTINATION = "channel type has no New Relic destination"  # no channel text: see reasons_for
GENERIC = NO_DESTINATION
NOT_EXPORTED = "channel is not in the export, so its type is unknown"


@dataclass(frozen=True)
class ChannelSpec:
    """What one Google channel type becomes: the reason a person is needed and the steps."""

    reason: str
    nr_type: str  # the closest of the 14 New Relic destination types, or "" for none
    steps: tuple[str, ...]
    secret: SecretRef | None = None  # the secret Google obfuscates; a logical name, never a value


def _secret(name: str) -> SecretRef:
    return SecretRef(name=name)


def _obfuscated(label: str, secret: SecretRef) -> str:
    return (
        f"Google obfuscates the {label} when it returns a channel, so it cannot be exported; "
        f"enter it again as the secret {secret.name}."
    )


_SLACK_TOKEN = _secret("GCP_SLACK_AUTH_TOKEN")
_PASSWORD = _secret("GCP_WEBHOOK_PASSWORD")
_TOKEN = _secret("GCP_WEBHOOK_TOKEN")
_SERVICE_KEY = _secret("GCP_PAGERDUTY_SERVICE_KEY")

CHANNEL_SPECS: dict[str, ChannelSpec] = {
    "email": ChannelSpec(EMAIL, DestinationType.EMAIL.value, ("Enter the address again.",)),
    "webhook_tokenauth": ChannelSpec(
        WEBHOOK_BEARER,
        DestinationType.WEBHOOK.value,
        (
            "Which label holds the token is not confirmed (UNVERIFIED), so take it from the "
            f"receiving service and enter it as the secret {_TOKEN.name}.",
            "If the URL points at ServiceNow or Jira use that New Relic destination instead; "
            "New Relic has no Opsgenie destination, so an Opsgenie URL stays a webhook.",
        ),
        _TOKEN,
    ),
    "webhook_basicauth": ChannelSpec(
        BASIC,
        DestinationType.WEBHOOK.value,
        (
            _obfuscated("password", _PASSWORD),
            "If the URL points at ServiceNow or Jira use that New Relic destination instead.",
        ),
        _PASSWORD,
    ),
    "slack": ChannelSpec(
        SLACK,
        DestinationType.SLACK.value,
        (
            f"Google obfuscates the auth_token (secret {_SLACK_TOKEN.name}) and New Relic signs "
            "in to Slack with its own OAuth, so no token is entered; a Slack admin approves it.",
        ),
        _SLACK_TOKEN,
    ),
    "pagerduty": ChannelSpec(
        PAGERDUTY,
        DestinationType.PAGERDUTY_SERVICE_INTEGRATION.value,
        (_obfuscated("service_key", _SERVICE_KEY),),
        _SERVICE_KEY,
    ),
    "google_chat": ChannelSpec(
        GOOGLE_CHAT,
        DestinationType.WEBHOOK.value,
        ("Use a custom payload shaped as a Google Chat message.",),
    ),
    "sms": ChannelSpec(
        SMS,
        "",
        ("Use email, a mobile push destination, PagerDuty or a webhook to an SMS service.",),
    ),
    "pubsub": ChannelSpec(
        PUBSUB, "", ("Point a webhook at a small receiver that publishes to the topic.",)
    ),
    "mobile": ChannelSpec(
        MOBILE,
        DestinationType.MOBILE_PUSH.value,
        ("The New Relic mobile app must be installed and signed in for each person.",),
    ),
    "hipchat": ChannelSpec(DEPRECATED.format(kind="hipchat"), "", ()),
    "campfire": ChannelSpec(DEPRECATED.format(kind="campfire"), "", ()),
}
# Google channel type -> the reason it cannot be created automatically
CHANNEL_TYPES = {kind: spec.reason for kind, spec in CHANNEL_SPECS.items()}


def reasons_for(kind: str) -> list[str]:
    """Why a channel of this Google type needs a person. Always at least one reason."""
    return [CHANNEL_TYPES.get(kind, GENERIC)]


def manual_destination(kind: str, label: str) -> ManualDestination | None:
    """The plan entry for a known channel type, with its New Relic type and steps."""
    spec = CHANNEL_SPECS.get(kind)
    if spec is None:
        return None
    return ManualDestination(
        service=kind, nr_type=spec.nr_type, label=label, steps=[spec.reason, *spec.steps]
    )


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
    if RESOURCE.match(handle):
        fallback = handle
    else:
        tail = handle.rsplit("/", 1)[-1]
        fallback = tail if SAFE_TAIL.match(tail) else "unknown channel"
    shown = str(channel.get("displayName", "")) if channel else ""
    return safe_name(shown, fallback)


def plan_set(
    handles: tuple[str, ...],
    conditions: list[str],
    channels: dict[str, dict[str, Any]],
    prefix: str,
) -> tuple[NotificationPlan, list[str]]:
    """The shared plan for one channel set, and one reason line per channel."""
    lines: list[str] = []
    reasons: list[str] = []
    manual: list[ManualDestination] = []
    for handle in handles:
        channel = channels.get(handle)
        kind = str(channel.get("type", "")) if channel else ""
        found = [NOT_EXPORTED] if channel is None else reasons_for(kind)
        reasons += found
        entry = manual_destination(kind, _display(handle, channels)) if channel else None
        if entry is not None:
            manual.append(entry)
        label = md_cell(_display(handle, channels), 60)
        suffix = f" ({kind if KIND.match(kind) else 'unrecognised type'})" if channel else ""
        lines += [f"- {label}{suffix}: {md_cell(r, 160)}" for r in found]
    shown = [_display(h, channels) for h in handles[:MAX_NAMED]]
    more = len(handles) - MAX_NAMED
    name = " + ".join(shown) + (f" and {more} more" if more > 0 else "")
    topic = IRTopic(
        source_system="gcp",
        source_key="set:" + hashlib.sha256("|".join(handles).encode()).hexdigest()[:16],
        name=name,
        channels=[],
        unsupported=reasons,
        manual_destinations=manual,
    )
    return build_plan(topic, conditions, prefix), lines


def write_outputs(
    channels: dict[str, dict[str, Any]], monitors: list[IRMonitor], out: Path, prefix: str
) -> int:
    """Write one plan file per channel set and a report. Returns the number of sets."""
    if out.exists() and any(out.iterdir()):
        # old plan files would be read by apply-notifications as if they were current
        raise ValueError(f"{out} already has files; pass a new folder")
    routes, unrouted = channel_sets(monitors, converted_condition_names(monitors))
    (out / "notification-plans").mkdir(parents=True, exist_ok=True)
    lines = [
        *header_lines("Google Cloud notification report", "gcp"),
        "",
        "Channel labels (addresses, URLs, tokens) are not exported, so nothing here can be "
        "created automatically. Channel names that look like an address, a URL or a token are "
        "shown as the resource id instead. Each section is one workflow to create by hand: a "
        "workflow with an issue filter on the condition names listed, sending to the "
        "destinations described.",
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
