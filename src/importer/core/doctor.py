"""Preflight checks, so a new user meets setup problems before a real run. Read-only.

Requirements are only: Python, credentials that can read the source (AWS keys for CloudWatch, a
Google login for Google Cloud), and a New Relic API key (the last only for creating things).
Terraform is optional, for the data-ingestion and project-link steps.
"""

import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass

from importer.sources.cloudwatch.extract import AwsError, Reader, caller_account
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
