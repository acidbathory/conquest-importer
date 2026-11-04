"""Preflight checks, so a new user meets setup problems before a real run. Read-only.

Requirements are only: Python, AWS keys that can read CloudWatch, and a New Relic API key
(the last only for creating things). Terraform is optional, for the data-ingestion step.
"""

import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass

from importer.sources.cloudwatch.extract import AwsError, Reader, caller_account


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


def run_checks(
    region: str,
    profile: str | None,
    which: Callable[[str], str | None] = shutil.which,
    reader_factory: Callable[[str, str | None], Reader] = Reader,
    env: dict[str, str] | None = None,
) -> list[Check]:
    env = dict(os.environ) if env is None else env
    checks = [
        Check(
            "python",
            sys.version_info >= (3, 12),
            f"{sys.version_info.major}.{sys.version_info.minor} (3.12 or newer needed)",
        )
    ]
    try:
        account = caller_account(reader_factory(region, profile))
        checks.append(Check("aws keys", True, f"working; signed in to account {account}"))
    except (AwsError, ValueError) as exc:
        checks.append(Check("aws keys", False, str(exc)[:240]))
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
