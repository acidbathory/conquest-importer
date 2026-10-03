"""Secret loading and redaction.

Keys come only from environment variables and are wrapped in SecretStr so they
do not appear in repr(), logs or error messages. Any text that may contain a
secret must go through redact() before it is logged or written out.
"""

import logging
import os

from pydantic import BaseModel, SecretStr

REDACTED = "***REDACTED***"


class MissingSecretError(RuntimeError):
    """Raised when a required environment variable is not set."""


def require_env(name: str) -> SecretStr:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingSecretError(f"Environment variable {name} is not set")
    return SecretStr(value)


class DatadogCredentials(BaseModel):
    api_key: SecretStr
    app_key: SecretStr
    site: str = "datadoghq.com"

    @classmethod
    def from_env(cls) -> "DatadogCredentials":
        return cls(
            api_key=require_env("DD_API_KEY"),
            app_key=require_env("DD_APP_KEY"),
            site=os.environ.get("DD_SITE", "datadoghq.com"),
        )


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
