import logging

import pytest
from pydantic import SecretStr

from importer.core.secrets import (
    REDACTED,
    DatadogCredentials,
    MissingSecretError,
    RedactingFilter,
    redact,
    require_env,
)

KEY = "dd-super-secret-123"


def test_require_env_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DD_API_KEY", raising=False)
    with pytest.raises(MissingSecretError):
        require_env("DD_API_KEY")


def test_require_env_blank_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DD_API_KEY", "   ")
    with pytest.raises(MissingSecretError):
        require_env("DD_API_KEY")


def test_credentials_repr_hides_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DD_API_KEY", KEY)
    monkeypatch.setenv("DD_APP_KEY", "app-key-456")
    creds = DatadogCredentials.from_env()
    assert KEY not in repr(creds)
    assert KEY not in str(creds)
    assert "app-key-456" not in repr(creds)


def test_redact_replaces_secret() -> None:
    out = redact(f"calling with key {KEY} now", [SecretStr(KEY)])
    assert KEY not in out
    assert REDACTED in out


def test_logging_filter_scrubs_secret(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("redaction-test")
    logger.addFilter(RedactingFilter([SecretStr(KEY)]))
    with caplog.at_level(logging.INFO, logger="redaction-test"):
        logger.info("header DD-API-KEY=%s", KEY)
    assert KEY not in caplog.text
    assert REDACTED in caplog.text
