"""The New Relic side: NRQL rewrite, variable fill, row parsing and caps."""

from typing import Any

import pytest

from importer.parity.model import ParityError
from importer.parity.newrelic import MAX_SERIES, NewRelicReader, check_buckets, parse_rows
from importer.parity.queries import (
    Unfilled,
    facet_names,
    fill_nrql,
    fill_promql,
    longest_window,
    timeseries_over,
)

NRQL = (
    "SELECT rate(sum(http_requests_total), 1 second) FROM Metric WHERE job IN ({{job}}) "
    "FACET code, `my.host` LIMIT MAX TIMESERIES AUTO"
)


def test_timeseries_is_replaced_and_window_fixed() -> None:
    out = timeseries_over(NRQL, 600, 1200, 60)
    assert out.endswith("TIMESERIES 60 SECONDS SINCE 600 UNTIL 1200")
    assert "AUTO" not in out


def test_only_a_single_select_is_sent() -> None:
    with pytest.raises(ParityError):
        timeseries_over("DROP everything; SELECT 1 FROM Metric", 0, 60, 60)
    with pytest.raises(ParityError):
        timeseries_over("SELECT 1 FROM Metric; SELECT 2 FROM Metric", 0, 60, 60)


def test_facet_names() -> None:
    assert facet_names(NRQL) == ["code", "my.host"]
    assert facet_names("SELECT count(*) FROM Log") == []


def test_variables_fill_with_quoting_and_refuse_unsafe_or_missing() -> None:
    assert fill_nrql(NRQL, {"job": ("api", "web")}).count("'api', 'web'") == 1
    with pytest.raises(Unfilled):
        fill_nrql(NRQL, {})
    with pytest.raises(Unfilled):
        fill_nrql(NRQL, {"job": ("*",)})
    with pytest.raises(Unfilled):
        fill_nrql(NRQL, {"job": ("a') OR 1=1 --",)})
    promql = 'rate(x{job=~"$job", i="${i}"}[$__rate_interval])'
    assert fill_promql(promql, {"job": ("a", "b"), "i": ("1",)}, 60, 6) == (
        'rate(x{job=~"a|b", i="1"}[60s])'
    )
    with pytest.raises(Unfilled):
        fill_promql('x{a="$nope"}', {}, 60, 6)


def test_longest_window() -> None:
    assert longest_window("rate(a[5m]) / rate(b[1h])") == 3600
    assert longest_window("up") is None


def test_rows_become_series_by_facet() -> None:
    rows: list[dict[str, Any]] = [
        {"beginTimeSeconds": 60, "endTimeSeconds": 120, "facet": ["200", "h1"], "v": 1.5},
        {"beginTimeSeconds": 120, "endTimeSeconds": 180, "facet": ["200", "h1"], "v": None},
        {"beginTimeSeconds": 60, "endTimeSeconds": 120, "facet": ["500", "h1"], "v": 2},
    ]
    got = parse_rows(rows, ["code", "host"])
    assert [s.labels for s in got.series] == [
        {"code": "200", "host": "h1"},
        {"code": "500", "host": "h1"},
    ]
    assert got.series[0].points == {60: 1.5}


def test_several_values_per_bucket_are_refused() -> None:
    with pytest.raises(ParityError, match="several values"):
        parse_rows([{"beginTimeSeconds": 60, "a": 1, "b": 2}], [])


def test_series_cap_and_hostile_labels() -> None:
    rows = [{"beginTimeSeconds": 60, "facet": f"s{i}", "v": 1} for i in range(MAX_SERIES + 3)]
    got = parse_rows(rows, ["x"])
    assert len(got.series) == MAX_SERIES and got.truncated
    evil = parse_rows([{"beginTimeSeconds": 60, "facet": "\x1b[0m‮" + "q" * 999, "v": 1}], ["x"])
    assert "\x1b" not in evil.series[0].labels["x"] and len(evil.series[0].labels["x"]) <= 200


def test_reader_sends_one_rewritten_query_and_counts() -> None:
    sent: list[str] = []

    def rows(query: str) -> list[dict[str, Any]]:
        sent.append(query)
        return []

    reader = NewRelicReader(rows)
    reader("SELECT average(x) FROM Metric TIMESERIES AUTO", 0, 600, 60)
    assert reader.requests == 1 and "SINCE 0 UNTIL 600" in sent[0]


def test_bucket_limit_explains_the_fix() -> None:
    check_buckets(6, 60)
    with pytest.raises(ParityError, match="--step"):
        check_buckets(24, 60)
