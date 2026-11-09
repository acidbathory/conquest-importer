"""Read contact points from the Grafana API or an export, dropping secrets at read time.

The importer never asks Grafana to decrypt. Grafana returns `[REDACTED]` for a stored secret and
the importer never tries to recover it. This module is the only place a contact point becomes a
`ContactPoint`. Every item goes through it, whatever shape it arrives in (the API shape with
`settings`, or the sanitised shape the extractor writes), and every field is checked again, so a
hand-made or tampered file cannot bring a value in.

Kept: type, name, uid (replaced by a stable reference when they look like an address or token),
the names of settings that hold a value, option values of a known type (a boolean, an integer
in range, one of a list; see the schema table), the host (never path, query or credentials) of a
url field that is not secret, a default/empty/custom flag for each template field, and email
addresses for type email only (field marked sensitive, never written to a report). Free text is
not kept.
"""

import re
from typing import Any

from importer.core.redact import looks_sensitive
from importer.sources.grafana.notifications.models import (
    MAX_ADDRESSES,
    MAX_CONTACT_POINTS,
    MAX_SETTING_KEYS,
    ContactPoint,
    as_dict,
    as_list,
    safe_ref,
)
from importer.sources.grafana.notifications.schema import FieldSpec, field_spec
from importer.sources.grafana.notifications.templates import translate_template

REDACTED = "[REDACTED]"
KEY_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}")
TYPE_NAME = re.compile(r"[A-Za-z0-9_-]{1,40}")
HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
URL_HOST = re.compile(
    r"https?://([A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)(?::\d{1,5})?(?:[/?#].*)?",
    re.DOTALL,
)
ADDRESS = re.compile(r"[^@\s,;<>\"'()]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}")
ADDRESS_SPLIT = re.compile(r"[,;\n]")
TEMPLATE_STATUS = {"default", "empty", "custom"}
INTEGER = re.compile(r"-?\d{1,9}")
MAX_NESTING = 3


def _is_empty(value: Any) -> bool:
    return value is None or value is False or (isinstance(value, str | list | dict) and not value)


def _plain_key(path: str) -> bool:
    return KEY_NAME.fullmatch(path) is not None and not looks_sensitive(path)


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


def option_value(spec: FieldSpec, value: Any) -> str:
    """The value as text when it has the type the table gives the field; "" when it does not."""
    if spec.type == "bool":
        if isinstance(value, bool):
            return "true" if value else "false"
        return value if value in ("true", "false") else ""
    if spec.type == "int":
        if isinstance(value, bool):
            return ""
        text = str(value) if isinstance(value, int | str) else ""
        return text if INTEGER.fullmatch(text) and spec.min <= int(text) <= spec.max else ""
    if spec.values:
        return value if isinstance(value, str) and value in spec.values else ""
    if spec.pattern and isinstance(value, str) and re.fullmatch(spec.pattern, value):
        return value
    return ""


def _keep(cp: ContactPoint, path: str, value: Any, spec: FieldSpec) -> None:
    """Record the value of a known field according to its kind."""
    kind = spec.kind
    if kind == "option":
        if kept := option_value(spec, value):
            cp.options[path] = kept
        else:
            cp.dropped_options.append(path)
    elif kind == "url" and not spec.secure and (host := _host(value)):
        cp.url_host = cp.url_host or host
    elif kind == "address" and cp.type == "email":
        cp.addresses.extend(a for a in _addresses(value) if a not in cp.addresses)
    elif kind == "template":
        cp.templates[path] = translate_template(value if isinstance(value, str) else "x").status


def _apply(cp: ContactPoint, path: str, value: Any) -> None:
    if _is_empty(value):
        return
    if not _plain_key(path):
        cp.other_fields += 1
        return
    cp.fields_set.append(path)
    if value == REDACTED:
        cp.redacted.append(path)
        return
    spec = field_spec(cp.type, path)
    if spec is not None and not spec.secure:
        _keep(cp, path, value, spec)


def _identity(raw: dict[str, Any]) -> ContactPoint:
    type_raw = raw.get("type")
    type_name = type_raw if isinstance(type_raw, str) and TYPE_NAME.fullmatch(type_raw) else "other"
    name, uid = raw.get("name"), raw.get("uid")
    return ContactPoint(
        uid=safe_ref(uid) if isinstance(uid, str) else "",
        name=safe_ref(name) if isinstance(name, str) else "",
        type=type_name,
        disable_resolve_message=raw.get("disableResolveMessage") is True,
    )


def _finish(cp: ContactPoint) -> ContactPoint:
    cp.fields_set = sorted(set(cp.fields_set))
    cp.redacted = sorted(set(cp.redacted))
    cp.dropped_options = sorted(set(cp.dropped_options))
    return cp


def sanitize_contact_point(raw: dict[str, Any]) -> ContactPoint:
    """A `ContactPoint` from one raw integration; secrets, headers and free text are dropped."""
    cp = _identity(raw)
    for path, value in _flatten(as_dict(raw.get("settings")), "", 0, cp.type):
        _apply(cp, path, value)
    return _finish(cp)


def revalidate_contact_point(item: dict[str, Any]) -> ContactPoint:
    """A `ContactPoint` from an item already in the sanitised shape, every field checked again."""
    cp = _identity(item)
    for key in as_list(item.get("fields_set"))[:MAX_SETTING_KEYS]:
        if isinstance(key, str) and _plain_key(key):
            cp.fields_set.append(key)
        else:
            cp.other_fields += 1
    redacted = as_list(item.get("redacted"))
    cp.redacted = [k for k in redacted if isinstance(k, str) and k in cp.fields_set]
    for key, value in list(as_dict(item.get("options")).items())[:MAX_SETTING_KEYS]:
        spec = field_spec(cp.type, key) if isinstance(key, str) else None
        kept = option_value(spec, value) if spec is not None and spec.kind == "option" else ""
        if kept:
            cp.options[key] = kept
        elif isinstance(key, str) and _plain_key(key):
            cp.dropped_options.append(key)
    host = item.get("url_host")
    cp.url_host = host.lower() if isinstance(host, str) and HOST.fullmatch(host) else ""
    for key, status in list(as_dict(item.get("templates")).items())[:MAX_SETTING_KEYS]:
        if isinstance(key, str) and _plain_key(key) and status in TEMPLATE_STATUS:
            cp.templates[key] = status
    if cp.type == "email":
        cp.addresses = [
            a
            for a in as_list(item.get("addresses"))[:MAX_ADDRESSES]
            if isinstance(a, str) and ADDRESS.fullmatch(a)
        ]
    count = item.get("other_fields")
    cp.other_fields += (
        max(0, count) if isinstance(count, int) and not isinstance(count, bool) else 0
    )
    cp.disable_resolve_message = (
        item.get("disable_resolve_message") is True or cp.disable_resolve_message
    )
    return _finish(cp)


def read_contact_points(payload: Any) -> list[ContactPoint]:
    """Contact points from any mix of shapes: the provisioning list, the export (receivers
    grouped by name), or the sanitised shape. Each item is rebuilt from scratch."""
    found: list[ContactPoint] = []
    for item in as_list(payload)[:MAX_CONTACT_POINTS]:
        entry = as_dict(item)
        if "receivers" in entry:  # export: {name, receivers: [{uid, type, settings}]}
            receivers = as_list(entry["receivers"])[:MAX_CONTACT_POINTS]
            found.extend(
                sanitize_contact_point({"name": entry.get("name"), **as_dict(r)}) for r in receivers
            )
        elif "settings" in entry:
            found.append(sanitize_contact_point(entry))
        elif entry:
            found.append(revalidate_contact_point(entry))
        if len(found) >= MAX_CONTACT_POINTS:
            break
    return found[:MAX_CONTACT_POINTS]
