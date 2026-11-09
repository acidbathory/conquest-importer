"""The Dynatrace settings helpers live once in core/dynatrace_env.py; the three extractors keep
their public names and their exact messages."""

from collections.abc import Callable

import pytest

from importer.core import dynatrace_env
from importer.sources.dynatrace import extract as workflows
from importer.sources.dynatrace.classic import extract as classic
from importer.sources.dynatrace.inventory import extract as inventory

TOKEN = "dt0s16.ABCDEFGH.secretsecret"  # noqa: S105 - fake
EXTRACTORS = [
    (workflows.api_token, workflows.DynatraceError, "automation:workflows:read and slo:slos:read"),
    (classic.api_token, classic.ClassicError, "settings:objects:read and settings:schemas:read"),
    (inventory.inventory_token, workflows.DynatraceError, "document:documents:read"),
]


@pytest.mark.parametrize(("get", "error", "scope"), EXTRACTORS)
def test_a_missing_token_names_the_variable_and_the_scopes(
    monkeypatch: pytest.MonkeyPatch, get: Callable[[], str], error: type[Exception], scope: str
) -> None:
    monkeypatch.delenv("DYNATRACE_API_TOKEN", raising=False)
    with pytest.raises(error, match="DYNATRACE_API_TOKEN is not set") as caught:
        get()
    assert scope in str(caught.value) and str(caught.value).endswith("put it in .env")


@pytest.mark.parametrize(("get", "error", "scope"), EXTRACTORS)
@pytest.mark.parametrize("bad", ["dt0s16.“x”", "a b", "a\nb"])
def test_a_malformed_token_is_refused_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch,
    get: Callable[[], str],
    error: type[Exception],
    scope: str,
    bad: str,
) -> None:
    monkeypatch.setenv("DYNATRACE_API_TOKEN", bad)
    with pytest.raises(error, match="plain ASCII text") as caught:
        get()
    assert bad not in str(caught.value) and scope


@pytest.mark.parametrize(("get", "error", "scope"), EXTRACTORS)
def test_a_good_token_is_returned_stripped(
    monkeypatch: pytest.MonkeyPatch, get: Callable[[], str], error: type[Exception], scope: str
) -> None:
    monkeypatch.setenv("DYNATRACE_API_TOKEN", f"  {TOKEN}\n")
    assert get() == TOKEN and error and scope


def test_the_public_names_are_the_shared_ones() -> None:
    assert workflows.ENV_URL is dynatrace_env.PLATFORM_HOST
    assert inventory.ENV_URL is dynatrace_env.PLATFORM_HOST
    assert classic.PLATFORM_HOST is dynatrace_env.PLATFORM_HOST
    assert classic.CLASSIC_HOST is dynatrace_env.CLASSIC_HOST
    assert workflows.environment_url is dynatrace_env.environment_url
    assert classic.environment_url is dynatrace_env.environment_url
    assert inventory.environment_url is dynatrace_env.environment_url
    assert workflows.is_plain_token is dynatrace_env.is_plain_token


def test_the_environment_url_is_read_when_called(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DYNATRACE_ENV_URL", " https://abc12345.apps.dynatrace.com \n")
    assert dynatrace_env.environment_url() == "https://abc12345.apps.dynatrace.com"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://abc12345.apps.dynatrace.com", True),
        ("https://abc12345.apps.dynatrace.com/", True),
        ("https://abc12345.apps.dynatrace.com//", False),
        ("https://abc12345.live.dynatrace.com", False),
        ("http://abc12345.apps.dynatrace.com", False),
        ("https://abc.apps.dynatrace.com.evil.example.com", False),
        ("https://abc12345.apps.dynatrace.com/ui", False),
    ],
)
def test_platform_url_shapes(url: str, expected: bool) -> None:
    assert dynatrace_env.is_platform_url(url) is expected
    assert workflows.is_env_url(url) is expected
