"""Contact points to New Relic destinations, by the verdict table.

One contact point integration gives one `Point`: a typed `NrDestination` when New Relic can
create it from a script (email, webhook, the twelve webhook-template services, PagerDuty), a
`ManualDestination` with human steps when it cannot (Slack OAuth, Teams, Jira, SNS through
EventBridge, Pushover, an email with no address), or nothing when the service has no New Relic
equivalent. Secrets are never read or stored: a URL path, a token or a key is a `SecretRef`, a
logical name a person supplies at apply time (a sensitive Terraform variable named
`nr_secret_<lower case name>`). Email addresses are in the destination's property, which is the
sensitive field of the plan and never in a step, a reason or a report.

Steps are fixed sentences. Names, addresses, hosts and secret names never go into them.
"""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from importer.core.notification_ir import (
    EMAIL_ADDRESS,
    AuthKind,
    DestinationAuth,
    DestinationError,
    DestinationType,
    HeaderAuth,
    ManualDestination,
    NrDestination,
    SecretRef,
    SecureUrl,
    spec_for,
    validate_destination,
)
from importer.core.text import printable
from importer.sources.grafana.notifications.models import ContactPoint
from importer.sources.grafana.notifications.templates import DEFAULT_BODIES
from importer.sources.grafana.notifications.verdicts import (
    ContactPointVerdict,
    Verdict,
    contact_point_verdict,
)
from importer.sources.grafana.notify_convert import reasons as C
from importer.sources.grafana.notify_convert.destmap import MAX_ADDRESSES, MapEntry
from importer.sources.grafana.notify_convert.payloads import (
    PayloadTemplate,
    all_templates,
    template_for,
)

MAX_NAME = 120
FREE_PLAN_ADDRESSES = 5
SLUG_CHARS = 10
TOKEN_FIELDS = ("authorization_credentials",)
BASIC_FIELDS = ("password", "basicAuthPassword")
BEARER = "Bearer"
SUBJECT = DEFAULT_BODIES["default.title"]
PD_PREFIX = spec_for(DestinationType.PAGERDUTY_SERVICE_INTEGRATION).token_prefix
VENDOR_TYPES = frozenset(all_templates()) - {"generic"}

STEP_URL = (
    "Supply the secret for the destination URL: everything after the fixed start of the URL "
    "that the destination shows. The secret is named in the secrets list of the report."
)
STEP_URL_SCHEME = (
    "The URL start was made from the host that was exported, with https and the default port; "
    "the scheme and port are not exported. Check it."
)
STEP_BEARER = "Supply the secret for the credentials of the webhook (the bearer token)."
STEP_BASIC = (
    "Supply the secret for the Authorization header as the full header value, for example "
    "Basic followed by the base64 of user:password; the user name is not exported."
)
STEP_PD_KEY = "Supply the PagerDuty integration key as the secret."
STEP_HEADER = "Supply the secret as the full header value, the scheme word then the key."
STEP_PLACEHOLDER = "Replace {name} in the channel payload with {what}."
STEP_NO_ADDRESS = (
    "No email address was exported for this contact point. Add the addresses to the "
    "destinations map (email) and convert again, or create the EMAIL destination by hand."
)
STEP_FREE_PLAN = (
    "More than 5 addresses: a free New Relic account allows 5 per destination, a paid account 150."
)
STEP_BAD_ADDRESSES = "{count} addresses were not valid and were left out."
STEP_CUT_ADDRESSES = "{count} addresses beyond 150 were left out."
UNVERIFIED_PAYLOAD = (
    "The payload shape was built from the service's public API and from what Grafana sends; "
    "it was not sent to the service. Send a test message to a sandbox receiver first."
)
STEP_PAYLOAD_NEW = (
    "The payload was written for New Relic; the Grafana message and title settings are not used."
)
NOT_FITTING = "The destination does not fit the New Relic schema; create it by hand."


@dataclass(frozen=True)
class Point:
    """What one contact point integration became."""

    cp: ContactPoint
    verdict: ContactPointVerdict
    destination: NrDestination | None = None
    manual: ManualDestination | None = None
    secrets: tuple[str, ...] = ()
    steps: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    unverified: tuple[str, ...] = ()
    skipped: bool = False
    map_mismatch: bool = False
    disable_resolve: bool = False


class SecretNamer:
    """Logical secret names: GRAFANA_<up to 10 letters of the name>_<4 hex>_<ROLE>.

    The letters come from the contact point name through a whitelist (A-Z and 0-9 only), the hex
    from a hash of uid, name and type, and a repeat of the same identity gets a counter, so two
    contact points never share a name unless a person's map says they should.
    """

    def __init__(self) -> None:
        self._bases: dict[int, tuple[ContactPoint, str]] = {}
        self._count: dict[str, int] = {}

    def _base(self, cp: ContactPoint) -> str:
        known = self._bases.get(id(cp))
        if known is not None:
            return known[1]
        slug = re.sub(r"[^A-Z0-9]", "", cp.name.upper())[:SLUG_CHARS] or "CP"
        digest = hashlib.sha256(f"{cp.uid}\0{cp.name}\0{cp.type}".encode()).hexdigest()[:4].upper()
        base = f"GRAFANA_{slug}_{digest}"
        seen = self._count.get(base, 0) + 1
        self._count[base] = seen
        base = base if seen == 1 else f"{base}{seen}"
        self._bases[id(cp)] = (cp, base)
        return base

    def ref(self, cp: ContactPoint, role: str) -> SecretRef:
        return SecretRef(name=f"{self._base(cp)}_{role}")


@dataclass
class _Build:
    destination: NrDestination | None = None
    manual: ManualDestination | None = None
    secrets: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def need(self, ref: SecretRef, *steps: str) -> SecretRef:
        """Record a secret the person must supply, with the steps that say what it is."""
        self.secrets.append(ref.name)
        self.steps.extend(steps)
        return ref

    def keep(self, dest: NrDestination) -> None:
        """Keep the destination only if it fits its type; otherwise it is a manual entry."""
        try:
            validate_destination(dest)
        except DestinationError:
            self.manual = ManualDestination(
                service=dest.type.value.lower(),
                nr_type=dest.type.value,
                label=dest.name,
                steps=[NOT_FITTING],
            )
            return
        self.destination = dest


@dataclass(frozen=True)
class _Req:
    """One conversion: the contact point, its verdict, the namer and the map entry."""

    cp: ContactPoint
    verdict: ContactPointVerdict
    namer: SecretNamer
    prefix: str
    entry: MapEntry | None

    @property
    def name(self) -> str:
        return printable(f"{self.prefix} {self.cp.name}", MAX_NAME)

    def secret(self, role: str) -> SecretRef:
        return self.namer.ref(self.cp, role)

    def url_ref(self) -> SecretRef:
        if self.entry is not None and self.entry.webhook_url:
            return SecretRef(name=self.entry.webhook_url)
        return self.secret("URL")

    def url_prefix(self, b: _Build) -> str:
        if self.cp.url_host:
            b.steps.append(STEP_URL_SCHEME)
            return f"https://{self.cp.url_host}/"
        return "https://"


# --- email ---


def _addresses(r: _Req, b: _Build) -> list[str]:
    source = r.entry.email if r.entry is not None and r.entry.email else r.cp.addresses
    valid = list(dict.fromkeys(a for a in source if EMAIL_ADDRESS.match(a)))
    invalid = len(set(source)) - len(valid)
    if invalid:
        b.steps.append(STEP_BAD_ADDRESSES.format(count=invalid))
    if len(valid) > MAX_ADDRESSES:
        b.steps.append(STEP_CUT_ADDRESSES.format(count=len(valid) - MAX_ADDRESSES))
        valid = valid[:MAX_ADDRESSES]
    if len(valid) > FREE_PLAN_ADDRESSES:
        b.steps.append(STEP_FREE_PLAN)
    return valid


def _email(r: _Req) -> _Build:
    b = _Build()
    valid = _addresses(r, b)
    if not valid:
        b.manual = ManualDestination(
            service="email",
            nr_type="EMAIL",
            label=printable(r.cp.name, 80),
            steps=[STEP_NO_ADDRESS],
        )
        b.reasons.append(C.R_NO_ADDRESS)
        return b
    b.keep(
        NrDestination(
            type=DestinationType.EMAIL,
            name=r.name,
            properties={"email": ",".join(valid)},
            channel_properties={"subject": SUBJECT},
        )
    )
    return b


# --- webhook and the twelve webhook-template services ---


def _auth_from_fields(r: _Req, b: _Build) -> DestinationAuth | None:
    """Authentication the contact point had, as references. A user name is not exported, so basic
    authentication becomes one Authorization header whose whole value is the secret."""
    fields = r.cp.fields_set
    if any(f in fields for f in TOKEN_FIELDS):
        scheme = r.cp.options.get("authorization_scheme", "")
        prefix = scheme if re.fullmatch(r"[A-Za-z]{1,20}", scheme) else BEARER
        token = b.need(r.secret("AUTH_TOKEN"), STEP_BEARER)
        return DestinationAuth(kind=AuthKind.TOKEN, prefix=prefix, token=token)
    if any(f in fields for f in BASIC_FIELDS):
        header = HeaderAuth(key="Authorization", value=b.need(r.secret("BASIC_AUTH"), STEP_BASIC))
        return DestinationAuth(kind=AuthKind.CUSTOM_HEADER, headers=[header])
    return None


def _webhook(r: _Req) -> _Build:
    b = _Build()
    ref = b.need(r.url_ref(), STEP_URL)
    b.keep(
        NrDestination(
            type=DestinationType.WEBHOOK,
            name=r.name,
            secure_url=SecureUrl(prefix=r.url_prefix(b), suffix=ref),
            auth=_auth_from_fields(r, b),
            channel_properties={"payload": template_for("generic").body},
        )
    )
    return b


def _vendor_url(
    r: _Req, template: PayloadTemplate, b: _Build
) -> tuple[dict[str, str | SecretRef], SecureUrl | None]:
    spec = template.url
    if spec is not None and spec.plain:
        host = r.cp.url_host
        return {
            "url": spec.host_option.format(host=host) if host and spec.host_option else spec.plain
        }, None
    ref = b.need(r.url_ref(), STEP_URL)
    prefix = spec.prefix if spec is not None and spec.prefix else r.url_prefix(b)
    return {}, SecureUrl(prefix=prefix, suffix=ref)


def _vendor_auth(r: _Req, template: PayloadTemplate, b: _Build) -> DestinationAuth | None:
    auth = template.auth
    if auth is None:
        return _auth_from_fields(r, b)
    if auth.kind == "token":
        token = b.need(r.secret("AUTH_TOKEN"), STEP_BEARER)
        return DestinationAuth(kind=AuthKind.TOKEN, prefix=auth.prefix, token=token)
    value = b.need(
        r.secret("AUTH_HEADER"), f"{STEP_HEADER} Write it as '{auth.scheme} <your key>'."
    )
    return DestinationAuth(
        kind=AuthKind.CUSTOM_HEADER, headers=[HeaderAuth(key=auth.key, value=value)]
    )


def _vendor(r: _Req) -> _Build:
    template = template_for(r.cp.type)
    b = _Build()
    properties, secure = _vendor_url(r, template, b)
    channel = {"payload": template.body}
    if template.channel_headers:
        channel["headers"] = json.dumps(template.channel_headers)
    b.steps += [STEP_PLACEHOLDER.format(name=n, what=w) for n, w in template.placeholders.items()]
    b.steps.append(STEP_PAYLOAD_NEW)
    if template.status == "unverified":
        b.unverified.append(UNVERIFIED_PAYLOAD)
        b.reasons.append(C.R_VENDOR_UNVERIFIED)
    b.unverified += template.unverified
    b.keep(
        NrDestination(
            type=DestinationType.WEBHOOK,
            name=r.name,
            properties=properties,
            secure_url=secure,
            auth=_vendor_auth(r, template, b),
            channel_properties=channel,
        )
    )
    return b


# --- PagerDuty, the types that stay manual, and the types with no equivalent ---


def _pagerduty(r: _Req) -> _Build:
    b = _Build()
    named = r.entry.pagerduty_key if r.entry is not None else None
    ref = SecretRef(name=named) if named else r.secret("PD_KEY")
    b.need(ref, STEP_PD_KEY)
    b.keep(
        NrDestination(
            type=DestinationType.PAGERDUTY_SERVICE_INTEGRATION,
            name=r.name,
            auth=DestinationAuth(kind=AuthKind.TOKEN, prefix=PD_PREFIX, token=ref),
            channel_properties={"summary": SUBJECT},
        )
    )
    return b


def _human_steps(target: str) -> tuple[str, ...]:
    try:
        return spec_for(DestinationType(target)).human_steps
    except ValueError:
        return ()


def _manual(r: _Req) -> _Build:
    target = r.verdict.target
    return _Build(
        manual=ManualDestination(
            service=r.cp.type,
            nr_type="" if target == "none" else target,
            label=printable(r.cp.name, 80),
            steps=[*r.verdict.manual_steps, *_human_steps(target)],
        )
    )


def _none(_: _Req) -> _Build:
    return _Build()


Builder = Callable[[_Req], _Build]


def _builder(cp: ContactPoint, verdict: ContactPointVerdict) -> Builder:
    """The builder for the verdict class; the type decides within a class."""
    if verdict.verdict is Verdict.NO_EQUIVALENT:
        return _none
    if verdict.verdict is Verdict.WEBHOOK_TEMPLATE:
        return _vendor
    if verdict.verdict is Verdict.CONVERTS:
        return _email if cp.type == "email" else _webhook
    return _pagerduty if cp.type == "pagerduty" else _manual


_FITS = {
    "email": frozenset({"email"}),
    "webhook_url": frozenset({"webhook", *VENDOR_TYPES}),
    "pagerduty_key": frozenset({"pagerduty"}),
}


def _fits(cp_type: str, entry: MapEntry) -> bool:
    if entry.skip:
        return True
    return any(getattr(entry, key) is not None and cp_type in types for key, types in _FITS.items())


def convert_point(
    cp: ContactPoint, namer: SecretNamer, prefix: str, entry: MapEntry | None = None
) -> Point:
    """Convert one contact point integration. `entry` is the destinations map entry, if any."""
    verdict = contact_point_verdict(cp)
    if entry is not None and entry.skip:
        return Point(cp, verdict, skipped=True, disable_resolve=cp.disable_resolve_message)
    mismatch = entry is not None and not _fits(cp.type, entry)
    request = _Req(cp, verdict, namer, prefix, None if mismatch else entry)
    built = _builder(cp, verdict)(request)
    reasons = [*verdict.reasons, *built.reasons]
    if built.secrets:
        reasons.append(C.R_SECRET)
    if mismatch:
        reasons.append(C.R_MAP_MISMATCH)
    return Point(
        cp,
        verdict,
        destination=built.destination,
        manual=built.manual,
        secrets=tuple(dict.fromkeys(built.secrets)),
        steps=(*(verdict.manual_steps if built.destination else ()), *built.steps),
        reasons=tuple(dict.fromkeys(reasons)),
        unverified=tuple(built.unverified),
        map_mismatch=mismatch,
        disable_resolve=cp.disable_resolve_message,
    )
