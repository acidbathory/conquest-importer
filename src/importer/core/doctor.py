"""Preflight checks, so a new user meets setup problems before a real run. Read-only.

Requirements are only: Python, credentials that can read the source (AWS keys for CloudWatch, a
Google login for Google Cloud, a token and tenant address for Dynatrace), and a New
Relic API key (the last only for creating things).
Terraform is optional, for the data-ingestion and project-link steps.
"""

import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from importer.sources.cloudwatch.extract import AwsError, Reader, caller_account
from importer.sources.dynatrace.extract import is_env_url, is_plain_token
from importer.sources.gcp.extract import GcpError, access_token


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


def _dynatrace_checks(env: dict[str, str]) -> list[Check]:
    """Settings for the Dynatrace source, checked without any network call."""
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
    url_ok = is_env_url(env.get("DYNATRACE_ENV_URL", "").strip())
    url_detail = (
        "DYNATRACE_ENV_URL looks right"
        if url_ok
        else "DYNATRACE_ENV_URL must look like https://<tenant id>.apps.dynatrace.com"
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
    elif source == "dynatrace":
        checks += _dynatrace_checks(env)
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
