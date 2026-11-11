"""Extra read-only reads for the Google extractor: log-based metrics, SLOs and uptime details.

An alert policy names a log-based metric (`logging.googleapis.com/user/<name>`), an SLO
(`select_slo_burn_rate("<slo name>", ...)`) or an uptime check (`check_id`) but does not carry the
definition. The definitions are in the Logging API (`logMetrics`) and the Monitoring API (services,
serviceLevelObjectives, uptimeCheckConfigs). Same rules as `extract.py`: GET only; every URL is
built from a fixed template, the project id (matched against PROJECT_ID) and a service id that
matched SERVICE_ID, so no server text becomes a path segment; pages and bytes are capped by the
shared PageBudget and ReadBudget; the token never appears in a message (core/safe_http.py).

What is kept: filters, extractors, goals and periods. What is dropped: descriptions, user labels,
the password of a basic-auth uptime check, header values and the request body (they can hold
secrets); only the header names are kept.
"""

import re
from typing import Any

from importer.core.redact import safe_name, safe_path
from importer.core.text import printable
from importer.sources.gcp.extract import (
    API,
    DEFAULT_VERSION,
    MAX_PAGES,
    PROJECT_ID,
    GcpError,
    Getter,
    PageBudget,
)

LOGGING_API = "https://logging.googleapis.com/v2"
# the only paths this module reads, below the project (GET); {s} is a validated service id
READ_TEMPLATES = {
    "logMetrics": f"{LOGGING_API}/projects/{{p}}/metrics",
    "services": f"{API}/{DEFAULT_VERSION}/projects/{{p}}/services",
    "serviceLevelObjectives": f"{API}/{DEFAULT_VERSION}/projects/{{p}}/services/{{s}}/"
    "serviceLevelObjectives",
}
SERVICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.~-]{0,99}")
HOSTNAME = re.compile(
    r"(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9]"
    r"([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
)
MAX_SERVICES = 200
MAX_FILTER = 20_000

LOG_METRICS_KEY = "log-metrics"
SLOS_KEY = "slos"


def _paged(get: Getter, url: str, key: str, budget: PageBudget) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    params: dict[str, str] = {"pageSize": "100"}
    for _ in range(MAX_PAGES):
        budget.spend()
        page = get(url, params)
        found = page.get(key, [])
        if isinstance(found, list):
            items += [i for i in found if isinstance(i, dict)]
        token = page.get("nextPageToken")
        if not isinstance(token, str) or not token:
            return items
        params = {"pageSize": "100", "pageToken": token}
    raise GcpError(f"more than {MAX_PAGES} pages of {key}; stopped")


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: object, limit: int = MAX_FILTER) -> str:
    return printable(value, limit) if isinstance(value, str) else ""


def log_metric_summary(metric: dict[str, Any]) -> dict[str, Any]:
    """The definition that decides what a log-based metric counts; descriptions are dropped."""
    descriptor = _dict(metric.get("metricDescriptor"))
    labels = descriptor.get("labels")
    extractors = metric.get("labelExtractors")
    buckets = metric.get("bucketOptions")
    return {
        "name": str(metric.get("name", "")).rsplit("/", 1)[-1],
        "filter": _text(metric.get("filter")),
        "disabled": metric.get("disabled") is True,
        "metricKind": _text(descriptor.get("metricKind"), 40),
        "valueType": _text(descriptor.get("valueType"), 40),
        "unit": _text(descriptor.get("unit"), 40),
        "labels": [_text(label.get("key"), 100) for label in labels if isinstance(label, dict)]
        if isinstance(labels, list)
        else [],
        "valueExtractor": _text(metric.get("valueExtractor")),
        "labelExtractors": {_text(k, 100): _text(v, 2000) for k, v in extractors.items()}
        if isinstance(extractors, dict)
        else {},
        "bucketOptions": buckets if isinstance(buckets, dict) else {},
    }


def slo_summary(slo: dict[str, Any]) -> dict[str, Any]:
    """Goal, period and indicator (metric filters only); the display name is masked if needed."""
    tail = str(slo.get("name", "")).rsplit("/", 1)[-1]
    sli = slo.get("serviceLevelIndicator")
    return {
        "name": _text(slo.get("name"), 500),
        "displayName": safe_name(str(slo.get("displayName", "")), tail, 100),
        "goal": slo.get("goal") if isinstance(slo.get("goal"), int | float) else None,
        "rollingPeriod": _text(slo.get("rollingPeriod"), 20),
        "calendarPeriod": _text(slo.get("calendarPeriod"), 40),
        "serviceLevelIndicator": sli if isinstance(sli, dict) else {},
    }


def uptime_details(check: dict[str, Any]) -> dict[str, Any]:
    """What a synthetic monitor needs from an uptime check, with every secret dropped.

    The host is kept only when it is a plain host name or address. A basic-auth password, header
    values, the request body and the user name are not kept: `auth` says that a credential
    existed, so the plan can ask for a secret reference.
    """
    http = _dict(check.get("httpCheck"))
    tcp = _dict(check.get("tcpCheck"))
    resource = _dict(check.get("monitoredResource"))
    labels = _dict(resource.get("labels"))
    host = str(labels.get("host", ""))
    headers = http.get("headers")
    auth = http.get("authInfo")
    matchers = check.get("contentMatchers")
    regions = check.get("selectedRegions")
    internal = check.get("internalCheckers")
    port = http.get("port", tcp.get("port"))
    return {
        "id": str(check.get("name", "")).rsplit("/", 1)[-1],
        "host": host if HOSTNAME.fullmatch(host) else "",
        "resourceType": _text(resource.get("type"), 60),
        "method": _text(http.get("requestMethod"), 20) or ("GET" if http else ""),
        "useSsl": http.get("useSsl") is True,
        "validateSsl": http.get("validateSsl") is True,
        "port": port if isinstance(port, int) and 0 < port < 65536 else None,
        "timeout": _text(check.get("timeout"), 20),
        "regions": [_text(r, 40) for r in regions if isinstance(r, str)]
        if isinstance(regions, list)
        else [],
        "checkerType": _text(check.get("checkerType"), 40),
        "internal": bool(internal) or bool(check.get("isInternal")),
        "auth": "basic" if isinstance(auth, dict) and auth else "",
        "headerNames": sorted(_text(k, 100) for k in headers) if isinstance(headers, dict) else [],
        "hasBody": bool(http.get("body")),
        "contentMatchers": [
            {"content": _text(m.get("content"), 500), "matcher": _text(m.get("matcher"), 40)}
            for m in matchers
            if isinstance(m, dict)
        ]
        if isinstance(matchers, list)
        else [],
        "pathSafe": safe_path(str(http.get("path", ""))) if http else "",
    }


def read_log_metrics(get: Getter, project: str, budget: PageBudget) -> list[dict[str, Any]]:
    _check(project)
    url = READ_TEMPLATES["logMetrics"].format(p=project)
    return [log_metric_summary(m) for m in _paged(get, url, "metrics", budget)]


def read_slos(get: Getter, project: str, budget: PageBudget) -> list[dict[str, Any]]:
    """Every SLO of every service of the project (at most MAX_SERVICES services)."""
    _check(project)
    services = _paged(get, READ_TEMPLATES["services"].format(p=project), "services", budget)
    slos: list[dict[str, Any]] = []
    for service in services[:MAX_SERVICES]:
        sid = str(service.get("name", "")).rsplit("/", 1)[-1]
        if not SERVICE_ID.fullmatch(sid):
            continue  # an id that is not plain text never reaches a URL
        url = READ_TEMPLATES["serviceLevelObjectives"].format(p=project, s=sid)
        slos += [slo_summary(s) for s in _paged(get, url, "serviceLevelObjectives", budget)]
    if len(services) > MAX_SERVICES:
        raise GcpError(f"more than {MAX_SERVICES} services; the SLOs of the rest were not read")
    return slos


def _check(project: str) -> None:
    if not PROJECT_ID.match(project):
        raise ValueError("--project must be a Google Cloud project id such as my-project-123")


def read_plus(
    get: Getter, project: str, budget: PageBudget, result: Any
) -> list[tuple[str, str, list[dict[str, Any]]]]:
    """The (folder, file, data) triples to write for the extra reads that succeeded.

    A read that fails is a warning and writes nothing, so a later convert can tell "not read"
    from "read, and there are none".
    """
    out: list[tuple[str, str, list[dict[str, Any]]]] = []
    for key, read in ((LOG_METRICS_KEY, read_log_metrics), (SLOS_KEY, read_slos)):
        try:
            data = read(get, project, budget)
        except GcpError as exc:
            result.warnings.append(f"{key}: {exc}")
            continue
        setattr(result, key.replace("-", "_"), len(data))
        out.append((key, f"{key}.json", data))
    return out
