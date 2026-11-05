import logging

import pytest
from pydantic import SecretStr

from importer.core.secrets import (
    REDACTED,
    MissingSecretError,
    RedactingFilter,
    redact,
    require_env,
)

KEY = "dd-super-secret-123"


def test_require_env_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_SECRET_KEY", raising=False)
    with pytest.raises(MissingSecretError):
        require_env("TEST_SECRET_KEY")


def test_require_env_blank_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_SECRET_KEY", "   ")
    with pytest.raises(MissingSecretError):
        require_env("TEST_SECRET_KEY")


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
