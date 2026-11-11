"""Action group receivers to New Relic destinations.

One `ReceiverResult` per receiver; one `GroupResult` per action group. What each kind becomes:

- email: one EMAIL destination for the whole group (comma separated, up to 150 addresses);
- webhook, logic app, Azure function, automation runbook: a WEBHOOK destination. A URL that can
  carry a secret is kept as `secure_url` (the host is the prefix, the rest is a `SecretRef`);
- ITSM, app push: manual, with the New Relic destination type to use;
- SMS, voice, Event Hub, ARM role: no New Relic destination; manual with a workaround.

Secrets are never read into the plan: a URL token is a `SecretRef` (a logical name a person
supplies as a sensitive Terraform variable `nr_secret_<lower case name>`). Phone numbers and the
Azure account of an app push receiver are personal data: they are not stored anywhere, and the
receiver is identified by a logical name (`AZURE_<group>_<hash>_SMS1`) and its position in the
action group. Steps and reasons are fixed sentences.
"""

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlsplit

from importer.core.emit.notifications import EMAIL_SUBJECT
from importer.core.notification_ir import (
    EMAIL_ADDRESS,
    DestinationError,
    DestinationType,
    ManualDestination,
    NrDestination,
    SecretRef,
    SecureUrl,
    validate_destination,
)
from importer.core.text import printable
from importer.sources.azure.notifications import reasons as R
from importer.sources.azure.notifications.link import action_group_key
from importer.sources.azure.notifications.models import ActionGroup, Receiver
from importer.sources.azure.notifications.payload import TEMPLATE

MAX_NAME = 120
MAX_EMAILS = 150
SLUG_CHARS = 10
TOKEN_SEGMENT = re.compile(r"[A-Za-z0-9_+=.-]{20,}")


class Outcome(StrEnum):
    CONVERTED = "converted"  # a destination is in the plan
    MANUAL = "manual"  # a person must act; a New Relic destination type exists
    NONE = "none"  # New Relic has no destination for this receiver
    SKIPPED = "skipped"  # the receiver is switched off (status Disabled)


@dataclass(frozen=True)
class ReceiverResult:
    kind: str
    index: int  # 1-based position among the receivers of the same kind in the group
    outcome: Outcome
    label: str  # safe to print: a service name, or "<kind> N" for receivers that name a person
    logical: str = ""  # logical name standing for a phone number or account (personal data)
    destination: NrDestination | None = None
    manual: ManualDestination | None = None
    secrets: tuple[str, ...] = ()
    steps: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    approximate: bool = False  # converted, but not the same behaviour


@dataclass
class GroupResult:
    key: str
    name: str
    short_name: str
    enabled: bool
    results: list[ReceiverResult] = field(default_factory=list)
    destinations: list[NrDestination] = field(default_factory=list)
    manual: list[ManualDestination] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unknown_kinds: int = 0


class SecretNamer:
    """Logical secret names: AZURE_<up to 10 letters of the group name>_<4 hex>_<ROLE><n>.

    The letters come from the group name through a whitelist, the hex from a hash of the group's
    key, and two groups that would share a base get a counter, so no two groups share a name.
    """

    def __init__(self) -> None:
        self._bases: dict[str, str] = {}
        self._taken: set[str] = set()

    def _base(self, key: str, name: str) -> str:
        known = self._bases.get(key)
        if known is not None:
            return known
        slug = re.sub(r"[^A-Z0-9]", "", name.upper())[:SLUG_CHARS] or "AG"
        digest = hashlib.sha256(key.encode()).hexdigest()[:4].upper()
        base, n = f"AZURE_{slug}_{digest}", 1
        while base in self._taken:
            n += 1
            base = f"AZURE_{slug}_{digest}{n}"
        self._taken.add(base)
        self._bases[key] = base
        return base

    def ref(self, key: str, name: str, role: str) -> SecretRef:
        return SecretRef(name=f"{self._base(key, name)}_{role}")


# --- fixed sentences ---------------------------------------------------------------------------
STEP_SECRET = (  # noqa: S105 - a sentence, not a password
    "Supply the secret part of the destination URL (everything after the host) as the secret "
    "named in the report; the URL is not stored in the plan."
)
STEP_COMMON_SCHEMA = (
    "The body is a New Relic template laid out like the Azure common alert schema; fields New "
    "Relic has no value for are left out. If the receiver parses the Azure schema, send a test "
    "notification to a sandbox receiver and compare before enabling the workflow."
)
STEP_HTTP = "The URL starts with http; check that New Relic accepts it for a webhook (UNVERIFIED)."
STEP_AAD = (
    "Azure signs the call with a Microsoft Entra token. A New Relic webhook takes basic auth, a "
    "token or a custom header from a script; OAuth2 can only be set in the New Relic UI. Create "
    "the destination there, or move the receiver to a URL with its own secret."
)
STEP_NO_URL = (
    "The export has no usable URL for this receiver (Azure may not return secret URLs). Create "
    "the WEBHOOK destination by hand with the URL from the Azure resource and attach it to the "
    "workflow."
)
STEP_CREDENTIALS = (
    "The URL carries a user name and password. Create a WEBHOOK destination with basic auth by "
    "hand, so the credentials are entered once in New Relic and never copied here."
)
STEP_PHONE = (
    "New Relic has no {what} destination. Choose one: PagerDuty (create a PagerDuty destination "
    "and let PagerDuty {how}), Opsgenie through a WEBHOOK destination, or a WEBHOOK destination to "
    "an SMS gateway your company already uses."
)
STEP_PHONE_PRIVACY = (
    "Keep the phone number out of the repository, the plan and the report: it is personal data. "
    "The logical name in the report stands for it; look it up in the Azure action group."
)
STEP_PUSH = (
    "New Relic mobile push goes only to the user who owns it, one destination per person. Each "
    "person installs the New Relic mobile app, signs in, and a MOBILE_PUSH destination is made "
    "for that user. The Azure account is not copied."
)
STEP_EVENT_HUB = (
    "No New Relic destination sends to Azure Event Hubs. Use a WEBHOOK destination to a small "
    "forwarder you own (an Azure Function or Logic App that writes to the event hub), starting "
    "from the payload template in the webhook destinations of this report."
)
STEP_ARM_ROLE = (
    "Azure mails everyone who holds the RBAC role when the alert fires. Find the holders (Access "
    "control (IAM) in the portal, or `az role assignment list`), then create an EMAIL "
    "destination with their addresses. New Relic does not follow later changes in the role."
)
STEP_ITSM = (
    "The ITSM receiver points at an IT Service Management Connector in a Log Analytics workspace; "
    "the action group does not say which product it is (UNVERIFIED how to read it). For "
    "ServiceNow create a SERVICE_NOW destination (URL, user, password) or SERVICE_NOW_APP after "
    "the New Relic application is installed in ServiceNow; for another product use a WEBHOOK."
)
STEP_EMAIL_LIMIT = (
    "A free New Relic account allows 5 addresses per EMAIL destination, a paid one 150."
)
STEP_NO_EMAIL = (
    "The export has no valid address for this receiver. Add it to the EMAIL destination by hand."
)
STEP_UNKNOWN = (
    "The action group has a receiver type this importer does not read (for example PagerDuty or "
    "Slack in a newer API version). Recreate it by hand."
)


def _manual(service: str, nr_type: str, label: str, steps: list[str]) -> ManualDestination:
    return ManualDestination(service=service, nr_type=nr_type, label=label, steps=steps)


@dataclass(frozen=True)
class _Url:
    raw: str
    prefix: str
    rest: str  # path, query and fragment: where a token lives
    credentials: bool
    https: bool


def _split(raw: str) -> _Url | None:
    """A usable absolute http(s) URL, or None (empty, a placeholder, no host)."""
    text = raw.strip()
    try:
        parts = urlsplit(text)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host or any(c.isspace() for c in text):
        return None
    netloc = host if ":" not in host else f"[{host}]"
    prefix = f"{parts.scheme}://{netloc}{f':{port}' if port else ''}/"
    rest = parts.path.lstrip("/")
    rest += f"?{parts.query}" if parts.query else ""
    rest += f"#{parts.fragment}" if parts.fragment else ""
    return _Url(
        raw=text,
        prefix=prefix,
        rest=rest,
        credentials=parts.username is not None or parts.password is not None,
        https=parts.scheme == "https",
    )


def _may_hold_secret(url: _Url, always: bool) -> bool:
    if not url.rest:
        return False
    if always or "?" in url.rest or "#" in url.rest:
        return True
    return any(TOKEN_SEGMENT.fullmatch(seg) for seg in url.rest.split("/"))


def _name(prefix: str, group: str, what: str) -> str:
    tail = f" {what}"
    return printable(f"{prefix} {group}", MAX_NAME - len(tail)) + tail


# receiver kind -> (secret role, label for names, URL field, URL always secret, service label)
_URL_KINDS = {
    "webhookReceivers": ("WEBHOOK", "webhook", "serviceUri", False, "azure webhook"),
    "logicAppReceivers": ("LOGICAPP", "logic app", "callbackUrl", True, "azure logic app"),
    "azureFunctionReceivers": ("FUNCTION", "function", "httpTriggerUrl", True, "azure function"),
    "automationRunbookReceivers": ("RUNBOOK", "runbook", "serviceUri", True, "azure runbook"),
}


def _webhook(
    group: ActionGroup, key: str, r: Receiver, n: int, namer: SecretNamer, prefix: str
) -> ReceiverResult:
    role, what, field_name, always, service = _URL_KINDS[r.kind]
    label = printable(r.name, 60) or f"{what} {n}"

    def stop(reason: str, step: str) -> ReceiverResult:
        manual = _manual(service, "WEBHOOK", label, [step])
        return ReceiverResult(
            r.kind, n, Outcome.MANUAL, label, manual=manual, steps=(step,), reasons=(reason,)
        )

    if r.kind == "webhookReceivers" and r.useAadAuth:
        return stop(R.R_AAD, STEP_AAD)
    url = _split(str(getattr(r, field_name)))
    if url is None:
        return stop(R.R_NO_URL, STEP_NO_URL)
    if url.credentials:
        return stop(R.R_URL_CREDENTIALS, STEP_CREDENTIALS)
    channel = {"payload": TEMPLATE}
    name = _name(prefix, group.name, f"{what} {n}")
    steps = [STEP_COMMON_SCHEMA]
    reasons = [R.R_COMMON_SCHEMA]
    if not url.https:
        steps.append(STEP_HTTP)
        reasons.append(R.R_HTTP_URL)
    dest: NrDestination | None = None
    secrets: tuple[str, ...] = ()
    if not _may_hold_secret(url, always):
        try:
            dest = NrDestination(
                type=DestinationType.WEBHOOK,
                name=name,
                properties={"url": url.raw},
                channel_properties=channel,
            )
        except DestinationError:
            dest = None  # looks like a secret after all: keep it as one
    if dest is None:
        ref = namer.ref(key, group.name, f"{role}{n}_URL")
        dest = NrDestination(
            type=DestinationType.WEBHOOK,
            name=name,
            secure_url=SecureUrl(prefix=url.prefix, suffix=ref),
            channel_properties=channel,
        )
        secrets = (ref.name,)
        steps.insert(0, STEP_SECRET)
        reasons.insert(0, R.R_SECRET)
    validate_destination(dest)
    return ReceiverResult(
        r.kind,
        n,
        Outcome.CONVERTED,
        label,
        destination=dest,
        secrets=secrets,
        steps=tuple(steps),
        reasons=tuple(reasons),
        approximate=True,
    )


def _personal(
    n: int, key: str, group: ActionGroup, namer: SecretNamer, role: str
) -> tuple[str, str]:
    """(label, logical name) of a receiver that names a person: no name, number or account."""
    return f"{role.lower()} receiver {n}", namer.ref(key, group.name, f"{role}{n}").name


def _fixed(
    r: Receiver,
    n: int,
    label: str,
    logical: str,
    outcome: Outcome,
    manual: ManualDestination,
    reason: str,
) -> ReceiverResult:
    return ReceiverResult(
        kind=r.kind,
        index=n,
        outcome=outcome,
        label=label,
        logical=logical,
        manual=manual,
        steps=tuple(manual.steps),
        reasons=(reason,),
    )


def _other(group: ActionGroup, key: str, r: Receiver, n: int, namer: SecretNamer) -> ReceiverResult:
    kind = r.kind
    if kind in ("smsReceivers", "voiceReceivers"):
        role, what, how, reason = (
            ("SMS", "SMS", "send the SMS", R.R_SMS)
            if kind == "smsReceivers"
            else ("VOICE", "voice call", "phone the person", R.R_VOICE)
        )
        label, logical = _personal(n, key, group, namer, role)
        steps = [STEP_PHONE.format(what=what, how=how), STEP_PHONE_PRIVACY]
        return _fixed(
            r, n, label, logical, Outcome.NONE, _manual(kind[:-9], "", logical, steps), reason
        )
    if kind == "azureAppPushReceivers":
        label, logical = _personal(n, key, group, namer, "PUSH")
        manual = _manual("azure app push", "MOBILE_PUSH", logical, [STEP_PUSH])
        return _fixed(r, n, label, logical, Outcome.MANUAL, manual, R.R_APP_PUSH)
    label = printable(r.name, 60) or f"{kind[:-9]} {n}"
    if kind == "itsmReceivers":
        manual = _manual("azure itsm", "SERVICE_NOW", label, [STEP_ITSM])
        return _fixed(r, n, label, "", Outcome.MANUAL, manual, R.R_ITSM)
    if kind == "eventHubReceivers":
        manual = _manual("azure event hub", "", label, [STEP_EVENT_HUB])
        return _fixed(r, n, label, "", Outcome.NONE, manual, R.R_EVENT_HUB)
    manual = _manual("azure arm role", "EMAIL", label, [STEP_ARM_ROLE])
    return _fixed(r, n, label, "", Outcome.NONE, manual, R.R_ARM_ROLE)


def _email_results(
    group: ActionGroup, prefix: str
) -> tuple[list[ReceiverResult], NrDestination | None, list[str]]:
    """Every email receiver's result, the one EMAIL destination, and group notes."""
    results: list[ReceiverResult] = []
    addresses: list[str] = []
    seen: set[str] = set()
    cut = False
    emails = [r for r in group.receivers if r.kind == "emailReceivers"]
    for n, r in enumerate(emails, start=1):
        label = f"email receiver {n}"
        if r.status.lower() == "disabled":
            results.append(ReceiverResult("emailReceivers", n, Outcome.SKIPPED, label))
            continue
        address = r.emailAddress.strip()
        if not EMAIL_ADDRESS.match(address):
            manual = _manual("azure email", "EMAIL", label, [STEP_NO_EMAIL])
            results.append(
                ReceiverResult(
                    "emailReceivers",
                    n,
                    Outcome.MANUAL,
                    label,
                    manual=manual,
                    steps=(STEP_NO_EMAIL,),
                    reasons=(R.R_NO_EMAIL,),
                )
            )
            continue
        if address.lower() in seen:
            results.append(ReceiverResult("emailReceivers", n, Outcome.CONVERTED, label))
            continue
        if len(addresses) >= MAX_EMAILS:
            cut = True
            continue
        seen.add(address.lower())
        addresses.append(address)
        results.append(ReceiverResult("emailReceivers", n, Outcome.CONVERTED, label))
    notes = [STEP_EMAIL_LIMIT] if len(addresses) > 5 else []
    if cut:
        notes.append(R.R_EMAIL_CUT)
    if not addresses:
        return results, None, notes
    dest = NrDestination(
        type=DestinationType.EMAIL,
        name=_name(prefix, group.name, "email"),
        properties={"email": ",".join(addresses)},
        channel_properties={"subject": EMAIL_SUBJECT},
    )
    validate_destination(dest)
    return results, dest, notes


def convert_group(group: ActionGroup, namer: SecretNamer, prefix: str) -> GroupResult:
    """Decide every receiver of one action group."""
    key = action_group_key(group.id) if group.id else group.name.lower()
    out = GroupResult(
        key=key,
        name=group.name,
        short_name=group.short_name,
        enabled=group.enabled,
        unknown_kinds=len(group.unknown_kinds),
    )
    email_results, email_dest, notes = _email_results(group, prefix)
    out.results += email_results
    out.notes += notes
    if email_dest is not None:
        out.destinations.append(email_dest)
    counters: dict[str, int] = {}
    for r in group.receivers:
        if r.kind == "emailReceivers":
            continue
        counters[r.kind] = n = counters.get(r.kind, 0) + 1
        res = (
            _webhook(group, key, r, n, namer, prefix)
            if r.kind in _URL_KINDS
            else _other(group, key, r, n, namer)
        )
        out.results.append(res)
        if res.destination is not None:
            out.destinations.append(res.destination)
    out.manual = [r.manual for r in out.results if r.manual is not None]
    for res in out.results:
        out.reasons += [x for x in res.reasons if x not in out.reasons]
    if out.unknown_kinds:
        out.reasons.append(R.R_UNKNOWN_RECEIVER)
    if not group.enabled:
        out.reasons.append(R.R_GROUP_DISABLED)
    return out
