"""Drop secrets from an Azure resource at read time; keep a logical SecretRef name instead.

Azure returns webhook URLs, Logic App callback URLs, function trigger URLs and secure webhook ids
in the clear in the official examples (whether a real tenant masks them is UNVERIFIED), and
alert rules carry free-form key/value maps that may hold tokens. None of that is written to disk:

- a URL field (`serviceUri`, `uri`, `callbackUrl`, `httpTriggerUrl`) is emptied; `<field>SecretRef`
  holds the logical name and `<field>Host` the host, which is not secret;
- other secret fields (secure webhook ids, passwords, tokens, keys, phone numbers, ITSM ticket
  configuration) are removed and `<field>SecretRef` holds the logical name;
- the values of `webHookProperties`, `customProperties` and `actionProperties` become references;
- any other text that holds credentials inside a URL, or a `sig=`, `code=` or `token=` parameter,
  becomes a reference.

A reference is `secretref:azure/<type>/<resource name>/<path>`. It names where the secret lives,
so a person can supply the value again; it is never derived from the value.
"""

import re
from typing import Any
from urllib.parse import urlsplit

URL_KEYS = {"serviceuri", "uri", "callbackurl", "httptriggerurl"}
SECRET_KEY = re.compile(
    r"(?i)^(?:identifieruri|objectid|ticketconfiguration|phonenumber|sig|sas|bearer|authorization)$"
    r"|secret|password|passwd|token|apikey|accesskey|connectionstring|credential|sharedaccess"
    r"|(?:primary|secondary)key"
)
FREE_FORM = re.compile(r"(?i)^(?:webhookproperties|customproperties|actionproperties)$")
CREDENTIAL_TEXT = re.compile(
    r"://[^/\s:@]+:[^/\s@]+@|[?&;](?:sig|code|token|key|apikey|access_token|sas)=", re.IGNORECASE
)
SAFE = re.compile(r"[^A-Za-z0-9_.-]")
MAX_DEPTH = 220


def ref_name(parts: list[str]) -> str:
    return "secretref:azure/" + "/".join(SAFE.sub("_", p) or "_" for p in parts)


class _Walker:
    def __init__(self, family: str, name: str) -> None:
        self.base = [family, name]
        self.dropped = 0

    def ref(self, path: list[str]) -> str:
        self.dropped += 1
        return ref_name([*self.base, *path])

    def value(self, value: Any, path: list[str], depth: int) -> Any:
        if depth > MAX_DEPTH:
            return None
        if isinstance(value, dict):
            return self.mapping(value, path, depth)
        if isinstance(value, list):
            return [
                self.value(v, [*path[:-1], self.item_label(path, v, i)], depth + 1)
                for i, v in enumerate(value)
            ]
        if isinstance(value, str) and CREDENTIAL_TEXT.search(value):
            return self.ref(path)
        return value

    @staticmethod
    def item_label(path: list[str], item: Any, index: int) -> str:
        label = item.get("name") if isinstance(item, dict) else None
        suffix = label if isinstance(label, str) and label else str(index)
        return f"{path[-1]}[{suffix}]" if path else f"[{suffix}]"

    def mapping(self, data: dict[str, Any], path: list[str], depth: int) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in data.items():
            if not isinstance(key, str):
                continue
            here = [*path, key]
            lowered = key.lower()
            if lowered in URL_KEYS and isinstance(value, str) and value:
                out[key] = ""
                out[f"{key}SecretRef"] = self.ref(here)
                host = urlsplit(value).hostname
                if host:
                    out[f"{key}Host"] = host
            elif SECRET_KEY.search(key) and value not in (None, "", False):
                out[f"{key}SecretRef"] = self.ref(here)
            elif FREE_FORM.match(key) and isinstance(value, dict):
                out[key] = {k: self.ref([*here, str(k)]) for k in value if isinstance(k, str)}
            else:
                out[key] = self.value(value, here, depth + 1)
        return out


def sanitize_resource(resource: dict[str, Any], family: str) -> tuple[dict[str, Any], int]:
    """A copy of the resource without secrets, and how many values were replaced."""
    name = resource.get("name")
    walker = _Walker(family, name if isinstance(name, str) and name else "unnamed")
    return walker.mapping(resource, [], 0), walker.dropped
