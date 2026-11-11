"""Preflight checks, so a new user meets setup problems before a real run. Read-only.

Requirements are only: Python, credentials that can read the source (AWS keys for CloudWatch, a
Google login for Google Cloud, a token and tenant address for Dynatrace, a service account token and
stack address for Grafana, a service principal for Azure), and a New Relic API key (the last only
for creating things).
Terraform is optional, for the data-ingestion and project-link steps.
"""

import os
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from importer.core.dynatrace_env import CLASSIC_HOST, is_plain_token, is_platform_url
from importer.sources.cloudwatch.extract import AwsError, Reader, caller_account
from importer.sources.gcp.extract import GcpError, access_token
from importer.sources.grafana.extract import validate_host as grafana_host

DOCTOR_SOURCES = (
    "cloudwatch",
    "gcp",
    "dynatrace",
    "dynatrace-classic",
    "grafana",
    "azure",
    "datadog",
)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


def _aws_login(
    region: str, profile: str | None, reader_factory: Callable[[str, str | None], Reader]
) -> Check:
    try:
        account = caller_account(reader_factory(region, profile))
    except (AwsError, ValueError) as exc:
        return Check("aws keys", False, str(exc)[:240])
    return Check("aws keys", True, f"working; signed in to account {account}")


def _google_login(token_factory: Callable[[], str]) -> Check:
    try:
        token_factory()
    except GcpError as exc:
        return Check("google login", False, str(exc)[:240])
    return Check("google login", True, "an access token is available (it is not shown)")


def _dynatrace_checks(env: dict[str, str], *, classic: bool = False) -> list[Check]:
    """Settings for a Dynatrace source, checked without any network call. The classic source
    also accepts a *.live.dynatrace.com address."""
    token = env.get("DYNATRACE_API_TOKEN", "").strip()
    token_ok = is_plain_token(token)
    if not token:
        auth_note = "DYNATRACE_API_TOKEN is not set (a platform token, see .env.example)"
    elif not token_ok:
        auth_note = (
            "DYNATRACE_API_TOKEN is not plain ASCII text without spaces; check for a smart "
            "quote, a dash or a line break from pasting"
        )
    else:
        auth_note = "DYNATRACE_API_TOKEN is set and plain text (it is not shown)"
    url = env.get("DYNATRACE_ENV_URL", "").strip()
    url_ok = is_platform_url(url) or (
        classic and CLASSIC_HOST.fullmatch(url.removesuffix("/")) is not None
    )
    shape = "https://<tenant id>.apps.dynatrace.com"
    if classic:
        shape += " (or https://<tenant id>.live.dynatrace.com)"
    url_detail = (
        "DYNATRACE_ENV_URL looks right" if url_ok else f"DYNATRACE_ENV_URL must look like {shape}"
    )
    cert = env.get("SSL_CERT_FILE", "")
    cert_ok = bool(cert) and Path(cert).is_file()
    cert_detail = (
        "SSL_CERT_FILE points to a file (it is used for the Dynatrace connection)"
        if cert_ok
        else "SSL_CERT_FILE is not set to a file; behind a network that inspects TLS, set it "
        "to a certificate bundle that includes the company root"
    )
    return [
        Check("dynatrace token", token_ok, auth_note),
        Check("dynatrace url", url_ok, url_detail),
        Check("tls certificates", cert_ok, cert_detail, required=False),
    ]


def _grafana_checks(env: dict[str, str]) -> list[Check]:
    """Settings for the Grafana source, checked without any network call."""
    token = env.get("GRAFANA_SA_TOKEN", "").strip()
    token_ok = is_plain_token(token)
    if not token:
        auth_note = "GRAFANA_SA_TOKEN is not set (a service account token, see .env.example)"
    elif not token_ok:
        auth_note = (
            "GRAFANA_SA_TOKEN is not plain ASCII text without spaces; check for a smart "
            "quote, a dash or a line break from pasting"
        )
    else:
        auth_note = "GRAFANA_SA_TOKEN is set and plain text (it is not shown)"
    url = env.get("GRAFANA_URL", "").strip()
    try:
        grafana_host(url)
        url_ok, url_detail = True, "GRAFANA_URL is a Grafana Cloud stack address"
    except ValueError as exc:
        host = url.lower().removeprefix("https://").rstrip("/")
        try:
            grafana_host(url, host)
        except ValueError:
            url_ok, url_detail = False, str(exc)
        else:
            url_ok = True
            url_detail = (
                f"GRAFANA_URL is a self-hosted address; run extract-grafana with "
                f"--allow-host {host}"
            )
    return [
        Check("grafana token", token_ok, auth_note),
        Check("grafana url", url_ok, url_detail),
    ]


def _azure_checks(env: dict[str, str]) -> list[Check]:
    """Settings for the Azure source, checked without any network call or showing a value."""
    guid = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
    given = env.get("AZURE_ACCESS_TOKEN", "").strip()
    secret = env.get("AZURE_CLIENT_SECRET", "").strip()
    if given:
        ok = is_plain_token(given)
        detail = (
            "AZURE_ACCESS_TOKEN is set and plain text (it is not shown)"
            if ok
            else "AZURE_ACCESS_TOKEN is not plain ASCII text without spaces; check for a smart "
            "quote, a dash or a line break from pasting"
        )
        creds = Check("azure credentials", ok, detail)
    else:
        missing = [
            n
            for n in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")
            if not env.get(n, "").strip()
        ]
        bad_ids = [
            n
            for n in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID")
            if env.get(n, "").strip() and not guid.fullmatch(env[n].strip())
        ]
        if missing:
            creds = Check(
                "azure credentials",
                False,
                f"{', '.join(missing)} not set (a read-only service principal, see "
                "docs/user/azure-credentials.md), or set AZURE_ACCESS_TOKEN",
            )
        elif bad_ids:
            creds = Check("azure credentials", False, f"{', '.join(bad_ids)} must be a UUID")
        elif not is_plain_token(secret):
            creds = Check(
                "azure credentials",
                False,
                "AZURE_CLIENT_SECRET is not plain ASCII text without spaces; check for a smart "
                "quote, a dash or a line break from pasting",
            )
        else:
            creds = Check(
                "azure credentials",
                True,
                "tenant, client id and secret are set and look right (none is shown)",
            )
    sub = env.get("AZURE_SUBSCRIPTION_ID", "").strip()
    sub_ok = bool(guid.fullmatch(sub))
    subscription = Check(
        "azure subscription",
        sub_ok,
        "AZURE_SUBSCRIPTION_ID is a UUID"
        if sub_ok
        else "AZURE_SUBSCRIPTION_ID is not set to a UUID; pass --subscription to extract-azure",
        required=False,
    )
    return [creds, subscription]


def run_checks(
    region: str,
    profile: str | None,
    which: Callable[[str], str | None] = shutil.which,
    reader_factory: Callable[[str, str | None], Reader] = Reader,
    env: dict[str, str] | None = None,
    source: str = "cloudwatch",
    token_factory: Callable[[], str] = access_token,
) -> list[Check]:
    env = dict(os.environ) if env is None else env
    checks = [
        Check(
            "python",
            sys.version_info >= (3, 12),
            f"{sys.version_info.major}.{sys.version_info.minor} (3.12 or newer needed)",
        )
    ]
    if source == "gcp":
        checks.append(_google_login(token_factory))
    elif source in ("dynatrace", "dynatrace-classic"):
        checks += _dynatrace_checks(env, classic=source == "dynatrace-classic")
    elif source == "grafana":
        checks += _grafana_checks(env)
    elif source == "azure":
        checks += _azure_checks(env)
    elif source == "datadog":
        checks.append(Check("datadog input", True, "no login needed: it reads exported JSON files"))
    else:
        checks.append(_aws_login(region, profile, reader_factory))
    has_key = bool(env.get("NEW_RELIC_API_KEY"))
    checks.append(
        Check(
            "new relic api key",
            has_key,
            "NEW_RELIC_API_KEY is set"
            if has_key
            else "NEW_RELIC_API_KEY is not set (only needed to create things in New Relic)",
            required=False,
        )
    )
    checks.append(
        Check(
            "terraform",
            which("terraform") is not None,
            "only needed to deploy the generated data-ingestion Terraform",
            required=False,
        )
    )
    return checks


def render(checks: list[Check]) -> str:
    lines = []
    for c in checks:
        mark = "ok  " if c.ok else ("FAIL" if c.required else "warn")
        lines.append(f"[{mark}] {c.name}: {c.detail}")
    return "\n".join(lines) + "\n"


def passed(checks: list[Check]) -> bool:
    return all(c.ok for c in checks if c.required)
