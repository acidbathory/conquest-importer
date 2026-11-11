"""Strip what must not be written from a workbook or a portal dashboard, at read time.

Besides the secret rules of `sanitize.py` (applied to every field, including the JSON inside a
workbook's `serializedData`), a workbook loses its managed identity, the user who saved it and
its storage URI: none of them is needed to convert the content. `serializedData` stays what the
API returns, a string holding JSON, so the converter reads exports and live reads alike.
"""

import json
from typing import Any

from importer.sources.azure.sanitize import CREDENTIAL_TEXT, ref_name, sanitize_resource

DROPPED_WORKBOOK_FIELDS = ("identity",)
DROPPED_WORKBOOK_PROPERTIES = ("userId", "storageUri")


def _serialized(text: str, name: str) -> tuple[str, int]:
    """The serialized workbook with secrets replaced (JSON is cleaned as data, not as text)."""
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError):
        if CREDENTIAL_TEXT.search(text):
            return ref_name(["workbooks", name, "serializedData"]), 1
        return text, 0
    if not isinstance(parsed, dict):
        return text, 0
    clean, dropped = sanitize_resource(parsed, "workbookContent")
    return json.dumps(clean, separators=(",", ":")), dropped


def sanitize_workbook(resource: dict[str, Any]) -> tuple[dict[str, Any], int]:
    name = resource.get("name")
    label = name if isinstance(name, str) and name else "unnamed"
    props = resource.get("properties")
    data = props.get("serializedData") if isinstance(props, dict) else None
    shallow = {k: v for k, v in resource.items() if k not in DROPPED_WORKBOOK_FIELDS}
    if isinstance(props, dict):
        shallow["properties"] = {
            k: v
            for k, v in props.items()
            if k not in DROPPED_WORKBOOK_PROPERTIES and k != "serializedData"
        }
    clean, dropped = sanitize_resource(shallow, "workbooks")
    if isinstance(data, str):
        text, more = _serialized(data, label)
        clean.setdefault("properties", {})["serializedData"] = text
        dropped += more
    elif data is not None and isinstance(props, dict):
        inner, more = sanitize_resource({"serializedData": data}, "workbooks")
        clean.setdefault("properties", {}).update(inner)
        dropped += more
    return clean, dropped
