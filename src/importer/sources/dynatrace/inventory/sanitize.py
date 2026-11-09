"""What the inventory extractor writes to disk: an allow-list for every object.

A deny-list of secret field names cannot be complete, so each saved object is rebuilt from the
few fields the planners read, and every value is checked or reduced:

- monitors keep their type, frequency, location ids, a script reduced to its structure (methods,
  cleaned URLs, event types, validation rule types, flags that say a body, header, credential or
  script was present) and the numbers of the availability thresholds. Request bodies, header and
  cookie values, authentication, client certificates, typed text, script code, validation
  patterns and CSS locator values are never kept.
- entities keep their type, technology names and operating system, and only when a value looks
  like a product name (letters, digits and a few signs).
- Platform dashboards keep the tile fields the planner reads. Titles, queries and markdown are
  stored as written, because the plan needs them; they can name internal systems or contain
  internal URLs. That is the one place free text from the tenant is kept: review before sharing.
- classic dashboards keep tile names, types, markdown and metric keys; owners and tags are dropped.
"""

import re
from typing import Any
from urllib.parse import urlsplit

from importer.core.redact import safe_name, safe_path
from importer.sources.dynatrace.inventory.models import dicts, label, labels, whole_number

OMITTED = "<omitted>"
MAX_TEXT = 100_000
MAX_TILES = 500
MAX_VARIABLES = 100
MAX_LIST_ITEMS = 200  # requests, events, locations, thresholds and tiles kept per object
LOCATION_ID = re.compile(r"[A-Za-z_]{3,40}-[0-9A-Fa-f]{1,32}")
FILTER_NAME = re.compile(r"[A-Za-z0-9_:.-]{1,80}")
METRIC_KEY = re.compile(r"[A-Za-z0-9_.:()-]{1,200}")
STATUS_VALUE = re.compile(r"[<>=!0-9, .-]{1,40}")
DEVICE_NAME = re.compile(r"[A-Za-z0-9 ._-]{1,60}")
MONITOR_TYPES = frozenset({"HTTP", "BROWSER", "NETWORK_AVAILABILITY"})
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
RULE_TYPES = frozenset(
    {"patternConstraint", "regexConstraint", "httpStatusesList", "certificateExpiryDateConstraint"}
)
VALUE_RULES = frozenset({"httpStatusesList", "certificateExpiryDateConstraint"})
EVENT_TYPE = re.compile(r"[a-z]{1,20}")
LOCATOR_TYPE = re.compile(r"[a-z]{1,12}")
PRESENCE_KEYS = (  # key -> kept as OMITTED when present, so the planner can see it was there
    "requestBody",
    "authentication",
    "clientCertificate",
    "preProcessingScript",
    "postProcessingScript",
)


def clean_url(url: object) -> str:
    """scheme://host[:port]/path without user info, query or fragment."""
    if not isinstance(url, str):
        return OMITTED
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return OMITTED
    if parts.scheme not in ("http", "https") or not host:
        return OMITTED
    return f"{parts.scheme}://{host}{port}{safe_path(parts.path)}"


def _rule(raw: dict[str, Any]) -> dict[str, Any]:
    kind = raw.get("type")
    rule: dict[str, Any] = {"type": kind if kind in RULE_TYPES else "other"}
    rule["passIfFound"] = raw.get("passIfFound") is True
    rule["regex"] = raw.get("regex") is True
    value = raw.get("value")
    if rule["type"] in VALUE_RULES and isinstance(value, str) and STATUS_VALUE.fullmatch(value):
        rule["value"] = value
    return rule


def _configuration(config: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"acceptAnyCertificate": config.get("acceptAnyCertificate") is True}
    if isinstance(config.get("followRedirects"), bool):
        out["followRedirects"] = config["followRedirects"]
    if config.get("userAgent"):
        out["userAgent"] = OMITTED
    return out


def _request(raw: dict[str, Any]) -> dict[str, Any]:
    method = str(raw.get("method", "GET")).upper()
    config = raw.get("configuration")
    config = config if isinstance(config, dict) else {}
    validation = raw.get("validation")
    rules = dicts(validation.get("rules"), MAX_LIST_ITEMS) if isinstance(validation, dict) else []
    out: dict[str, Any] = {
        "method": method if method in METHODS else "OTHER",
        "url": clean_url(raw.get("url")),
        "configuration": _configuration(config),
        "validation": {"rules": [_rule(r) for r in rules]},
    }
    out.update({k: OMITTED for k in PRESENCE_KEYS if raw.get(k)})
    if config.get("requestHeaders") or raw.get("requestHeaders"):
        out["hasHeaders"] = True
    return out


def _event(raw: dict[str, Any]) -> dict[str, Any]:
    kind = str(raw.get("type", "")).lower()
    out: dict[str, Any] = {"type": kind if EVENT_TYPE.fullmatch(kind) else "other"}
    if out["type"] == "navigate":
        out["url"] = clean_url(raw.get("url"))
    target = raw.get("target")
    found = dicts(target.get("locators"), 10) if isinstance(target, dict) else []
    kinds = [str(x.get("type", "")).lower() for x in found]
    if kinds:
        out["target"] = {
            "locators": [{"type": k if LOCATOR_TYPE.fullmatch(k) else "other"} for k in kinds]
        }
    return out


def _device(config: object) -> dict[str, Any]:
    device = config.get("device") if isinstance(config, dict) else None
    if not isinstance(device, dict):
        return {}
    name, orientation = device.get("deviceName"), str(device.get("orientation", "")).lower()
    return {
        "device": {
            "deviceName": name if isinstance(name, str) and DEVICE_NAME.fullmatch(name) else "",
            "orientation": orientation if orientation in ("landscape", "portrait") else "",
        }
    }


def script(monitor_type: str, raw: object) -> dict[str, Any]:
    """The script reduced to the structure the synthetic planner reads."""
    if not isinstance(raw, dict):
        return {}
    version = raw.get("version")
    out: dict[str, Any] = {
        "version": version if isinstance(version, str) and len(version) <= 10 else ""
    }
    if monitor_type == "HTTP":
        out["requests"] = [_request(r) for r in dicts(raw.get("requests"), MAX_LIST_ITEMS)]
    elif monitor_type == "BROWSER":
        kind = raw.get("type")
        out["type"] = kind if kind in ("availability", "clickpath") else ""
        out["configuration"] = _device(raw.get("configuration"))
        out["events"] = [_event(e) for e in dicts(raw.get("events"), MAX_LIST_ITEMS)]
        if raw.get("cookies"):
            out["cookies"] = [OMITTED]
    return out


def anomaly(raw: object) -> dict[str, Any]:
    """Only the numbers and switches the planner reads."""
    raw = raw if isinstance(raw, dict) else {}
    outage = raw.get("outageHandling")
    outage = outage if isinstance(outage, dict) else {}
    policy = outage.get("globalOutagePolicy")
    runs = whole_number(policy.get("consecutiveRuns")) if isinstance(policy, dict) else None
    loading = raw.get("loadingTimeThresholds")
    loading = loading if isinstance(loading, dict) else {}
    values = [whole_number(t.get("valueMs")) for t in dicts(loading.get("thresholds"), 20)]
    return {
        "outageHandling": {
            "globalOutage": outage.get("globalOutage") is True,
            "localOutage": outage.get("localOutage") is True,
            "globalOutagePolicy": {"consecutiveRuns": runs},
        },
        "loadingTimeThresholds": {
            "enabled": loading.get("enabled") is True,
            "thresholds": [{"valueMs": v} for v in values if v is not None],
        },
    }


def monitor(ident: str, detail: dict[str, Any], number: int) -> dict[str, Any]:
    kind = detail.get("type")
    kind = kind if kind in MONITOR_TYPES else "OTHER"
    places = detail.get("locations")
    return {
        "entityId": ident,
        "name": safe_name(str(detail.get("name", "")), f"monitor-{number}", 100),
        "type": kind,
        "enabled": detail.get("enabled") is not False,
        "frequencyMin": whole_number(detail.get("frequencyMin")),
        "locations": [
            x
            for x in (places[:MAX_LIST_ITEMS] if isinstance(places, list) else [])
            if isinstance(x, str) and LOCATION_ID.fullmatch(x)
        ],
        "script": script(kind, detail.get("script")),
        "anomalyDetection": anomaly(detail.get("anomalyDetection")),
    }


def location(raw: dict[str, Any]) -> dict[str, Any]:
    ident = raw.get("entityId")
    out: dict[str, Any] = {
        "entityId": ident if isinstance(ident, str) and LOCATION_ID.fullmatch(ident) else ""
    }
    for key, limit in (("name", 100), ("city", 80)):
        out[key] = safe_name(str(raw.get(key, "")), "", limit)
    country = raw.get("countryCode")
    out["countryCode"] = (
        country if isinstance(country, str) and re.fullmatch(r"[A-Za-z]{2}", country) else ""
    )
    kind = raw.get("type")
    out["type"] = kind if kind in ("PUBLIC", "PRIVATE", "CLUSTER") else ""
    return out


def entity(kind: str, raw: dict[str, Any]) -> dict[str, Any]:
    props = raw.get("properties")
    props = props if isinstance(props, dict) else {}
    return {
        "type": kind,
        "technologies": labels(props.get("serviceTechnologyTypes")),
        "os": label(props.get("osType")),
    }


def _text(value: object) -> str:
    return value[:MAX_TEXT] if isinstance(value, str) else ""


def _tile(raw: dict[str, Any]) -> dict[str, Any]:
    kind = _text(raw.get("type"))[:40]
    out: dict[str, Any] = {"type": kind, "title": _text(raw.get("title"))[:300]}
    for key in ("content", "query"):
        if isinstance(raw.get(key), str):
            out[key] = _text(raw[key])
    if kind in ("code", "slo") and raw.get("input"):
        out["input"] = OMITTED  # JavaScript, or a reference to an SLO
    if isinstance(raw.get("visualization"), str):
        out["visualization"] = raw["visualization"][:80]
    if "davisCopilot" in raw:
        out["davisCopilot"] = True
    for key, flag in (("timeframe", "tileTimeframeEnabled"), ("segments", "tileSegmentsEnabled")):
        value = raw.get(key)
        if isinstance(value, dict):
            out[key] = {flag: value.get(flag) is True}
    settings = raw.get("visualizationSettings")
    if isinstance(settings, dict):
        out["visualizationSettings"] = {
            k: settings[k] for k in ("thresholds", "coloring") if k in settings
        }
    return out


def _variable(raw: dict[str, Any]) -> dict[str, Any]:
    default = raw.get("defaultValue")
    default = (
        ([str(x)[:200] for x in default[:20]] if isinstance(default, list) else str(default)[:200])
        if isinstance(default, str | int | float | list)
        else ""
    )
    return {
        "key": _text(raw.get("key"))[:80],
        "type": _text(raw.get("type"))[:40],
        "input": _text(raw.get("input")),
        "multiple": raw.get("multiple") is True,
        "defaultValue": default,
    }


def _layout(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    return {k: raw[k] for k in ("x", "y", "w", "h") if isinstance(raw.get(k), int)}


def dashboard_content(inner: dict[str, Any]) -> dict[str, Any] | None:
    """A Platform dashboard reduced to what the planner reads; None when it has no tiles."""
    tiles = inner.get("tiles")
    if not isinstance(tiles, dict):
        return None
    layouts = inner.get("layouts")
    layouts = layouts if isinstance(layouts, dict) else {}
    kept = {str(k)[:80]: v for k, v in list(tiles.items())[:MAX_TILES] if isinstance(v, dict)}
    variables = dicts(inner.get("variables"), MAX_VARIABLES)
    settings = inner.get("settings")
    version = inner.get("version")
    return {
        "version": version if isinstance(version, int) and not isinstance(version, bool) else None,
        "variables": [_variable(v) for v in variables],
        "tiles": {k: _tile(v) for k, v in kept.items()},
        "layouts": {k: _layout(layouts.get(k)) for k in kept},
        "settings": {"defaultTimeframe": True}
        if isinstance(settings, dict) and "defaultTimeframe" in settings
        else {},
    }


def _classic_tile(raw: dict[str, Any]) -> dict[str, Any]:
    metrics = [
        q["metric"]
        for q in dicts(raw.get("queries"), 50)
        if isinstance(q.get("metric"), str) and METRIC_KEY.fullmatch(q["metric"])
    ]
    out: dict[str, Any] = {
        "name": safe_name(str(raw.get("name", "")), "", 100),
        "tileType": str(raw.get("tileType", ""))[:40],
        "queries": [{"metric": m} for m in metrics],
    }
    if isinstance(raw.get("markdown"), str):
        out["markdown"] = _text(raw["markdown"])
    if isinstance(raw.get("tileFilter"), dict) and raw["tileFilter"].get("managementZone"):
        out["tileFilter"] = {"managementZone": True}
    return out


def classic_dashboard(body: dict[str, Any]) -> dict[str, Any] | None:
    """A classic dashboard without owner, tags, ids or filter values; None without tiles."""
    tiles = body.get("tiles")
    if not isinstance(tiles, list):
        return None
    meta = body.get("dashboardMetadata")
    meta = meta if isinstance(meta, dict) else {}
    dashboard_filter = meta.get("dashboardFilter")
    dynamic = meta.get("dynamicFilters")
    names = dynamic.get("filters") if isinstance(dynamic, dict) else None
    return {
        "dashboardMetadata": {
            "name": safe_name(str(meta.get("name", "")), "", 100),
            "owner": "",
            "dashboardFilter": {
                "managementZone": isinstance(dashboard_filter, dict)
                and bool(dashboard_filter.get("managementZone"))
            },
            "dynamicFilters": {
                "filters": [
                    n
                    for n in (names[:MAX_LIST_ITEMS] if isinstance(names, list) else [])
                    if isinstance(n, str) and FILTER_NAME.fullmatch(n)
                ]
            },
        },
        "tiles": [_classic_tile(t) for t in dicts(tiles, MAX_TILES)],
    }
