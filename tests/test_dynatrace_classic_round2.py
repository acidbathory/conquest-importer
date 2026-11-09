"""Round 2: defects an independent review proved, each with a test that failed first."""

import itertools
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from dynatrace_classic_helpers import (
    APPS,
    CPU,
    GOOD,
    LIVE,
    RT,
    TOKEN,
    detector,
    event,
    one,
    sel,
)
from importer.cli import app
from importer.commands import dynatrace_classic as command
from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence
from importer.sources.dynatrace.classic import extract as cx
from importer.sources.dynatrace.classic import metric_map, models, selector_query
from importer.sources.dynatrace.classic.extract import ClassicError, extract, http_getter
from importer.sources.dynatrace.classic.models import SettingsObject, parse_object
from importer.sources.dynatrace.classic.selector import SelectorError
from importer.sources.dynatrace.classic.selector_query import to_query
from importer.sources.dynatrace.classic.translate import translate

# ----- 1. or/and filters keep their meaning ----------------------------------------------------

Row = dict[str, str]
ROWS: list[Row] = [{"a": x, "b": y} for x in "123" for y in "123"]


def leaf(key: str, value: str) -> tuple[Any, ...]:
    return ("eq", key, value)


def trees(depth: int) -> list[tuple[Any, ...]]:
    leaves = [leaf(k, v) for k in "ab" for v in "12"]
    if depth == 0:
        return leaves
    smaller = trees(depth - 1)[:14]
    out = list(leaves)
    out += [("not", t) for t in smaller]
    out += [(op, s, t) for op in ("and", "or") for s, t in itertools.product(smaller, smaller)]
    return out


def source_text(tree: tuple[Any, ...]) -> str:
    if tree[0] == "eq":
        return f'eq("{tree[1]}","{tree[2]}")'
    return str(tree[0]) + "(" + ",".join(source_text(t) for t in tree[1:]) + ")"


def meaning(tree: tuple[Any, ...], row: Row) -> bool:
    if tree[0] == "eq":
        return bool(row[tree[1]] == tree[2])
    if tree[0] == "not":
        return not meaning(tree[1], row)
    results = [meaning(t, row) for t in tree[1:]]
    return all(results) if tree[0] == "and" else any(results)


def converted(filters: list[selector_query.DimFilter], row: Row) -> bool:
    def holds(f: selector_query.DimFilter) -> bool:
        found = row[f.key] in f.values
        return not found if f.negate else found

    return all(holds(f) for f in filters)


def test_converted_filters_mean_what_the_selector_says_for_many_small_trees() -> None:
    tried = converted_count = 0
    for tree in trees(2):
        tried += 1
        try:
            query = to_query(f"{CPU}:filter({source_text(tree)}):avg")
        except SelectorError:
            continue
        converted_count += 1
        for row in ROWS:
            assert converted(query.terms[0].filters, row) == meaning(tree, row), source_text(tree)
    assert tried > 300 and converted_count > 20


def test_the_reviewers_or_of_and_is_manual() -> None:
    text = f'{CPU}:filter(or(and(eq("h","a"),eq("h","b")),eq("h","c"))):avg'
    with pytest.raises(SelectorError, match="or filter"):
        to_query(text)


@pytest.mark.parametrize("empty", ["and()", "or()", "not()"])
def test_an_empty_filter_call_is_manual_not_no_filter(empty: str) -> None:
    with pytest.raises(SelectorError):
        to_query(f"{CPU}:filter({empty}):avg")


# ----- 2. unit factors and arithmetic ----------------------------------------------------------


@pytest.mark.parametrize("selector", [f"({RT}:avg) * 2", f"2 * ({RT}:avg)", f"({RT}:avg) / 2"])
def test_multiplying_or_dividing_by_a_constant_scales_the_threshold_by_the_factor(
    selector: str,
) -> None:
    m = one(sel(selector, threshold=1_000_000))
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert m.thresholds[0].value == pytest.approx(1.0)  # 1e6 us * 1e-6, whatever the constant


def test_a_constant_divided_by_the_metric_inverts_the_factor() -> None:
    m = one(sel(f"1000 / ({RT}:avg)", threshold=5))
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert m.thresholds[0].value == pytest.approx(5 / 1e-6)


@pytest.mark.parametrize("text", [f"({RT}:avg) + 1000", f"({RT}:avg) - 1000", f"1000 - ({RT}:avg)"])
def test_adding_to_a_scaled_metric_is_manual(text: str) -> None:
    m = one(sel(text, threshold=5))
    assert m.confidence is Confidence.MANUAL and "unit" in " ".join(m.reasons)


def test_adding_to_an_unscaled_metric_still_converts() -> None:
    m = one(sel(f"({CPU}:avg) + 10", threshold=5))
    assert m.confidence is Confidence.APPROXIMATE and m.thresholds[0].value == 5.0


def test_a_detector_threshold_is_scaled_too() -> None:
    inputs = [
        {"key": "query", "value": "timeseries avg(dt.service.request.response_time)"},
        {"key": "threshold", "value": "2000000"},
        {"key": "alertCondition", "value": "ABOVE"},
        {"key": "violatingSamples", "value": "3"},
        {"key": "slidingWindow", "value": "3"},
    ]
    m = one(detector(inputs))
    assert m.confidence is Confidence.APPROXIMATE
    assert m.thresholds[0].value == pytest.approx(2.0)


# ----- 3. queryOffset ----------------------------------------------------------------------------


@pytest.mark.parametrize(("offset", "delay"), [(1, 120), (2, 120), (5, 300), (20, 1200)])
def test_offsets_up_to_20_minutes_become_a_delay_within_new_relics_limit(
    offset: int, delay: int
) -> None:
    m = one(event({"queryOffset": offset}))
    assert m.signal.aggregation_delay_seconds == delay


@pytest.mark.parametrize("offset", [21, 60, 61])
def test_offsets_over_20_minutes_are_manual_and_say_the_limit(offset: int) -> None:
    m = one(event({"queryOffset": offset}))
    assert m.confidence is Confidence.MANUAL and "20" in m.reasons[0]


# ----- 4. entity ids ----------------------------------------------------------------------------


ENTITY_ID = "SERVICE-ABCDEF0123456789"


def test_an_entity_id_filter_is_manual() -> None:
    m = one(sel(f'{RT}:filter(eq("dt.entity.service","{ENTITY_ID}")):avg'))
    assert m.confidence is Confidence.MANUAL and "entity id" in " ".join(m.reasons)


def test_an_entity_id_in_a_dimension_filter_is_manual() -> None:
    q = {
        "metricKey": RT,
        "dimensionFilter": [
            {"dimensionKey": "dt.entity.service", "operator": "EQUALS", "dimensionValue": ENTITY_ID}
        ],
    }
    assert one(event(q)).confidence is Confidence.MANUAL


def test_an_entity_name_filter_still_converts() -> None:
    m = one(sel(f'{RT}:filter(eq("dt.entity.service","checkout")):avg'))
    assert m.confidence is Confidence.APPROXIMATE


def test_splitting_by_an_entity_dimension_says_the_labels_change() -> None:
    m = one(sel(f'{CPU}:splitBy("dt.entity.host"):avg'))
    assert "entity ids" in " ".join(m.reasons) and "names" in " ".join(m.reasons)


# ----- 5. numbers that cannot be floats ----------------------------------------------------------


def detector_with(**inputs: Any) -> SettingsObject:
    kept = [i for i in GOOD if i["key"] not in inputs]
    return detector([*kept, *({"key": k, "value": v} for k, v in inputs.items())])


@pytest.mark.parametrize(
    "key", ["threshold", "violatingSamples", "slidingWindow", "dealertingSamples"]
)
@pytest.mark.parametrize("value", [10**400, -(10**400), 10**16, "9" * 400])
def test_a_huge_detector_number_is_manual_not_a_crash(key: str, value: Any) -> None:
    m = one(detector_with(**{key: value}))
    assert m.confidence is Confidence.MANUAL


def test_a_huge_number_in_a_metric_event_does_not_crash(tmp_path: Path) -> None:
    big = "9" * 400
    raw = json.dumps(
        {
            "objectId": "o",
            "schemaId": models.METRIC_EVENTS,
            "value": {
                "summary": "s",
                "queryDefinition": {"type": "METRIC_KEY", "metricKey": CPU, "aggregation": "AVG"},
                "modelProperties": {
                    "type": "STATIC_THRESHOLD",
                    "threshold": "@",
                    "alertCondition": "ABOVE",
                    "violatingSamples": "@",
                    "samples": "@",
                },
            },
        }
    ).replace('"@"', big)
    path = tmp_path / "e.json"
    path.write_text("[" + raw + "]")
    out = tmp_path / "out"
    result = CliRunner().invoke(
        app, ["convert", "--source", "dynatrace-classic", "--in", str(path), "--out", str(out)]
    )
    assert result.exit_code == 0 and "Traceback" not in result.output


# ----- 6. keys stay unique -----------------------------------------------------------------------


def test_generated_suffixes_never_collide_with_real_ids() -> None:
    ids = ["A", "A", "A#2", "A", "A#2"]
    keys = [m.source_key for m in translate([event(object_id=i) for i in ids])]
    assert len(set(keys)) == 5 and keys[0] == "A" and keys[1] == "A#2"
    assert len({m.source_id for m in translate([event(object_id=i) for i in ids])}) == 5


# ----- 7. host handling --------------------------------------------------------------------------

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.mark.parametrize(
    "url",
    [
        LIVE + "//",
        APPS + "//",
        "https://evil.live.dynatrace.com.attacker.net",
        "https://live.dynatrace.com.evil.com",
        "https://abc.live.dynatrace.com.evil.com",
        "https://abc.evil.apps.dynatrace.com",
        "https://a.b.live.dynatrace.com",
        "https://a.b.apps.dynatrace.com",
        "https://abc.live.dynatrace.com@evil.com",
        "https://user:pw@abc.live.dynatrace.com",
        "https://ABC.live.dynatrace.com",
        "https://abc.LIVE.dynatrace.com",
        "https://abc.live.dynatrace.com.",
        "https://abc.live.dynatrace.com:443",
        "https://abc.live.dynatrace.com:8443",
        "https://\u0430bc.live.dynatrace.com",  # Cyrillic a
        "https://abc.live.dynatrace.com​",
        "https://abc​.live.dynatrace.com",
        "https://abc.live.dynatrace.com\n",
        "https://abc.live.dynatrace.com\nevil",
        "https://abc.live.dynatrace.com/path",
        "https://abc.live.dynatrace.com?x=1",
        "https://abc.live.dynatrace.com#x",
        "http://abc.live.dynatrace.com",
        "https://abc-def.live.dynatrace.com",
        "https://.live.dynatrace.com",
        "https://abc.live.dynatrace.com/ ",
        " https://abc.live.dynatrace.com",
        "https://abc.live-dynatrace.com",
        "https://abclive.dynatrace.com",
        "https://abc.appsXdynatrace.com",
        "https://abc.liveXdynatrace.com",
    ],
)
def test_host_patterns_refuse_every_bypass(url: str) -> None:
    assert not cx.is_env_url(url)
    with pytest.raises(ValueError, match="DYNATRACE_ENV_URL"), http_getter(TOKEN, url):
        pass


def requests_for(url: str, tmp_path: Path) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": []})

    with http_getter(TOKEN, url, transport=httpx.MockTransport(handler)) as get:
        extract(get, url, tmp_path / "out")
    return seen


def test_a_platform_token_goes_only_to_an_apps_host_and_its_paths(tmp_path: Path) -> None:
    seen = requests_for(APPS + "/", tmp_path)
    assert {r.headers["authorization"] for r in seen} == {f"Bearer {TOKEN}"}
    assert all(r.url.path.startswith("/platform/") for r in seen)
    assert {r.url.host for r in seen} == {"abc12345.apps.dynatrace.com"}


def test_an_api_token_goes_only_to_a_live_host_and_its_path(tmp_path: Path) -> None:
    seen = requests_for(LIVE, tmp_path)
    assert {r.headers["authorization"] for r in seen} == {f"Api-Token {TOKEN}"}
    assert {r.url.path for r in seen} == {"/api/v2/settings/objects"}
    assert {r.url.host for r in seen} == {"abc12345.live.dynatrace.com"}


# ----- 8. status is an attribute, not text ------------------------------------------------------


def run(handler: Handler, tmp_path: Path) -> cx.ExtractResult:
    with http_getter(TOKEN, APPS, transport=httpx.MockTransport(handler)) as get:
        return extract(get, APPS, tmp_path / "out")


@pytest.mark.parametrize("status", [403, 500, 401])
def test_an_error_whose_text_says_404_is_still_an_error(status: int, tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "the server returned 404 here"}})

    with pytest.raises(ClassicError) as err:
        run(handler, tmp_path)
    assert err.value.status == status


def test_a_404_has_status_404() -> None:
    err = ClassicError("x", status=404)
    assert err.status == 404 and ClassicError("y").status is None


def test_a_404_on_a_later_page_is_an_error_not_a_missing_schema(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "nextPageKey" in request.url.params:
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"items": [], "nextPageKey": "k"})

    with pytest.raises(ClassicError, match="later page"):
        run(handler, tmp_path)


def test_when_no_schema_can_be_read_the_run_fails(tmp_path: Path) -> None:
    with pytest.raises(ClassicError, match="no schema could be read"):
        run(lambda _r: httpx.Response(404, json={}), tmp_path)


def test_the_command_exits_1_when_nothing_could_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        command,
        "http_getter",
        lambda token, url: http_getter(
            token, url, transport=httpx.MockTransport(lambda _r: httpx.Response(404, json={}))
        ),
    )
    monkeypatch.setenv("DYNATRACE_API_TOKEN", TOKEN)
    monkeypatch.setenv("DYNATRACE_ENV_URL", APPS)
    result = CliRunner().invoke(app, ["extract-dynatrace-classic", "--out", str(tmp_path / "o")])
    assert result.exit_code == 1 and "no schema could be read" in result.output


# ----- 9. hostile transports --------------------------------------------------------------------


def test_a_huge_page_key_is_an_error(tmp_path: Path) -> None:
    body = {"items": [], "nextPageKey": "k" * 100_000}
    with pytest.raises(ClassicError, match="page key"):
        run(lambda _r: httpx.Response(200, json=body), tmp_path)


def test_a_page_key_that_is_not_a_valid_url_part_is_an_error_not_a_traceback(
    tmp_path: Path,
) -> None:
    body = {"items": [], "nextPageKey": "a\x00b" * 10}
    with pytest.raises(ClassicError):
        run(lambda _r: httpx.Response(200, json=body), tmp_path)


def test_a_deeply_nested_body_is_an_error_not_a_traceback(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"[" * 100_000, headers={"content-type": "application/json"}
        )

    with pytest.raises(ClassicError, match="nested"):
        run(handler, tmp_path)


def test_the_cli_never_shows_a_traceback_for_hostile_bodies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bodies = [b"[" * 100_000, json.dumps({"items": [], "nextPageKey": "k" * 100_000}).encode()]
    for n, body in enumerate(bodies):
        monkeypatch.setattr(
            command,
            "http_getter",
            lambda token, url, body=body: http_getter(
                token,
                url,
                transport=httpx.MockTransport(lambda _r: httpx.Response(200, content=body)),
            ),
        )
        monkeypatch.setenv("DYNATRACE_API_TOKEN", TOKEN)
        monkeypatch.setenv("DYNATRACE_ENV_URL", APPS)
        result = CliRunner().invoke(
            app, ["extract-dynatrace-classic", "--out", str(tmp_path / str(n))]
        )
        assert result.exit_code == 1 and "Traceback" not in result.output
        assert isinstance(result.exception, SystemExit)


# ----- 10. event types that open no problem ----------------------------------------------------


@pytest.mark.parametrize(
    "kind", ["CUSTOM_ANNOTATION", "INFO", "CUSTOM_DEPLOYMENT", "CUSTOM_CONFIGURATION"]
)
def test_informational_event_types_are_not_paging_conditions(kind: str) -> None:
    m = one(event(template={"eventType": kind}))
    assert m.confidence is Confidence.MANUAL
    assert "does not open a problem" in m.reasons[0]


@pytest.mark.parametrize("kind", ["ERROR", "AVAILABILITY", "SLOWDOWN", "RESOURCE", "CUSTOM_ALERT"])
def test_problem_event_types_convert(kind: str) -> None:
    assert one(event(template={"eventType": kind})).confidence is Confidence.APPROXIMATE


def test_davis_merge_is_noted_when_present() -> None:
    m = one(event(template={"eventType": "ERROR", "davisMerge": True}))
    assert "davisMerge" in " ".join(m.reasons)
    assert "davisMerge" not in " ".join(one(event()).reasons)


# ----- 12. smaller points ---------------------------------------------------------------------


def test_division_by_zero_is_manual() -> None:
    assert one(sel(f"({CPU}:avg) / 0")).confidence is Confidence.MANUAL


def test_a_repeated_split_dimension_is_listed_once_in_order() -> None:
    q = to_query(f'{CPU}:splitBy("dt.entity.host","dt.entity.host"):avg')
    assert q.terms[0].group_by == ["dt.entity.host"]
    m = one(sel(f'{CPU}:splitBy("dt.entity.host","dt.entity.host"):avg'))
    assert nrql_for(m).endswith("FACET hostname")


def test_the_default_dealerting_count_is_not_noted() -> None:
    assert "dealertingSamples" not in " ".join(one(event(model={"dealertingSamples": 5})).reasons)
    assert "dealertingSamples" in " ".join(one(event(model={"dealertingSamples": 7})).reasons)


def test_an_empty_name_has_its_own_placeholder() -> None:
    from importer.sources.dynatrace.classic.common import shown

    assert shown("") == "<an empty value>"


@pytest.mark.parametrize(
    "field", ["violatingSamples", "samples", "dealertingSamples", "queryOffset"]
)
def test_a_boolean_is_not_a_count(field: str) -> None:
    obj = event({"queryOffset": True} if field == "queryOffset" else None, {field: True})
    assert obj.problem


def test_a_string_enabled_flag_is_not_read_as_enabled() -> None:
    value = models.reduce_notification({"enabled": "false", "type": "EMAIL", "displayName": "n"})
    assert "enabled" not in value


def test_bidi_and_zero_width_characters_are_removed_from_names() -> None:
    nasty = "pay‮load​⁦x⁩\u0000 ok"
    for obj in (
        event(summary=nasty),
        parse_object({"objectId": "p", "schemaId": models.PROFILE, "value": {"name": nasty}}),
    ):
        name = one(obj).name
        assert name == "payloadx ok" or name == "payloadx?ok" or "‮" not in name
        assert not any(c in name for c in "‮​⁦⁩\u0000")


def test_count_median_and_value_carry_the_partial_notes() -> None:
    text = " ".join(one(sel(f"{CPU}:count")).reasons)
    assert "counts events" in text
    assert "median" in " ".join(one(sel(f"{CPU}:median")).reasons)
    assert "latest" in " ".join(one(sel(f"{CPU}:value")).reasons)


def test_remediation_does_not_point_at_a_file_inside_the_package() -> None:
    from importer.sources.dynatrace.classic import remediation_rules

    for _p, _t, steps in remediation_rules.RULES:
        assert not any("src/importer" in s for s in steps)


# ----- a user metric map ----------------------------------------------------------------------


ROW: dict[str, Any] = {
    "keys": ["my.metric"],
    "event": "MyEvent",
    "attribute": "value",
    "unit_factor": 1,
    "dimensions": {},
    "default_aggregation": "average",
    "status": "unverified",
}


def write_map(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"default_prerequisite": "d", "metrics": rows}))
    return path


def test_a_user_map_is_used_when_the_environment_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert one(event({"metricKey": "my.metric"})).confidence is Confidence.MANUAL
    monkeypatch.setenv("IMPORTER_DYNATRACE_METRIC_MAP", str(write_map(tmp_path, [ROW])))
    m = one(event({"metricKey": "my.metric"}))
    assert m.confidence is Confidence.APPROXIMATE
    assert nrql_for(m) == "SELECT average(value) FROM MyEvent"


def test_a_bad_user_map_is_a_clear_error_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "IMPORTER_DYNATRACE_METRIC_MAP", str(write_map(tmp_path, [{**ROW, "event": "x;y"}]))
    )
    result = CliRunner().invoke(app, ["validate-dynatrace-map", str(tmp_path / "map.json")])
    assert result.exit_code == 1 and "metric map" in result.output.lower()


def test_the_validate_command_accepts_the_shipped_map_and_refuses_hostile_rows(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    ok = runner.invoke(app, ["validate-dynatrace-map", str(metric_map.MAP_PATH)])
    assert ok.exit_code == 0 and "rows" in ok.output
    for bad in (
        {"select": "count(*); DROP"},
        {"attribute": None, "select": "x" * 300},
        {"unit_factor": 0},
        {"status": "great"},
        {"dimensions": {"d": "a b"}},
    ):
        result = runner.invoke(
            app, ["validate-dynatrace-map", str(write_map(tmp_path, [{**ROW, **bad}]))]
        )
        assert result.exit_code == 1, bad


def test_the_environment_variable_with_a_missing_file_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IMPORTER_DYNATRACE_METRIC_MAP", str(tmp_path / "missing.json"))
    with pytest.raises(metric_map.MapError):
        metric_map.active_map()
