"""Read alert rules from a Grafana stack, read-only.

Only the GET paths in ALLOWED_PATHS can run, against one validated https host, and none takes
a query string, so contact points are never read with `decrypt` and no secret is ever returned.
Contact points, notification policies and mute timings are read for counts only: no name, address
or setting is written. The service account token comes from the environment (GRAFANA_SA_TOKEN)
and is sent as a bearer token; this code never stores or prints it, and text that Grafana sends
back is scrubbed of it before it reaches a message. Redirects are not followed, responses are
size-capped, and every loop has a bound.

The host must be a Grafana Cloud stack (https://<stack>.grafana.net). Another https host is
accepted only when it is named a second time with an explicit `allow_host` (`--allow-host`), so a
mistyped or hostile GRAFANA_URL cannot receive the token. The provisioning list has no
pagination; it is capped at MAX_RULES.

Paths and shapes follow the Grafana documentation and one live stack (Grafana 13.4) for
`/api/v1/provisioning/alert-rules`. The three inventory paths were not read live.
"""

import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.json_depth import check_depth
from importer.core.text import printable

CLOUD_HOST = re.compile(r"https://[a-z0-9-]+\.grafana\.net")
CUSTOM_HOST = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?::\d{1,5})?"
)
IP_LIKE = re.compile(r"\d+(?:\.\d+){3}(?::\d+)?")
NUMERIC_LABEL = re.compile(
    r"[0-9]+|0x[0-9a-f]*"
)  # a final label like this is an address in disguise
INTERNAL_SUFFIXES = (".internal", ".local", ".localhost")
ALLOW_HOST_HELP = (
    "--allow-host must be a plain public host name like grafana.example.com (lowercase ASCII, "
    "port 443 only, not an address, .internal, .local or .localhost)"
)
BEARER = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
TOKEN_LIKE = re.compile(r"gl(?:sa|c|cp)_[A-Za-z0-9_=.-]+")
TYPE_NAME = re.compile(r"[a-z0-9_-]{1,40}")
RULES_PATH = "/api/v1/provisioning/alert-rules"
INVENTORY_PATHS = {
    "contact_points": "/api/v1/provisioning/contact-points",
    "policies": "/api/v1/provisioning/policies",
    "mute_timings": "/api/v1/provisioning/mute-timings",
}
INVENTORY_LABELS = {
    "contact_points": "contact points",
    "policies": "notification policies",
    "mute_timings": "mute timings",
}
ALLOWED_PATHS = frozenset({RULES_PATH, *INVENTORY_PATHS.values()})
MAX_RULES = 5000
MAX_CONTACT_POINTS = 10_000
MAX_TYPES = 50
MAX_POLICY_DEPTH = 20
MAX_POLICY_NODES = 10_000
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
MAX_MESSAGE = 300
HOST_HELP = (
    "GRAFANA_URL must look like https://<stack>.grafana.net (https only). For another https "
    "host, name it again with --allow-host <host name>"
)

# path -> response body as plain JSON
Getter = Callable[[str], Any]


class GrafanaError(Exception):
    """Reading from Grafana failed; the message is safe to show."""


def api_token() -> str:
    token = os.environ.get("GRAFANA_SA_TOKEN", "").strip()
    if not token:
        raise GrafanaError(
            "GRAFANA_SA_TOKEN is not set. Create a service account token with read access to "
            "alert rules, contact points, notification policies and mute timings, and put it "
            "in .env"
        )
    if not (token.isascii() and token.isprintable() and " " not in token):
        raise GrafanaError(
            "GRAFANA_SA_TOKEN must be plain ASCII text with no spaces; check that it was "
            "pasted without a smart quote, a dash or a line break"
        )
    return token


def grafana_url() -> str:
    return os.environ.get("GRAFANA_URL", "").strip()


def _check_allow_host(allow_host: str) -> str:
    """The allowed host in lowercase when it is a plain public name; else raise."""
    if not allow_host.isascii():
        raise ValueError(ALLOW_HOST_HELP)
    allowed = allow_host.strip().lower()
    if not CUSTOM_HOST.fullmatch(allowed) or IP_LIKE.fullmatch(allowed):
        raise ValueError(ALLOW_HOST_HELP)
    name, _, port = allowed.partition(":")
    if port not in ("", "443") or NUMERIC_LABEL.fullmatch(name.rsplit(".", 1)[-1]):
        raise ValueError(ALLOW_HOST_HELP)
    if name == "localhost" or name.endswith(INTERNAL_SUFFIXES):
        raise ValueError(ALLOW_HOST_HELP)
    return allowed


def validate_host(url: str, allow_host: str | None = None) -> str:
    """The base URL (scheme and host only) when it is a Grafana Cloud stack or the allowed host."""
    if not url.isascii():  # before lower(): the Kelvin sign would become a plain k
        raise ValueError(HOST_HELP)
    host = url.strip().lower().rstrip("/")
    if host and "://" not in host:
        host = "https://" + host
    allowed = _check_allow_host(allow_host) if allow_host is not None else None
    if CLOUD_HOST.fullmatch(host):
        return host
    if allowed is None:
        raise ValueError(HOST_HELP)
    if host != f"https://{allowed}":
        raise ValueError("--allow-host does not match the host in GRAFANA_URL; " + HOST_HELP)
    return host


def scrub(text: str, token: str) -> str:
    """Server text that is safe to show: no token, no bearer value, printable, capped."""
    if token:
        text = text.replace(token, "***")
    return printable(TOKEN_LIKE.sub("***", BEARER.sub("Bearer ***", text)), MAX_MESSAGE)


def _message(body: object) -> str:
    found = body.get("message") if isinstance(body, dict) else None
    return found if isinstance(found, str) else ""


def _read(response: httpx.Response) -> bytes:
    """The body, refusing one larger than MAX_RESPONSE_BYTES (header or actual size)."""
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
        raise GrafanaError("Grafana sent a response larger than the importer accepts")
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise GrafanaError("Grafana sent a response larger than the importer accepts")
        chunks.append(chunk)
    return b"".join(chunks)


def _decode(response: httpx.Response, token: str) -> Any:
    body = _read(response)
    valid = True
    try:
        check_depth(body)
        parsed = json.loads(body)
    except (ValueError, RecursionError):
        parsed, valid = None, False
    status = response.status_code
    if status >= 300:  # redirects are not followed, so 3xx is a failure too
        detail = scrub(_message(parsed), token) if status >= 400 else ""
        raise GrafanaError(f"Grafana returned {status}" + (f": {detail}" if detail else ""))
    if not valid:
        raise GrafanaError("Grafana returned a body that is not JSON")
    return parsed


@contextmanager
def http_getter(
    token: str,
    base: str,
    transport: httpx.BaseTransport | None = None,
    allow_host: str | None = None,
) -> Iterator[Getter]:
    """A Getter for the allow-listed paths that closes its connection pool when the block ends.

    httpx reads SSL_CERT_FILE, so a corporate root certificate can be supplied that way.
    Redirects are not followed, so the token cannot be sent to another host. `transport` is for
    tests.
    """
    try:  # checked here too, so no caller can send the token to a host that was not validated
        checked = validate_host(base, allow_host)
    except ValueError as exc:
        raise GrafanaError(str(exc)) from None
    if checked != base:
        raise GrafanaError("the base URL must be https://<host> exactly as validated")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    with httpx.Client(timeout=60, headers=headers, transport=transport) as client:

        def get(path: str) -> Any:
            if path not in ALLOWED_PATHS:
                raise GrafanaError("this path is not on the allow-list of read-only calls")
            try:
                with client.stream("GET", base + path) as response:
                    return _decode(response, token)
            except httpx.HTTPError as exc:
                raise GrafanaError(f"could not reach Grafana: {type(exc).__name__}") from None

        yield get


@dataclass
class ExtractResult:
    rules: int = 0
    inventory: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _rules(get: Getter, warnings: list[str]) -> list[dict[str, Any]]:
    body = get(RULES_PATH)
    if not isinstance(body, list):
        raise GrafanaError("Grafana did not return a list of alert rules")
    if len(body) > MAX_RULES:
        raise GrafanaError(f"more than {MAX_RULES} alert rules; stopped")
    rules: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in body:
        uid = item.get("uid") if isinstance(item, dict) else None
        if not isinstance(item, dict) or (isinstance(uid, str) and uid in seen):
            continue
        if isinstance(uid, str):
            seen.add(uid)
        rules.append(item)
    if len(rules) != len(body):
        warnings.append(
            f"{len(body) - len(rules)} list entries were left out (repeats or not rules)"
        )
    return rules


def _type_name(value: object) -> str:
    return value if isinstance(value, str) and TYPE_NAME.fullmatch(value) else "other"


def _contact_points(body: Any) -> dict[str, Any]:
    """Counts by integration type; never a name, address or setting."""
    if not isinstance(body, list):
        raise GrafanaError("unexpected response shape")
    types: dict[str, int] = {}
    for item in body[:MAX_CONTACT_POINTS]:
        name = _type_name(item.get("type") if isinstance(item, dict) else None)
        if name in types or len(types) < MAX_TYPES:
            types[name] = types.get(name, 0) + 1
    return {"count": len(body), "by_type": types}


def _policies(body: Any) -> dict[str, Any]:
    """How many routes the notification policy tree has, within a depth and size bound."""
    if not isinstance(body, dict):
        raise GrafanaError("unexpected response shape")
    routes = 0
    stack: list[tuple[dict[str, Any], int]] = [(body, 0)]
    while stack and routes < MAX_POLICY_NODES:
        node, depth = stack.pop()
        children = node.get("routes")
        if depth >= MAX_POLICY_DEPTH or not isinstance(children, list):
            continue
        for child in children:
            if isinstance(child, dict):
                routes += 1
                stack.append((child, depth + 1))
    return {"routes": routes}


def _mute_timings(body: Any) -> dict[str, Any]:
    if not isinstance(body, list):
        raise GrafanaError("unexpected response shape")
    return {"count": len(body)}


INVENTORY_READERS: dict[str, Callable[[Any], dict[str, Any]]] = {
    "contact_points": _contact_points,
    "policies": _policies,
    "mute_timings": _mute_timings,
}


def _inventory(get: Getter, warnings: list[str]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for name, path in INVENTORY_PATHS.items():
        try:
            found[name] = INVENTORY_READERS[name](get(path))
        except GrafanaError as exc:
            found[name] = None
            warnings.append(f"{INVENTORY_LABELS[name]} were NOT read: {exc}")
    return found


def _write(folder: Path, name: str, data: object) -> None:
    text = json.dumps(data, indent=2) + "\n"
    if len(text) > MAX_INPUT_BYTES:
        raise GrafanaError(f"{name} is larger than the converter accepts")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text)


def extract(get: Getter, base_url: str, out: Path, allow_host: str | None = None) -> ExtractResult:
    """Write alert-rules/ and inventory/ under `out` (a new or empty folder)."""
    validate_host(base_url, allow_host)
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} already has files; pass a new folder")
    result = ExtractResult()
    rules = _rules(get, result.warnings)
    result.rules = len(rules)
    result.inventory = _inventory(get, result.warnings)
    _write(out / "alert-rules", "alert-rules.json", rules)
    _write(out / "inventory", "inventory.json", result.inventory)
    return result
