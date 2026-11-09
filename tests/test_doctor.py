from collections.abc import Callable

from importer.core.doctor import passed, render, run_checks
from importer.sources.cloudwatch.extract import AwsError, Reader


def _reader(account: str | None) -> Callable[[str, str | None], Reader]:
    def runner(
        service: str, op: str, region: str, profile: str | None, params: dict[str, object]
    ) -> dict[str, object]:
        if account is None:
            raise AwsError("no AWS credentials found")
        return {"Account": account}

    def factory(region: str, profile: str | None) -> Reader:
        return Reader(region, profile, runner=runner)

    return factory


def _checks(account: str | None, env: dict[str, str], terraform: bool = True) -> list:  # type: ignore[type-arg]
    return run_checks(
        "us-east-1",
        None,
        which=lambda _: "/bin/terraform" if terraform else None,
        reader_factory=_reader(account),
        env=env,
    )


def test_all_good() -> None:
    checks = _checks("123456789012", {"NEW_RELIC_API_KEY": "k"})
    assert passed(checks)
    text = render(checks)
    assert "signed in to account 123456789012" in text
    assert "NEW_RELIC_API_KEY is set" in text


def test_missing_aws_keys_fail_with_the_reason() -> None:
    checks = _checks(None, {})
    assert not passed(checks)
    assert "[FAIL] aws keys: no AWS credentials found" in render(checks)


def test_new_relic_key_and_terraform_are_only_warnings() -> None:
    checks = _checks("123456789012", {}, terraform=False)
    assert passed(checks)
    text = render(checks)
    assert "[warn] new relic api key" in text
    assert "[warn] terraform" in text


def test_the_key_value_is_never_shown() -> None:
    assert "secret-value" not in render(
        _checks("123456789012", {"NEW_RELIC_API_KEY": "secret-value"})
    )


def test_google_login_is_checked_for_the_gcp_source_and_the_token_is_not_shown() -> None:
    from importer.sources.gcp.extract import GcpError

    ok = run_checks("us-east-1", None, source="gcp", token_factory=lambda: "ya29.secret")
    assert [c.name for c in ok][1] == "google login" and passed(ok)
    assert "ya29" not in render(ok)

    def no_login() -> str:
        raise GcpError("no Google credentials found")

    bad = run_checks("us-east-1", None, source="gcp", token_factory=no_login)
    assert not passed(bad) and "aws keys" not in render(bad)
