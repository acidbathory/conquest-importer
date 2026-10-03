import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from importer.cli import app
from importer.core.apply import (
    GuardError,
    NewRelicAPIError,
    NewRelicClient,
    apply_plan,
    check_guards,
    load_plan,
    printable,
)

KEY = "NRAK-SECRET-KEY-123"
FIXTURES = Path(__file__).parent / "fixtures" / "datadog"


class FakeNewRelic:
    """Answers NerdGraph requests from memory and records every call."""

    def __init__(self, policies: list[str] | None = None, conditions: list[str] | None = None):
        self.policies = {name: str(100 + i) for i, name in enumerate(policies or [])}
        self.conditions = list(conditions or [])
        self.calls: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self.fail_create_with: int | None = None
        self.graphql_error: str | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        self.headers.append(request.headers)
        q, v = body["query"], body["variables"]
        if self.graphql_error:
            return httpx.Response(200, json={"errors": [{"message": self.graphql_error}]})
        if "alertsNrqlConditionStaticCreate" in q:
            if self.fail_create_with:
                return httpx.Response(self.fail_create_with, text="boom")
            self.conditions.append(v["condition"]["name"])
            return httpx.Response(200, json={"data": {"alertsNrqlConditionStaticCreate": {}}})
        if "alertsPolicyCreate" in q:
            self.policies[v["policy"]["name"]] = "555"
            return httpx.Response(
                200, json={"data": {"alertsPolicyCreate": {"id": "555", "name": "x"}}}
            )
        if "policiesSearch" in q:
            found = [{"id": i, "name": n} for n, i in self.policies.items()]
            data = {"policiesSearch": {"policies": found, "nextCursor": None}}
            return httpx.Response(200, json={"data": {"actor": {"account": {"alerts": data}}}})
        if "nrqlConditionsSearch" in q:
            found = [{"id": "1", "name": n} for n in self.conditions]
            data = {"nrqlConditionsSearch": {"nrqlConditions": found, "nextCursor": None}}
            return httpx.Response(200, json={"data": {"actor": {"account": {"alerts": data}}}})
        return httpx.Response(200, json={"data": {"actor": {"account": {"id": 1, "name": "Test"}}}})

    def creates(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if "alertsNrqlConditionStaticCreate" in c["query"]]


def client_for(fake: FakeNewRelic, sleeps: list[float] | None = None) -> NewRelicClient:
    rec = sleeps if sleeps is not None else []
    return NewRelicClient(SecretStr(KEY), transport=httpx.MockTransport(fake), sleep=rec.append)


@pytest.fixture(scope="module")
def converted(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("converted")
    result = CliRunner().invoke(app, ["convert", "--in", str(FIXTURES), "--out", str(out)])
    assert result.exit_code == 0, result.output
    return out


# --- plan ----------------------------------------------------------------------------


def test_plan_contains_only_emittable_monitors(converted: Path) -> None:
    plan = load_plan(converted / "ir")
    assert len(plan.items) == 21
    assert plan.skipped == 9
    assert all(i.nrql.startswith("SELECT") for i in plan.items)


# --- guards --------------------------------------------------------------------------


def test_guard_requires_allow_list() -> None:
    with pytest.raises(GuardError, match="ALLOWED"):
        check_guards(123, 123, None)


def test_guard_rejects_account_not_in_allow_list() -> None:
    with pytest.raises(GuardError, match="not in"):
        check_guards(123, 123, "456,789")


def test_guard_requires_confirmation() -> None:
    with pytest.raises(GuardError, match="confirm"):
        check_guards(123, None, "123")
    with pytest.raises(GuardError, match="confirm"):
        check_guards(123, 124, "123")


def test_guard_passes_when_all_conditions_met() -> None:
    check_guards(123, 123, " 456 , 123 ")


def test_unknown_region_rejected() -> None:
    with pytest.raises(GuardError):
        NewRelicClient(SecretStr(KEY), "evil.example.com")


# --- CLI ----------------------------------------------------------------------------


def test_cli_dry_run_sends_nothing_and_needs_no_key(
    converted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NEW_RELIC_API_KEY", raising=False)

    def boom(*a: object, **k: object) -> None:
        raise AssertionError("network client must not be created in a dry run")

    monkeypatch.setattr("importer.cli.NewRelicClient", boom)
    result = CliRunner().invoke(app, ["apply", "--in", str(converted), "--account-id", "123"])
    assert result.exit_code == 0, result.output
    assert "DRY RUN" in result.output
    assert "21 conditions" in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["--apply"],  # no confirmation
        ["--apply", "--confirm-account", "123"],  # allow-list missing
    ],
)
def test_cli_apply_is_refused_without_guards(
    converted: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    monkeypatch.delenv("NEW_RELIC_ALLOWED_ACCOUNT_IDS", raising=False)
    monkeypatch.setenv("NEW_RELIC_API_KEY", KEY)

    def boom(*a: object, **k: object) -> None:
        raise AssertionError("network client must not be created when a guard fails")

    monkeypatch.setattr("importer.cli.NewRelicClient", boom)
    result = CliRunner().invoke(
        app, ["apply", "--in", str(converted), "--account-id", "123", *args]
    )
    assert result.exit_code == 1
    assert KEY not in result.output


def test_cli_requires_account_id(converted: Path) -> None:
    result = CliRunner().invoke(app, ["apply", "--in", str(converted)])
    assert result.exit_code == 1


# --- applying -------------------------------------------------------------------------


def test_apply_creates_policy_and_disabled_conditions(converted: Path) -> None:
    plan = load_plan(converted / "ir")
    fake = FakeNewRelic()
    with client_for(fake) as client:
        result = apply_plan(client, 123, "Imported from Datadog", plan)
    assert len(result.created) == 21 and not result.failed
    assert fake.policies == {"Imported from Datadog": "555"}
    first = fake.creates()[0]["variables"]
    assert first["accountId"] == 123
    assert first["policyId"] == "555"
    assert first["condition"]["enabled"] is False
    assert "PLACEHOLDER" not in json.dumps(fake.creates())
    assert all(h["API-Key"] == KEY for h in fake.headers)


def test_apply_reuses_policy_and_skips_existing_conditions(converted: Path) -> None:
    plan = load_plan(converted / "ir")
    already = plan.items[0].name
    fake = FakeNewRelic(policies=["Imported from Datadog"], conditions=[already])
    with client_for(fake) as client:
        result = apply_plan(client, 123, "Imported from Datadog", plan)
    assert result.skipped_existing == [already]
    assert len(result.created) == 20
    assert not any("alertsPolicyCreate" in c["query"] for c in fake.calls)


def test_rerun_creates_nothing_new(converted: Path) -> None:
    plan = load_plan(converted / "ir")
    fake = FakeNewRelic()
    with client_for(fake) as client:
        apply_plan(client, 123, "P", plan)
        second = apply_plan(client, 123, "P", plan)
    assert not second.created
    assert len(second.skipped_existing) == len({i.name for i in plan.items})
    assert len(fake.creates()) == 21


def test_create_is_never_retried(converted: Path) -> None:
    plan = load_plan(converted / "ir")
    fake = FakeNewRelic()
    fake.fail_create_with = 503
    sleeps: list[float] = []
    with client_for(fake, sleeps) as client:
        result = apply_plan(client, 123, "P", plan)
    assert len(result.failed) == 21
    assert len(fake.creates()) == 21  # exactly one attempt each
    assert sleeps == []


def test_reads_are_retried() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": {"actor": {"account": {"id": 1, "name": "T"}}}})

    sleeps: list[float] = []
    client = NewRelicClient(
        SecretStr(KEY), transport=httpx.MockTransport(handler), sleep=sleeps.append
    )
    from importer.core.apply import get_account_name

    assert get_account_name(client, 1) == "T"
    assert sleeps == [1.0, 2.0]


def test_graphql_errors_are_raised_and_redacted() -> None:
    fake = FakeNewRelic()
    fake.graphql_error = f"bad credentials {KEY}"
    from importer.core.apply import get_account_name

    with pytest.raises(NewRelicAPIError) as info, client_for(fake) as client:
        get_account_name(client, 1)
    assert KEY not in str(info.value)


def test_http_error_body_is_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=f"forbidden {KEY}")

    client = NewRelicClient(SecretStr(KEY), transport=httpx.MockTransport(handler))
    from importer.core.apply import get_account_name

    with pytest.raises(NewRelicAPIError) as info:
        get_account_name(client, 1)
    assert KEY not in str(info.value)


def test_hostile_payload_values_are_refused(converted: Path) -> None:
    plan = load_plan(converted / "ir")
    plan.items[0].payload["variables"]["accountId"] = 999  # tampered
    with client_for(FakeNewRelic()) as client, pytest.raises(GuardError):
        apply_plan(client, 123, "P", plan)


# --- terminal safety -----------------------------------------------------------------


def test_printable_strips_escape_sequences() -> None:
    out = printable("name\x1b[31mRED\x1b]0;pwn\x07\nline")
    assert "\x1b" not in out and "\x07" not in out and "\n" not in out
