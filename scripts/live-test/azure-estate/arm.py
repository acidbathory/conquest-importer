"""Minimal Azure Resource Manager client for the ckg- test estate.

Secrets come only from the environment (or a .env file read without echoing). The token and the
client secret are never printed, logged or put into an error message. TLS verification stays on;
set SSL_CERT_FILE when a corporate proxy re-signs traffic.
"""

import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

MGMT = "https://management.azure.com"
REQUIRED = ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_SUBSCRIPTION_ID")


class ArmError(RuntimeError):
    """An ARM call failed. Holds the status and the error code, never a secret."""

    def __init__(self, method: str, path: str, status: int, code: str, message: str) -> None:
        super().__init__(f"{method} {path} -> {status} {code}: {message}")
        self.status = status
        self.code = code


def parse_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        out[key.strip()] = value.strip().strip("\"'")
    return out


def load_env(dotenv: Path | None = None) -> dict[str, str]:
    """Process environment first, then the .env file for names that are not set."""
    env = {k: os.environ[k] for k in REQUIRED if k in os.environ}
    if dotenv and dotenv.exists():
        for k, v in parse_dotenv(dotenv).items():
            if k in REQUIRED:
                env.setdefault(k, v)
    return env


class Arm:
    def __init__(
        self, env: Mapping[str, str], transport: httpx.BaseTransport | None = None
    ) -> None:
        missing = [k for k in REQUIRED if not env.get(k)]
        if missing:
            raise SystemExit("missing environment variables: " + ", ".join(missing))
        self._env = dict(env)
        self.subscription = self._env["AZURE_SUBSCRIPTION_ID"]
        self._http = httpx.Client(transport=transport, timeout=60.0)
        self._token = ""
        self._expires = 0.0

    def close(self) -> None:
        self._http.close()

    def _bearer(self) -> str:
        if self._token and time.time() < self._expires:
            return self._token
        url = f"https://login.microsoftonline.com/{self._env['AZURE_TENANT_ID']}/oauth2/v2.0/token"
        resp = self._http.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._env["AZURE_CLIENT_ID"],
                "client_secret": self._env["AZURE_CLIENT_SECRET"],
                "scope": f"{MGMT}/.default",
            },
        )
        if resp.status_code != 200:
            raise ArmError("POST", "token", resp.status_code, "TokenRequestFailed", "see portal")
        self._token = resp.json()["access_token"]
        self._expires = time.time() + 3000
        return self._token

    def request(self, method: str, path: str, api: str | None, body: Any = None) -> tuple[int, Any]:
        url = path if path.startswith("https://") else MGMT + path
        # copy_merge_params keeps a query already in the URL (nextLink, ?forceDelete=true)
        target = httpx.URL(url).copy_merge_params({"api-version": api} if api else {})
        resp = self._http.request(
            method,
            target,
            json=body,
            headers={"Authorization": f"Bearer {self._bearer()}"},
        )
        if resp.status_code >= 400:
            try:
                err = resp.json().get("error", {})
            except ValueError:
                err = {}
            raise ArmError(
                method,
                path.split("?")[0],
                resp.status_code,
                str(err.get("code", "")),
                str(err.get("message", ""))[:300],
            )
        return resp.status_code, (resp.json() if resp.content else None)

    def get(self, path: str, api: str | None) -> Any:
        return self.request("GET", path, api)[1]

    def put(self, path: str, api: str, body: Any) -> Any:
        return self.request("PUT", path, api, body)[1]

    def delete(self, path: str, api: str) -> int:
        return self.request("DELETE", path, api)[0]

    def list_all(self, path: str, api: str) -> list[Any]:
        items: list[Any] = []
        page = self.get(path, api)
        while True:
            items.extend(page.get("value", []))
            nxt = page.get("nextLink")
            if not nxt:
                return items
            page = self.get(nxt, None)
