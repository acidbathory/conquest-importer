"""The data check says which converted conditions would be blind."""

from typing import Any

from importer.core.ir import Confidence
from importer.sources.gcp.data_check import Status, check_all, has_blind_conditions, render
from importer.sources.gcp.models import GcpAlertPolicy
from importer.sources.gcp.translate import translate_policy


class FakeClient:
    """Answers count queries from a table of metric name -> points."""

    def __init__(self, points: dict[str, int], logs: int = 0) -> None:
        self.points = points
        self.logs = logs
        self.queries: list[str] = []

    def run(self, query: str, variables: dict[str, Any], *, is_write: bool) -> dict[str, Any]:
        assert is_write is False
        nrql = variables["n"]
        self.queries.append(nrql)
        if "FROM Log" in nrql:
            row: dict[str, Any] = {"count": self.logs}
        else:
            hit = [n for n in self.points if n in nrql]
            row = {"count": self.points[hit[0]] if hit else 0}
            if "count(" not in nrql:
                row = {"max": 7.0} if hit and self.points[hit[0]] else {"max": None}
        return {"actor": {"account": {"nrql": {"results": [row]}}}}


def policy(kind: str, body: dict[str, Any]) -> GcpAlertPolicy:
    return GcpAlertPolicy.model_validate(
        {"displayName": kind, "conditions": [{"displayName": "c", kind: body}]}
    )


def metric(metric_type: str) -> dict[str, Any]:
    return {
        "filter": f'metric.type="{metric_type}"',
        "comparison": "COMPARISON_GT",
        "thresholdValue": 1,
        "duration": "60s",
        "aggregations": [{"alignmentPeriod": "60s", "perSeriesAligner": "ALIGN_MAX"}],
    }


def test_data_and_blind_conditions_are_told_apart() -> None:
    good = translate_policy(
        policy("conditionThreshold", metric("pubsub.googleapis.com/subscription/backlog_bytes"))
    )
    blind = translate_policy(
        policy("conditionThreshold", metric("compute.googleapis.com/instance/cpu/utilization"))
    )
    client = FakeClient({"gcp.pubsub.subscription.backlog_bytes": 12})
    checks = check_all(client, 1, [*good, *blind])  # type: ignore[arg-type]
    assert [c.status for c in checks] == [Status.DATA, Status.NO_DATA]
    assert has_blind_conditions(checks)
    assert checks[0].points == 12 and checks[0].value == 7.0
    assert "never fire" in checks[1].note


def test_a_log_condition_needs_log_events() -> None:
    (m,) = translate_policy(policy("conditionMatchedLog", {"filter": 'severity="ERROR"'}))
    quiet = check_all(FakeClient({}, logs=0), 1, [m])  # type: ignore[arg-type]
    busy = check_all(FakeClient({}, logs=4), 1, [m])  # type: ignore[arg-type]
    assert quiet[0].status is Status.NO_DATA and busy[0].status is Status.DATA


def test_a_manual_conversion_is_not_queried() -> None:
    (m,) = translate_policy(policy("conditionAbsent", {"filter": 'metric.type="x"'}))
    assert m.confidence is Confidence.MANUAL
    client = FakeClient({})
    (c,) = check_all(client, 1, [m])  # type: ignore[arg-type]
    assert c.status is Status.NOT_CHECKED and not client.queries
    assert not has_blind_conditions([c])


def test_the_report_escapes_names() -> None:
    (m,) = translate_policy(policy("conditionMatchedLog", {"filter": 'severity="ERROR"'}))
    m.name = "a|b`c`<x>"
    text = render(check_all(FakeClient({}), 1, [m]), 6)  # type: ignore[arg-type]
    assert "a|b" not in text and "<x>" not in text
