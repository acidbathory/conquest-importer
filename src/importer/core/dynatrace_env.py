"""The Dynatrace settings every Dynatrace reader and `doctor` share: token, tenant address, hosts.

The three extractors (workflows, classic alerting objects, inventory) differ in the scopes they ask
for and in the error type they raise, so `env_token` takes the scope text and raises
`TokenProblem`; each extractor turns that into its own error with the same message.
"""

import os
import re

from importer.core.safe_http import is_plain_token

PLATFORM_HOST = re.compile(r"https://[a-z0-9]+\.apps\.dynatrace\.com")
CLASSIC_HOST = re.compile(r"https://[a-z0-9]+\.live\.dynatrace\.com")

__all__ = [
    "CLASSIC_HOST",
    "PLATFORM_HOST",
    "TokenProblem",
    "env_token",
    "environment_url",
    "is_plain_token",
    "is_platform_url",
]


class TokenProblem(ValueError):
    """DYNATRACE_API_TOKEN is missing or malformed; the message never holds the token."""


def environment_url() -> str:
    return os.environ.get("DYNATRACE_ENV_URL", "").strip()


def is_platform_url(url: str) -> bool:
    """True for https://<tenant id>.apps.dynatrace.com, with or without one trailing slash."""
    return PLATFORM_HOST.fullmatch(url.removesuffix("/")) is not None


def env_token(create_hint: str) -> str:
    """DYNATRACE_API_TOKEN from the environment. `create_hint` says which token to create."""
    token = os.environ.get("DYNATRACE_API_TOKEN", "").strip()
    if not token:
        raise TokenProblem(f"DYNATRACE_API_TOKEN is not set. {create_hint} and put it in .env")
    if not is_plain_token(token):
        raise TokenProblem(
            "DYNATRACE_API_TOKEN must be plain ASCII text with no spaces; check that it was "
            "pasted without a smart quote, a dash or a line break"
        )
    return token
