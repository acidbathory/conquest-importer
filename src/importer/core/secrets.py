"""Secret loading and redaction.

Keys come only from environment variables and are wrapped in SecretStr so they
do not appear in repr(), logs or error messages. Any text that may contain a
secret must go through redact() before it is logged or written out.
"""

import logging
import os

from pydantic import SecretStr

REDACTED = "***REDACTED***"


class MissingSecretError(RuntimeError):
    """Raised when a required environment variable is not set."""


def require_env(name: str) -> SecretStr:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingSecretError(f"Environment variable {name} is not set")
    return SecretStr(value)


def redact(text: str, secrets: list[SecretStr]) -> str:
    """Replace every known secret value in text."""
    for secret in secrets:
        raw = secret.get_secret_value()
        if raw:
            text = text.replace(raw, REDACTED)
    return text


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs known secrets from every record."""

    def __init__(self, secrets: list[SecretStr]) -> None:
        super().__init__()
        self._secrets = secrets

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage(), self._secrets)
        record.args = None
        return True
