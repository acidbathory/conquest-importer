"""Read-only Datadog client that lists monitors.

Security notes:
- Only official Datadog sites are allowed, so a bad DD_SITE cannot redirect
  API keys to another host.
- Redirects are not followed (a redirect could carry headers elsewhere).
- Only GET requests are made.
- Error messages are passed through redact() before being raised.
"""

import contextlib
import logging
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from importer.core.secrets import DatadogCredentials, redact
from importer.core.tls import system_tls_context
from importer.sources.datadog.models import DatadogMonitor

log = logging.getLogger(__name__)

ALLOWED_SITES = frozenset(
    {
        "datadoghq.com",
        "us3.datadoghq.com",
        "us5.datadoghq.com",
        "datadoghq.eu",
        "ap1.datadoghq.com",
        "ap2.datadoghq.com",
        "ddog-gov.com",
    }
)
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_WAIT_SECONDS = 60.0
MAX_ERROR_BODY = 300


class InvalidSiteError(ValueError):
    """DD_SITE is not an official Datadog site."""


class DatadogAPIError(RuntimeError):
    """A Datadog API call failed."""


class DatadogClient:
    def __init__(
        self,
        creds: DatadogCredentials,
        *,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 3,
        page_size: int = 100,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if creds.site not in ALLOWED_SITES:
            raise InvalidSiteError(
                f"Unsupported Datadog site {creds.site!r}; allowed: {sorted(ALLOWED_SITES)}"
            )
        self._secrets = [creds.api_key, creds.app_key]
        self._max_retries = max_retries
        self._page_size = page_size
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=f"https://api.{creds.site}",
            headers={
                "DD-API-KEY": creds.api_key.get_secret_value(),
                "DD-APPLICATION-KEY": creds.app_key.get_secret_value(),
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(30.0),
            follow_redirects=False,
            transport=transport,
            verify=system_tls_context(),
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "DatadogClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def list_monitors(self) -> Iterator[DatadogMonitor]:
        page = 0
        while True:
            batch = self._get("/api/v1/monitor", {"page": page, "page_size": self._page_size})
            if not isinstance(batch, list):
                raise DatadogAPIError("Unexpected response shape from monitor list")
            for item in batch:
                yield DatadogMonitor.model_validate(item)
            if len(batch) < self._page_size:
                return
            page += 1

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._http.get(path, params=params)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise DatadogAPIError(
                        redact(f"Network error calling Datadog: {exc}", self._secrets)
                    ) from None
                self._wait(attempt, None)
                continue

            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in RETRYABLE_STATUS and attempt < self._max_retries:
                self._wait(attempt, resp.headers.get("X-RateLimit-Reset"))
                continue
            raise DatadogAPIError(self._describe_failure(resp))
        raise DatadogAPIError("Retries exhausted")  # pragma: no cover

    def _wait(self, attempt: int, reset_header: str | None) -> None:
        delay = 2.0**attempt
        if reset_header:
            with contextlib.suppress(ValueError):
                delay = max(delay, float(reset_header))
        delay = min(delay, MAX_WAIT_SECONDS)
        log.warning("Datadog request failed; retrying in %.1fs", delay)
        self._sleep(delay)

    def _describe_failure(self, resp: httpx.Response) -> str:
        hint = ""
        if resp.status_code in (401, 403):
            hint = " (check DD_API_KEY, DD_APP_KEY, DD_SITE and the monitors_read scope)"
        body = redact(resp.text[:MAX_ERROR_BODY], self._secrets)
        return f"Datadog returned HTTP {resp.status_code}{hint}: {body}"
