"""Data source-managed rules (Mimir and Loki ruler, Prometheus rule files) keep their query."""

import json
from pathlib import Path

from importer.sources.grafana.models import load_rules

RULES2 = Path(__file__).parent / "fixtures" / "grafana-rules2"


def test_a_rule_file_keeps_the_expression_of_each_rule() -> None:
    rules = load_rules(RULES2 / "prometheus_rule_file.json")
    by_title = {r.title: r for r in rules}
    assert by_title["PaymentsErrorRate"].expr == (
        'sum(rate(payments_errors_total{env="prod"}[5m])) > 2'
    )
    assert by_title["PaymentsErrorRate"].datasource_managed
    assert by_title["PaymentsErrorRate"].for_seconds == 600
    assert by_title["PaymentsErrorRate"].interval_seconds == 60
    assert by_title["PaymentsErrorRate"].annotations["description"] == "Error rate is high"


def test_a_recording_rule_in_a_rule_file_is_a_recording_rule() -> None:
    rules = load_rules(RULES2 / "prometheus_rule_file.json")
    (recording,) = (r for r in rules if r.title == "job:payments_errors:rate5m")
    assert recording.record is not None


def test_a_ruler_namespace_with_a_loki_rule_keeps_its_expression(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    expr = 'sum(count_over_time({app="api"} |= "error" [5m])) > 10'
    path.write_text(json.dumps({"ns": [{"name": "g", "rules": [{"alert": "A", "expr": expr}]}]}))
    (rule,) = load_rules(path)
    assert rule.expr == expr and rule.datasource_managed
