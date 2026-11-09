"""NRQL filter clauses and the saved IR: existing output stays byte-identical."""

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.emit.nrql import build_nrql
from importer.core.ir import (
    Aggregation,
    Confidence,
    CountFilter,
    CountSignal,
    IRMonitor,
    MetricSignal,
    MetricTerm,
    NrTarget,
    Signal,
    SignalKind,
    TagFilter,
)
from importer.sources import REGISTRY

FIXTURES = Path(__file__).parent / "fixtures"


def metric(scope: list[TagFilter]) -> IRMonitor:
    term = MetricTerm(
        metric="m",
        space_aggregation=Aggregation.AVG,
        nr_target=NrTarget(event="Metric", attribute="m"),
    )
    signal = MetricSignal(terms={"a": term}, aggregation=Aggregation.AVG, scope=scope)
    return IRMonitor(
        source_system="x",
        source_id=1,
        source_type="t",
        name="n",
        signal=Signal(kind=SignalKind.METRIC, raw_query="q", metric=signal),
        confidence=Confidence.APPROXIMATE,
    )


def logs(filters: list[CountFilter]) -> IRMonitor:
    return IRMonitor(
        source_system="x",
        source_id=1,
        source_type="t",
        name="n",
        signal=Signal(kind=SignalKind.LOG, raw_query="q", count=CountSignal(filters=filters)),
        confidence=Confidence.APPROXIMATE,
    )


@pytest.mark.parametrize(
    ("scope", "clause"),
    [
        (TagFilter(key="a", value="x"), "a = 'x'"),
        (TagFilter(key="a", value="x", negate=True), "a != 'x'"),
        (TagFilter(key="a", absent=True), "a IS NULL"),
        (TagFilter(key="a", absent=True, negate=True), "a IS NOT NULL"),
        (TagFilter(key="a", values=["x", "y"]), "a IN ('x', 'y')"),
        (TagFilter(key="a", values=["x", "y"], negate=True), "a NOT IN ('x', 'y')"),
        (TagFilter(key="a", value="x*", wildcard=True), "a LIKE 'x%'"),
        (TagFilter(key="a", value="*x*", wildcard=True, negate=True), "a NOT LIKE '%x%'"),
    ],
)
def test_metric_scope_clauses(scope: TagFilter, clause: str) -> None:
    assert build_nrql(metric([scope])) == f"SELECT average(m) FROM Metric WHERE {clause}"  # noqa: S608 - expected text


@pytest.mark.parametrize(
    ("flt", "clause"),
    [
        (CountFilter(key="a", value="x"), "a = 'x'"),
        (CountFilter(key="a", value="x", negate=True), "a != 'x'"),
        (CountFilter(key="a", value="", values=["x", "y"]), "a IN ('x', 'y')"),
        (CountFilter(key="a", value="", values=["x"], negate=True), "a NOT IN ('x')"),
        (CountFilter(key="a", value="x*", wildcard=True), "a LIKE 'x%'"),
        (CountFilter(key="a", value="*x", wildcard=True, negate=True), "a NOT LIKE '%x'"),
    ],
)
def test_count_filter_clauses_are_unchanged(flt: CountFilter, clause: str) -> None:
    assert build_nrql(logs([flt])) == f"SELECT count(*) FROM Log WHERE {clause}"  # noqa: S608 - expected text


@pytest.mark.parametrize("value", ["50%", "%x*", "*a%b*"])
def test_a_literal_percent_in_a_wildcard_value_is_refused_at_emit(value: str) -> None:
    with pytest.raises(ValueError, match="%"):
        build_nrql(metric([TagFilter(key="a", value=value, wildcard=True)]))
    with pytest.raises(ValueError, match="%"):
        build_nrql(logs([CountFilter(key="a", value=value, wildcard=True)]))


def test_a_percent_in_a_plain_equals_value_is_not_a_wildcard_and_is_kept() -> None:
    assert build_nrql(logs([CountFilter(key="a", value="50%")])).endswith("a = '50%'")


def test_one_helper_builds_both_clauses() -> None:
    from importer.core.emit import nrql

    assert hasattr(nrql, "_match_clause")


def test_default_filter_fields_are_left_out_of_the_saved_tag_filter() -> None:
    assert TagFilter(key="a", value="x").model_dump(mode="json") == {
        "key": "a",
        "value": "x",
        "absent": False,
    }
    assert json.loads(TagFilter(key="a", absent=True).model_dump_json()) == {
        "key": "a",
        "value": "",
        "absent": True,
    }
    full = TagFilter(key="a", values=["x"], negate=True, wildcard=True).model_dump(mode="json")
    assert full["values"] == ["x"] and full["negate"] is True and full["wildcard"] is True


def test_a_tag_filter_with_extra_fields_round_trips() -> None:
    for tag in (
        TagFilter(key="a", value="x"),
        TagFilter(key="a", values=["x", "y"], negate=True),
        TagFilter(key="a", value="x*", wildcard=True),
    ):
        assert TagFilter.model_validate_json(tag.model_dump_json()) == tag


def test_the_count_filter_keeps_its_saved_shape() -> None:
    assert CountFilter(key="a", value="x").model_dump(mode="json") == {
        "key": "a",
        "value": "x",
        "negate": False,
        "wildcard": False,
        "values": [],
    }


def convert_hashes(source: str, folder: str, tmp_path: Path) -> dict[str, str]:
    out = tmp_path / folder
    args = ["convert", "--source", source, "--in", str(FIXTURES / folder), "--out", str(out)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    return {
        f"{folder}/{f.relative_to(out)}": hashlib.sha256(f.read_bytes()).hexdigest()
        for f in sorted(out.rglob("*.json"))
    }


def test_the_saved_ir_of_the_other_sources_is_byte_identical_to_before_grafana(
    tmp_path: Path,
) -> None:
    before = json.loads((FIXTURES / "ir-before-grafana.json").read_text())
    now: dict[str, str] = {}
    for source, folder in (
        ("cloudwatch", "cloudwatch"),
        ("cloudwatch", "cloudwatch-real"),
        ("gcp", "gcp"),
        ("dynatrace", "dynatrace"),
    ):
        now |= convert_hashes(source, folder, tmp_path)
    assert now == before


def test_the_grafana_ir_round_trips_through_the_saved_files(tmp_path: Path) -> None:
    out = tmp_path / "o"
    args = ["convert", "--source", "grafana", "--in", str(FIXTURES / "grafana"), "--out", str(out)]
    assert CliRunner().invoke(app, args).exit_code == 0
    expected = {m.source_id: m for m in REGISTRY["grafana"].convert(FIXTURES / "grafana")}
    loaded = {
        m.source_id: m
        for m in (IRMonitor.model_validate_json(f.read_text()) for f in (out / "ir").glob("*.json"))
    }
    assert loaded == expected and len(loaded) == 21
    text = "".join(f.read_text() for f in (out / "ir").glob("*.json"))
    assert '"absent": false' in text  # the stored scope filter keeps its old shape
