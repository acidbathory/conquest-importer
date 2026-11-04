from collections.abc import Callable

from importer.core.doctor import passed, render, run_checks
from importer.sources.cloudwatch.extract import AwsError, Reader


def _reader(account: str | None) -> Callable[[str, str | None], Reader]:
    def runner(args: list[str]) -> str:
        if account is None:
            raise AwsError("Unable to locate credentials")
        return f'{{"Account": "{account}"}}'

    def factory(region: str, profile: str | None) -> Reader:
        return Reader(region, profile, runner=runner)

    return factory


def test_all_good() -> None:
    checks = run_checks(
        "us-east-1", None, which=lambda _: "/bin/x", reader_factory=_reader("123456789012")
    )
    assert passed(checks)
    assert "signed in to account 123456789012" in render(checks)


def test_missing_aws_cli_fails_and_skips_sign_in() -> None:
    checks = run_checks("us-east-1", None, which=lambda _: None)
    assert not passed(checks)
    assert [c.name for c in checks] == ["aws cli", "terraform"]


def test_terraform_is_only_a_warning() -> None:
    checks = run_checks(
        "us-east-1",
        None,
        which=lambda n: "/bin/aws" if n == "aws" else None,
        reader_factory=_reader("123456789012"),
    )
    assert passed(checks)
    assert "[warn] terraform" in render(checks)


def test_sign_in_failure_is_reported() -> None:
    checks = run_checks("us-east-1", None, which=lambda _: "/bin/x", reader_factory=_reader(None))
    assert not passed(checks)
    assert "Unable to locate credentials" in render(checks)
