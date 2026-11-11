"""Read Azure Monitor alert rules from one subscription, read-only.

Only GET requests to `management.azure.com` and the one token POST to
`login.microsoftonline.com` can run. Paths come from the fixed READ table and a subscription id that
matched a UUID, so no user text becomes a path or an operation. `nextLink` values are followed only
when they point at the same host, the same path and https. Redirects are never followed, a 429 is
retried after its `Retry-After` (capped), and pages, bytes and time are capped for the whole run.

The token comes from `AZURE_ACCESS_TOKEN`, or from the client-credentials flow with
`AZURE_TENANT_ID`, `AZURE_CLIENT_ID` and `AZURE_CLIENT_SECRET` (scope
`https://management.azure.com/.default`). Neither is written anywhere, and both are scrubbed from
every message. Secrets in the resources (webhook URLs, callback URLs, secure webhook ids, passwords)
are dropped at read time by `sanitize.py`. The export is written to a private temporary folder and
renamed into place (folder 0700, files 0600), so a failed run leaves nothing behind.

The export holds the JSON `convert --source azure` reads: one `{"value": [...]}` file per type.
It still holds subscription ids and resource names and email addresses: review it before sharing.
"""

import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit

import httpx

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.safe_http import ReadBudget, decode_json, is_plain_token, scrub
from importer.sources.azure.dashboard_sanitize import sanitize_workbook
from importer.sources.azure.sanitize import sanitize_resource

ARM_HOST = "management.azure.com"
LOGIN_HOST = "login.microsoftonline.com"
ALLOWED_HOSTS = frozenset({ARM_HOST, LOGIN_HOST})
SCOPE = "https://management.azure.com/.default"
SERVICE = "Azure"
UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
# key in the export -> (path below /subscriptions/{id}/providers/, api-version) from
# docs/research/azure-monitor-alerting.md section 2
READS: dict[str, tuple[str, str]] = {
    "metricAlerts": ("Microsoft.Insights/metricAlerts", "2026-01-01"),
    "scheduledQueryRules": ("Microsoft.Insights/scheduledQueryRules", "2026-03-01"),
    "activityLogAlerts": ("Microsoft.Insights/activityLogAlerts", "2026-01-01"),
    "actionGroups": ("Microsoft.Insights/actionGroups", "2023-01-01"),
    "alertProcessingRules": ("Microsoft.AlertsManagement/actionRules", "2021-08-08"),
    "prometheusRuleGroups": ("Microsoft.AlertsManagement/prometheusRuleGroups", "2023-03-01"),
    "smartDetectorAlertRules": (
        "microsoft.alertsManagement/smartDetectorAlertRules",
        "2021-04-01",
    ),
}
# Workbooks and portal dashboards are read only with dashboards=True, so the alert export is
# unchanged. Versions from docs/research/azure-monitor-alerting.md section 2 (the 2023-06-01
# workbooks swagger; portal dashboards have preview versions only).
DASHBOARD_READS: dict[str, tuple[str, str]] = {
    "workbooks": ("Microsoft.Insights/workbooks", "2023-06-01"),
    "portalDashboards": ("Microsoft.Portal/dashboards", "2025-04-01-preview"),
}
# `category` is required by the workbooks list; `canFetchContent` makes it return serializedData.
DASHBOARD_QUERY: dict[str, dict[str, str]] = {
    "workbooks": {"category": "workbook", "canFetchContent": "true"},
}
NOT_AVAILABLE = (
    "not available: Azure answered {error} for this subscription (the Microsoft.Portal/dashboards "
    "API is preview only). Download the dashboards from the portal (Dashboard > Download) into "
    "the export folder and run convert-dashboards --source azure on it"
)
MAX_PAGES = 100
MAX_TOTAL_PAGES = 400  # across every list in one run
MAX_ITEMS = 20000
MAX_RETRIES = 5
MAX_RETRY_WAIT = 60
DEFAULT_RETRY_WAIT = 5
SUMMARY = "extract-summary.json"

Getter = Callable[[str, dict[str, str] | None], dict[str, Any]]


class AzureError(Exception):
    """Reading from Azure failed; the message is safe to show."""


def _env_id(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise AzureError(f"{name} is not set (see docs/user/azure-credentials.md)")
    if not UUID.fullmatch(value):
        raise AzureError(f"{name} must be a UUID such as 00000000-0000-0000-0000-000000000000")
    return value


def _login(
    client: httpx.Client, env: Mapping[str, str]
) -> tuple[str, str]:  # (access token, client secret)
    tenant = _env_id(env, "AZURE_TENANT_ID")
    client_id = _env_id(env, "AZURE_CLIENT_ID")
    secret = env.get("AZURE_CLIENT_SECRET", "").strip()
    if not secret:
        raise AzureError(
            "AZURE_CLIENT_SECRET is not set. Set it, or set AZURE_ACCESS_TOKEN to a token "
            "from `az account get-access-token`"
        )
    if not is_plain_token(secret):
        raise AzureError("AZURE_CLIENT_SECRET must be plain ASCII text with no spaces")
    form = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": secret,
        "scope": SCOPE,
    }
    url = f"https://{LOGIN_HOST}/{tenant}/oauth2/v2.0/token"
    try:
        with client.stream("POST", url, data=form) as response:
            body = decode_json(response, secret, AzureError, "Azure sign-in")
    except httpx.HTTPError as exc:
        raise AzureError(f"could not reach Azure sign-in: {type(exc).__name__}") from None
    token = body.get("access_token")
    if not isinstance(token, str) or not is_plain_token(token):
        raise AzureError("Azure sign-in did not return a usable access token")
    return token, secret


def retry_wait(header: str | None) -> float:
    """Seconds to wait for a `Retry-After` header, within bounds; a date or junk gets a default."""
    if header is not None and header.strip().isdigit():
        return float(min(int(header.strip()), MAX_RETRY_WAIT))
    return float(DEFAULT_RETRY_WAIT)


class Session:
    """A signed-in read session: `with open_client(...) as get:` gives the Getter."""

    def __init__(
        self,
        token: str,
        secret: str,
        transport: httpx.BaseTransport | None,
        sleep: Callable[[float], None],
    ) -> None:
        self.token, self.secret, self.sleep = token, secret, sleep
        self.budget = ReadBudget()
        self.client = httpx.Client(
            timeout=60,
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
            follow_redirects=False,
        )

    def __enter__(self) -> Getter:
        return self.get

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        self.client.close()

    def get(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        host = urlsplit(url).hostname
        if host != ARM_HOST:  # the bearer token goes to one host only
            raise AzureError(f"refused to send the token to {host or 'an unknown host'}")
        try:
            for attempt in range(MAX_RETRIES + 1):
                self.budget.check_time(AzureError, SERVICE)
                wait: float | None = None
                with self.client.stream("GET", url, params=params or None) as response:
                    if response.status_code == 429 and attempt < MAX_RETRIES:
                        wait = retry_wait(response.headers.get("retry-after"))
                    else:
                        return decode_json(response, self.token, AzureError, SERVICE, self.budget)
                self.sleep(wait)
            raise AzureError(f"{SERVICE} kept throttling the request; stopped")  # pragma: no cover
        except httpx.HTTPError as exc:
            raise AzureError(f"could not reach {SERVICE}: {type(exc).__name__}") from None
        except AzureError as exc:  # the client secret can be echoed too
            raise AzureError(scrub(scrub(str(exc), self.token), self.secret)) from None


def open_client(
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    env: Mapping[str, str] | None = None,
) -> Session:
    """Sign in (or take AZURE_ACCESS_TOKEN) and return the session; `transport` is for tests."""
    values = os.environ if env is None else env
    given = values.get("AZURE_ACCESS_TOKEN", "").strip()
    if given:
        if not is_plain_token(given):
            raise AzureError(
                "AZURE_ACCESS_TOKEN must be plain ASCII text with no spaces; check that it was "
                "pasted without a smart quote or a line break"
            )
        return Session(given, "", transport, sleep)
    with httpx.Client(timeout=60, transport=transport, follow_redirects=False) as login:
        token, secret = _login(login, values)
    return Session(token, secret, transport, sleep)


def subscription_id(given: str | None, env: Mapping[str, str] | None = None) -> str:
    values = os.environ if env is None else env
    text = (given or values.get("AZURE_SUBSCRIPTION_ID", "")).strip()
    if not UUID.fullmatch(text):
        raise ValueError(
            "the subscription must be a UUID: pass --subscription or set AZURE_SUBSCRIPTION_ID"
        )
    return text.lower()


class PageBudget:
    def __init__(self, pages: int) -> None:
        self.left = pages

    def spend(self) -> None:
        if self.left <= 0:
            raise AzureError(f"more than {MAX_TOTAL_PAGES} pages in total; stopped")
        self.left -= 1


def _check_next_link(link: str, path: str) -> None:
    parts = urlsplit(link)
    same_place = (
        parts.scheme == "https"
        and parts.netloc.lower() == ARM_HOST
        and parts.path.lower() == path.lower()
        and not parts.fragment
    )
    if not same_place:
        raise AzureError("a page link pointed somewhere other than the list being read; stopped")


def _list_all(get: Getter, sub: str, key: str, budget: PageBudget) -> list[dict[str, Any]]:
    provider, version = {**READS, **DASHBOARD_READS}[key]
    path = f"/subscriptions/{sub}/providers/{provider}"
    url = f"https://{ARM_HOST}{path}"
    params = {"api-version": version, **DASHBOARD_QUERY.get(key, {})}
    items: list[dict[str, Any]] = []
    for _ in range(MAX_PAGES):
        budget.spend()
        page = get(url, params)
        found = page.get("value", [])
        if isinstance(found, list):
            items += [i for i in found if isinstance(i, dict)]
        if len(items) > MAX_ITEMS:
            raise AzureError(f"more than {MAX_ITEMS} items of {key}; stopped")
        link = page.get("nextLink")
        if not isinstance(link, str) or not link:
            return items
        _check_next_link(link, path)
        url, params = link, {}
    raise AzureError(f"more than {MAX_PAGES} pages of {key}; stopped")


@dataclass
class ExtractResult:
    subscription: str
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    secrets_dropped: int = 0


def _write_private(path: Path, text: str) -> None:
    if len(text) > MAX_INPUT_BYTES:
        raise AzureError(f"{path.name} is larger than the converter accepts")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def _publish(files: dict[str, str], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".azure-extract-", dir=out.parent))
    try:
        for name, text in files.items():
            _write_private(temp / name, text)
        temp.chmod(0o700)
        if out.exists():
            out.rmdir()  # an empty folder the caller made
        temp.rename(out)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def _clean(item: dict[str, Any], key: str) -> tuple[dict[str, Any], int]:
    if key == "workbooks":
        return sanitize_workbook(item)
    return sanitize_resource(item, key)


def _unavailable(error: AzureError) -> bool:
    return bool(re.search(r"returned 5\d\d\b", str(error)))


def extract(get: Getter, subscription: str, out: Path, dashboards: bool = False) -> ExtractResult:
    """Write one `<type>.json` per resource type under a new or empty `out`.

    With `dashboards`, workbooks and portal dashboards are read as well. A 5xx answer for a
    dashboard type is reported as "not available" and the rest of the export still succeeds.
    """
    if not UUID.fullmatch(subscription):
        raise ValueError(
            "the subscription must be a UUID such as 00000000-0000-0000-0000-000000000000"
        )
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} already has files; pass a new folder")
    sub = subscription.lower()
    result = ExtractResult(sub)
    budget = PageBudget(MAX_TOTAL_PAGES)
    files: dict[str, str] = {}
    for key in [*READS, *(DASHBOARD_READS if dashboards else ())]:
        try:
            items = _list_all(get, sub, key, budget)
        except AzureError as exc:
            if key in DASHBOARD_READS and _unavailable(exc):
                result.warnings.append(f"{key}: " + NOT_AVAILABLE.format(error=exc))
            else:
                result.warnings.append(f"{key}: {exc}")
            continue
        clean: list[dict[str, Any]] = []
        for item in items:
            safe, dropped = _clean(item, key)
            clean.append(safe)
            result.secrets_dropped += dropped
        result.counts[key] = len(clean)
        files[f"{key}.json"] = json.dumps({"value": clean}, indent=2) + "\n"
    if not files:
        first = result.warnings[0] if result.warnings else "no list was read"
        raise AzureError(f"nothing could be read: {first}")
    summary = {
        "subscription": sub,
        "readAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "counts": result.counts,
        "apiVersions": {k: {**READS, **DASHBOARD_READS}[k][1] for k in result.counts},
        "warnings": result.warnings,
        "secretsDropped": result.secrets_dropped,
    }
    files[SUMMARY] = json.dumps(summary, indent=2) + "\n"
    _publish(files, out)
    return result
