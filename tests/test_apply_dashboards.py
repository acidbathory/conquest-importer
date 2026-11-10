import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from importer.cli import app
from importer.core.apply import GuardError, NewRelicClient
from importer.core.apply_dashboards import (
    DashboardPlan,
    apply_dashboards,
    load_dashboard_plan,
    with_account,
)

FIXTURES = Path(__file__).parent / "fixtures" / "cloudwatch-dashboards"


class Fake:
    def __init__(self, existing: list[str] | None = None, errors: list[str] | None = None) -> None:
        self.existing = existing or []
        self.errors = errors
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        if "entitySearch" in body["query"]:
            q = body["variables"]["q"]
            found = [{"guid": "g-old", "name": n} for n in self.existing if f"'{n}'" in q]
            return httpx.Response(
                200, json={"data": {"actor": {"entitySearch": {"results": {"entities": found}}}}}
            )
        if self.errors:
            errs = [{"description": e, "type": "INVALID"} for e in self.errors]
            return httpx.Response(200, json={"data": {"dashboardCreate": {"errors": errs}}})
        name = body["variables"]["dashboard"]["name"]
        made = {"entityResult": {"guid": f"guid-{name}", "name": name}, "errors": None}
        return httpx.Response(200, json={"data": {"dashboardCreate": made}})

    def creates(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if "dashboardCreate" in c["query"]]


def client_for(fake: Fake) -> NewRelicClient:
    return NewRelicClient(SecretStr("k"), transport=httpx.MockTransport(fake), sleep=lambda _: None)


@pytest.fixture(scope="module")
def converted(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("dash")
    res = CliRunner().invoke(app, ["convert-dashboards", "--in", str(FIXTURES), "--out", str(out)])
    assert res.exit_code == 0, res.output
    return out


def test_plan_has_the_emittable_dashboards(converted: Path) -> None:
    plan = load_dashboard_plan(converted / "dashboard-ir")
    assert len(plan.items) == 2 and plan.skipped == 1
    assert all(i.widgets > 0 for i in plan.items)


def test_apply_substitutes_the_account_everywhere_and_records_guids(converted: Path) -> None:
    fake = Fake()
    plan = load_dashboard_plan(converted / "dashboard-ir")
    result = apply_dashboards(client_for(fake), 4242, plan)
    assert len(result.created) == 2 and not result.failed
    assert sorted(o.kind for o in result.objects) == ["dashboard", "dashboard"]
    sent = fake.creates()[0]["variables"]
    assert sent["accountId"] == 4242
    text = json.dumps(sent)
    assert "ACCOUNT_ID_PLACEHOLDER" not in text and "4242" in text


def test_existing_dashboard_is_skipped_not_updated(converted: Path) -> None:
    plan = load_dashboard_plan(converted / "dashboard-ir")
    fake = Fake(existing=[plan.items[0].name])
    result = apply_dashboards(client_for(fake), 1, plan)
    assert result.skipped_existing == [plan.items[0].name]
    assert len(fake.creates()) == 1


def test_api_reported_errors_are_failures_not_crashes(converted: Path) -> None:
    plan = load_dashboard_plan(converted / "dashboard-ir")
    result = apply_dashboards(client_for(Fake(errors=["widget 3 is invalid"])), 1, plan)
    assert not result.created and len(result.failed) == 2
    assert "widget 3 is invalid" in result.failed[0][1] and not result.objects


def test_unexpected_placeholders_are_refused(converted: Path) -> None:
    item = load_dashboard_plan(converted / "dashboard-ir").items[0]
    bad = json.loads(json.dumps(item.payload))
    bad["variables"]["accountId"] = 999
    with pytest.raises(GuardError, match="account"):
        with_account(bad, 1)
    bad = json.loads(json.dumps(item.payload))
    widget = next(
        w
        for w in bad["variables"]["dashboard"]["pages"][0]["widgets"]
        if "nrqlQueries" in w["rawConfiguration"]
    )
    widget["rawConfiguration"]["nrqlQueries"][0]["accountIds"] = [12345]
    with pytest.raises(GuardError, match="widget"):
        with_account(bad, 1)


def test_hostile_name_is_escaped_in_the_search_query(converted: Path) -> None:
    item = load_dashboard_plan(converted / "dashboard-ir").items[0]
    evil = "x' OR type = 'ENTITY"
    payload = json.loads(json.dumps(item.payload))
    payload["variables"]["dashboard"]["name"] = evil
    fake = Fake()
    apply_dashboards(
        client_for(fake),
        1,
        DashboardPlan(
            items=[item.model_copy(update={"name": evil, "payload": payload})], skipped=0
        ),
    )
    query = fake.calls[0]["variables"]["q"]
    assert "x\\' OR type = \\'ENTITY" in query  # the quotes are escaped, so it stays one string


def test_cli_dry_run_sends_nothing_and_needs_an_account(converted: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["apply-dashboards", "--in", str(converted)]).exit_code == 1
    res = runner.invoke(app, ["apply-dashboards", "--in", str(converted), "--account-id", "7"])
    assert res.exit_code == 0 and "DRY RUN" in res.output and "2 dashboards to create" in res.output


def test_cli_apply_needs_all_guards(converted: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEW_RELIC_ALLOWED_ACCOUNT_IDS", raising=False)
    res = CliRunner().invoke(
        app,
        [
            "apply-dashboards",
            "--in",
            str(converted),
            "--account-id",
            "7",
            "--apply",
            "--confirm-account",
            "7",
        ],
    )
    assert res.exit_code == 1 and "ALLOWED" in res.output


def _nrql_variable_payload() -> dict[str, Any]:
    from importer.core.dashboard_ir import IRDashboard, IRVariable, VariableType
    from importer.core.emit.dashboard import nerdgraph_dashboard
    from importer.core.ir import Confidence

    var = IRVariable(
        name="cluster",
        title="cluster",
        type=VariableType.NRQL,
        nrql="FROM Metric SELECT uniques(cluster)",
    )
    pinned = IRVariable(
        name="acct",
        title="acct",
        type=VariableType.NRQL,
        nrql="FROM Metric SELECT uniques(a)",
        account_ids=[777],
    )
    dash = IRDashboard(
        source_system="grafana",
        source_key="k",
        name="d",
        widgets=[],
        confidence=Confidence.EXACT,
        variables=[var, pinned],
    )
    return nerdgraph_dashboard(dash)


def test_nrql_variable_account_placeholder_is_replaced() -> None:
    """A placeholder string left in a variable's accountIds makes NerdGraph reject the dashboard."""
    out = with_account(_nrql_variable_payload(), 1234)
    variables = out["variables"]["dashboard"]["variables"]
    assert variables[0]["nrqlQuery"]["accountIds"] == [1234]
    assert variables[1]["nrqlQuery"]["accountIds"] == [777]
    assert "ACCOUNT_ID_PLACEHOLDER" not in json.dumps(out)


def test_unexpected_variable_account_is_refused() -> None:
    bad = _nrql_variable_payload()
    bad["variables"]["dashboard"]["variables"][0]["nrqlQuery"]["accountIds"] = ["other"]
    with pytest.raises(GuardError, match="variable"):
        with_account(bad, 1)
