"""Read contact points from the Grafana API or an export, dropping secrets at read time.

The importer never asks Grafana to decrypt. Grafana returns `[REDACTED]` for a stored secret and
the importer never tries to recover it. This module is the only place a raw contact point is
turned into a `ContactPoint`, so the rule is kept in one function: a value is kept only when the
schema table says its kind is safe.

Kept: type, name, uid, the names of settings that hold a value, short plain options, short
labels that do not look like a token or an address, the host (never path, query or credentials)
of a url field that is not secret, a default/empty/custom flag for each template field, and
email addresses for type email only (field marked sensitive, never written to a report).
"""

import re
from typing import Any

from importer.core.redact import looks_sensitive
from importer.core.text import printable
from importer.sources.grafana.notifications.models import (
    MAX_ADDRESSES,
    MAX_CONTACT_POINTS,
    MAX_NAME,
    MAX_SETTING_KEYS,
    ContactPoint,
    as_dict,
    as_list,
)
from importer.sources.grafana.notifications.schema import FieldSpec, field_spec
from importer.sources.grafana.notifications.templates import translate_template

REDACTED = "[REDACTED]"
KEY_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}")
TYPE_NAME = re.compile(r"[A-Za-z0-9_-]{1,40}")
OPTION_VALUE = re.compile(r"[A-Za-z0-9_.:-]{1,20}")  # an enum or a number, not a token
URL_HOST = re.compile(
    r"https?://([A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)(?::\d{1,5})?(?:[/?#].*)?",
    re.DOTALL,
)
ADDRESS = re.compile(r"[^@\s,;<>\"'()]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}")
ADDRESS_SPLIT = re.compile(r"[,;\n]")
LABEL_TEXT = re.compile(r"[#A-Za-z0-9_. /-]{1,40}")  # names and channels, not tokens
MAX_NESTING = 3


def _is_empty(value: Any) -> bool:
    return value is None or value is False or (isinstance(value, str | list | dict) and not value)


def _flatten(
    settings: dict[str, Any], prefix: str, depth: int, type_name: str
) -> list[tuple[str, Any]]:
    """(dotted path, value) leaves; a nested object is entered unless the schema calls it data."""
    leaves: list[tuple[str, Any]] = []
    for key, value in settings.items():
        path = f"{prefix}{key}"
        spec = field_spec(type_name, path)
        enter = isinstance(value, dict) and depth < MAX_NESTING
        if enter and (spec is None or spec.kind != "structured"):
            leaves.extend(_flatten(value, path + ".", depth + 1, type_name))
        else:
            leaves.append((path, value))
        if len(leaves) >= MAX_SETTING_KEYS:
            break
    return leaves[:MAX_SETTING_KEYS]


def _host(value: Any) -> str:
    match = URL_HOST.fullmatch(value) if isinstance(value, str) else None
    return match.group(1).lower() if match else ""


def _addresses(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    found = [part.strip() for part in ADDRESS_SPLIT.split(value[:100_000])]
    return [a for a in found if ADDRESS.fullmatch(a)][:MAX_ADDRESSES]


def _label(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    return text if LABEL_TEXT.fullmatch(text) and not looks_sensitive(text) else ""


def _option(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value) if isinstance(value, int | float | str) else ""
    return text if OPTION_VALUE.fullmatch(text) and not looks_sensitive(text) else ""


def _keep(cp: ContactPoint, path: str, value: Any, spec: FieldSpec) -> None:
    """Record the value of a known field according to its kind."""
    kind = spec.kind
    if kind == "option" and (kept := _option(value)):
        cp.options[path] = kept
    elif kind == "label" and (kept := _label(value)):
        cp.labels[path] = kept
    elif kind == "url" and not spec.secure and (host := _host(value)):
        cp.url_host = cp.url_host or host
    elif kind == "address" and cp.type == "email":
        cp.addresses.extend(a for a in _addresses(value) if a not in cp.addresses)
    elif kind == "template":
        cp.templates[path] = translate_template(value if isinstance(value, str) else "x").status


def _apply(cp: ContactPoint, path: str, value: Any) -> None:
    if _is_empty(value):
        return
    if not KEY_NAME.fullmatch(path):
        cp.other_fields += 1
        return
    cp.fields_set.append(path)
    if value == REDACTED:
        cp.redacted.append(path)
        return
    spec = field_spec(cp.type, path)
    if spec is not None and not spec.secure:
        _keep(cp, path, value, spec)


def sanitize_contact_point(raw: dict[str, Any]) -> ContactPoint:
    """A `ContactPoint` from one raw integration; secrets, headers and free text are dropped."""
    type_raw = raw.get("type")
    type_name = type_raw if isinstance(type_raw, str) and TYPE_NAME.fullmatch(type_raw) else "other"
    name = raw.get("name")
    cp = ContactPoint(
        uid=printable(str(raw.get("uid") or ""), MAX_NAME),
        name=printable(name if isinstance(name, str) else "", MAX_NAME),
        type=type_name,
        disable_resolve_message=raw.get("disableResolveMessage") is True,
    )
    for path, value in _flatten(as_dict(raw.get("settings")), "", 0, type_name):
        _apply(cp, path, value)
    cp.fields_set = sorted(set(cp.fields_set))
    cp.redacted = sorted(set(cp.redacted))
    return cp


def contact_points_from_payload(payload: Any) -> list[ContactPoint]:
    """Contact points from the provisioning list or the export (receivers grouped by name)."""
    found: list[ContactPoint] = []
    for item in as_list(payload)[:MAX_CONTACT_POINTS]:
        entry = as_dict(item)
        if "receivers" in entry:  # export: {name, receivers: [{uid, type, settings}]}
            receivers = as_list(entry["receivers"])[:MAX_CONTACT_POINTS]
            found.extend(
                sanitize_contact_point({"name": entry.get("name"), **as_dict(r)}) for r in receivers
            )
        elif entry:
            found.append(sanitize_contact_point(entry))
        if len(found) >= MAX_CONTACT_POINTS:
            break
    return found[:MAX_CONTACT_POINTS]
