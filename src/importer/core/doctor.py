"""Preflight checks, so a new user meets setup problems before a real run. Read-only."""

import shutil
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
) -> list[Check]:
    checks = [
        Check("aws cli", which("aws") is not None, "needed to read CloudWatch"),
        Check(
            "terraform",
            which("terraform") is not None,
            "only needed to validate or deploy the generated Terraform",
            required=False,
        ),
    ]
    if checks[0].ok:
        try:
            account = caller_account(reader_factory(region, profile))
            checks.append(Check("aws sign-in", True, f"signed in to account {account}"))
        except (AwsError, ValueError) as exc:
            checks.append(Check("aws sign-in", False, str(exc)[:200]))
    return checks


def render(checks: list[Check]) -> str:
    lines = []
    for c in checks:
        mark = "ok  " if c.ok else ("FAIL" if c.required else "warn")
        lines.append(f"[{mark}] {c.name}: {c.detail}")
    return "\n".join(lines) + "\n"


def passed(checks: list[Check]) -> bool:
    return all(c.ok for c in checks if c.required)
