"""Tests that pin boundaries and allow-lists, written to kill mutants a review found alive."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from dynatrace_classic_helpers import APPS, CPU, GOOD, TOKEN, detector, event, one
from importer.cli import app
from importer.core.data_check import Status
from importer.core.ir import Confidence
from importer.sources.dynatrace.classic import detectors, metric_map, models
from importer.sources.dynatrace.classic import extract as cx
from importer.sources.dynatrace.classic import selector as sel
from importer.sources.dynatrace.classic.common import shown
from importer.sources.dynatrace.classic.data_check import check_all
from importer.sources.dynatrace.classic.extract import ClassicError, extract, http_getter
from importer.sources.dynatrace.classic.models import parse_object
from importer.sources.dynatrace.classic.translate import translate

FIXTURES = Path(__file__).parent / "fixtures" / "dynatrace-classic"


# ----- caps: both sides of every boundary ---------------------------------------------------


def serve(items_per_schema: int = 0, pages: int = 1) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        number = int(request.url.params.get("nextPageKey", "0"))
        body: dict[str, Any] = {
            "items": [
                {"objectId": f"{number}-{n}", "schemaId": "s", "scope": "e", "value": {}}
                for n in range(items_per_schema)
            ]
        }
        if number + 1 < pages:
            body["nextPageKey"] = str(number + 1)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def read(transport: httpx.MockTransport, tmp_path: Path) -> cx.ExtractResult:
    with http_getter(TOKEN, APPS, transport=transport) as get:
        return extract(get, APPS, tmp_path / "out")


def test_the_page_cap_allows_exactly_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cx, "MAX_PAGES", 3)
    monkeypatch.setattr(models, "READ_SCHEMAS", models.READ_SCHEMAS[:1])
    result = read(serve(1, pages=3), tmp_path)
    assert result.counts[models.READ_SCHEMAS[0]] == 3


def test_the_total_page_cap_allows_exactly_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cx, "MAX_TOTAL_PAGES", len(models.READ_SCHEMAS))
    assert len(read(serve(), tmp_path).counts) == len(models.READ_SCHEMAS)


def test_the_object_cap_allows_exactly_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cx, "MAX_OBJECTS", 2)
    assert read(serve(2), tmp_path).counts[models.METRIC_EVENTS] == 2
    with pytest.raises(ClassicError, match="more than 2 objects"):
        read(serve(3), tmp_path / "again")


def test_the_page_key_cap_allows_exactly_the_cap(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "nextPageKey" in request.url.params:
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json={"items": [], "nextPageKey": "k" * cx.MAX_PAGE_KEY})

    assert read(httpx.MockTransport(handler), tmp_path).counts


def test_only_allow_listed_schema_ids_are_requested(tmp_path: Path) -> None:
    seen: set[str] = set()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.add(request.url.params["schemaIds"])
        return httpx.Response(200, json={"items": []})

    read(httpx.MockTransport(handler), tmp_path)
    assert seen == set(models.READ_SCHEMAS)
    assert {models.METRIC_EVENTS, models.DETECTORS, models.PROFILE, models.NOTIFICATIONS} <= seen
    assert all(s.startswith("builtin:") for s in seen)
    assert len(models.READ_SCHEMAS) == len(set(models.READ_SCHEMAS))


def test_the_selector_and_query_length_caps_allow_exactly_the_cap() -> None:
    from importer.sources.dynatrace.classic.selector_query import to_query

    padded = CPU + ":avg" + " " * (sel.MAX_LENGTH - len(CPU) - 4)
    assert len(padded) == sel.MAX_LENGTH and to_query(padded).terms
    with pytest.raises(sel.SelectorError, match="longer than"):
        to_query(padded + " ")
    query = "timeseries avg(dt.host.cpu.usage)" + " " * (detectors.MAX_QUERY - 33)
    inputs = [i for i in GOOD if i["key"] != "query"] + [{"key": "query", "value": query}]
    assert one(detector(inputs)).confidence is Confidence.APPROXIMATE
    over = [*inputs[:-1], {"key": "query", "value": query + " "}]
    assert "longer than" in one(detector(over)).reasons[0]


def test_shown_caps_its_length_and_keeps_plain_names() -> None:
    assert shown("a" * 80) == repr("a" * 80)
    assert shown("a" * 81) == "<a name with unusual characters>"
    assert shown("builtin:host.cpu.usage") == "'builtin:host.cpu.usage'"
    for bad in ("a b", "a'b", 'a"b', "a|b", "a`b", "${x}", "a\nb", "<x>"):
        assert shown(bad) == "<a name with unusual characters>"


# ----- secrets ------------------------------------------------------------------------------

SECRETS = {
    "password": "PLANTED-PASSWORD",
    "token": "PLANTED-TOKEN",
    "url": "https://hooks.example.invalid/PLANTED-URL",
    "serviceApiKey": "PLANTED-SERVICE-KEY",
    "clientSecret": "PLANTED-CLIENT-SECRET",
    "apiToken": "PLANTED-API-TOKEN",
    "secretUrl": "PLANTED-SECRET-URL",
}


def test_top_level_string_secrets_in_a_notification_survive_nowhere(tmp_path: Path) -> None:
    value = {
        "enabled": True,
        "type": "WEBHOOK",
        "displayName": "Bridge",
        "alertingProfile": "p1",
        **SECRETS,
    }
    item = {
        "objectId": "n1",
        "schemaId": models.NOTIFICATIONS,
        "scope": "environment",
        "value": value,
    }
    path = tmp_path / "n.json"
    path.write_text(json.dumps({"items": [item]}))
    assert parse_object(item).value == {
        "enabled": True,
        "type": "WEBHOOK",
        "displayName": "Bridge",
        "alertingProfile": "p1",
    }
    out = tmp_path / "out"
    result = CliRunner().invoke(
        app, ["convert", "--source", "dynatrace-classic", "--in", str(path), "--out", str(out)]
    )
    assert result.exit_code == 0
    blob = result.output + "".join(f.read_text() for f in out.rglob("*") if f.is_file())
    for planted in SECRETS.values():
        assert planted not in blob


def test_the_export_folder_drops_the_same_secrets(tmp_path: Path) -> None:
    value = {
        "enabled": True,
        "type": "WEBHOOK",
        "displayName": "B",
        "alertingProfile": "p",
        **SECRETS,
    }
    item = {"objectId": "n", "schemaId": models.NOTIFICATIONS, "scope": "e", "value": value}

    def handler(request: httpx.Request) -> httpx.Response:
        found = [item] if request.url.params["schemaIds"] == models.NOTIFICATIONS else []
        return httpx.Response(200, json={"items": found})

    read(httpx.MockTransport(handler), tmp_path)
    text = "".join(f.read_text() for f in (tmp_path / "out").rglob("*.json"))
    assert "PLANTED" not in text and '"displayName": "B"' in text


# ----- the metric map -----------------------------------------------------------------------


ROW: dict[str, Any] = {
    "keys": ["k"],
    "event": "E",
    "attribute": "a",
    "status": "unverified",
}


def write_map(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"metrics": rows}))
    return path


@pytest.mark.parametrize(
    "select", ["count(*)", "filter(count(*), WHERE error IS true)", "a" * 200, "sum(a)=1,b.c"]
)
def test_select_expressions_inside_the_allowed_set_load(tmp_path: Path, select: str) -> None:
    row = {**ROW, "attribute": None, "select": select}
    assert metric_map.load_metric_map(write_map(tmp_path, [row])).metrics[0].select == select


@pytest.mark.parametrize("select", ["a" * 201, "count(*);", "x'y", "a`b", "a\nb", "", "a|b", "$x"])
def test_select_expressions_outside_it_are_refused(tmp_path: Path, select: str) -> None:
    row = {**ROW, "attribute": None, "select": select}
    with pytest.raises(metric_map.MapError):
        metric_map.load_metric_map(write_map(tmp_path, [row]))


def test_a_row_with_status_none_is_manual_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = {**ROW, "keys": ["my.none"], "status": "none", "evidence": "nothing like it exists"}
    monkeypatch.setenv(metric_map.MAP_ENV, str(write_map(tmp_path, [row])))
    m = one(event({"metricKey": "my.none"}))
    assert m.confidence is Confidence.MANUAL
    assert "nothing like it exists" in m.reasons[0] and "no New Relic signal" in m.reasons[0]


def test_a_documented_row_has_its_own_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    row = {**ROW, "keys": ["my.doc"], "status": "documented", "default_aggregation": "average"}
    monkeypatch.setenv(metric_map.MAP_ENV, str(write_map(tmp_path, [row])))
    text = " ".join(one(event({"metricKey": "my.doc"})).reasons)
    assert "comes from documentation" in text and "unverified" not in text


def test_a_verified_row_has_no_name_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    row = {**ROW, "keys": ["my.ok"], "status": "verified", "default_aggregation": "average"}
    monkeypatch.setenv(metric_map.MAP_ENV, str(write_map(tmp_path, [row])))
    text = " ".join(one(event({"metricKey": "my.ok"})).reasons)
    assert "metric name mapping" not in text and "data prerequisite" in text


# ----- windows --------------------------------------------------------------------------------


def test_loss_of_signal_waits_the_violating_samples_not_the_window() -> None:
    m = one(event(model={"alertOnNoData": True, "violatingSamples": 2, "samples": 5}))
    assert m.no_data.timeout_minutes == 2 and m.signal.window_seconds == 120


def test_a_case_insensitive_dimension_test_is_manual_but_an_entity_name_one_converts() -> None:
    dim = {
        "dimensionFilter": [
            {
                "dimensionKey": "dt.entity.host",
                "operator": "CONTAINS_CASE_INSENSITIVE",
                "dimensionValue": "x",
            }
        ]
    }
    assert one(event(dim)).confidence is Confidence.MANUAL
    name = {
        "entityFilter": {
            "dimensionKey": "dt.entity.host",
            "conditions": [
                {"type": "NAME", "operator": "CONTAINS_CASE_INSENSITIVE", "value": "web"}
            ],
        }
    }
    m = one(event(name))
    assert m.confidence is Confidence.APPROXIMATE and "case-insensitive" in " ".join(m.reasons)


def test_inventory_names_lose_controls_and_are_capped() -> None:
    hostile = "line\nbreak\x1b[31m" + "x" * 500
    obj = parse_object({"objectId": "p", "schemaId": models.PROFILE, "value": {"name": hostile}})
    (m,) = translate([obj])
    assert "\n" not in m.name and "\x1b" not in m.name and len(m.name) <= 120


# ----- the data check looks at the condition's own filters ----------------------------------


class ScopedClient:
    """Has points only for a count that carries the condition's WHERE clause."""

    def __init__(self, needs: str) -> None:
        self.needs = needs
        self.queries: list[str] = []

    def run(self, query: str, variables: dict[str, Any], *, is_write: bool) -> dict[str, Any]:
        assert is_write is False
        nrql = variables["n"]
        self.queries.append(nrql)
        has = self.needs in nrql
        row: dict[str, Any] = {"count": 4 if has else 0} if "count(" in nrql else {"v": 1.0}
        return {"actor": {"account": {"nrql": {"results": [row]}}}}


FILTERED = {
    "dimensionFilter": [
        {"dimensionKey": "dt.entity.host", "operator": "EQUALS", "dimensionValue": "web-1"}
    ]
}


def test_a_filtered_condition_is_blind_when_only_other_rows_exist() -> None:
    (m,) = translate([event(FILTERED)])
    blind = ScopedClient("hostname = 'nothing'")
    (c,) = check_all(blind, 1, [m])  # type: ignore[arg-type]
    assert c.status is Status.NO_DATA
    assert any("hostname = 'web-1'" in q for q in blind.queries)
    seen = ScopedClient("hostname = 'web-1'")
    (c,) = check_all(seen, 1, [m])  # type: ignore[arg-type]
    assert c.status is Status.DATA and c.points == 4


def test_an_unfiltered_condition_counts_without_where() -> None:
    (m,) = translate([event()])
    client = ScopedClient("SystemSample")
    check_all(client, 1, [m])  # type: ignore[arg-type]
    assert "SELECT count(cpuPercent) FROM SystemSample SINCE 24 hours ago" in client.queries
