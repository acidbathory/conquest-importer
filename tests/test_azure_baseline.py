"""The baseline document and the code that measures it stay equal."""

from pathlib import Path

from importer.core.ir import Confidence
from importer.sources.azure.baseline import measure, render

ROOT = Path(__file__).resolve().parent.parent


def test_the_table_in_the_baseline_document_is_what_the_code_measures() -> None:
    table = render(measure(ROOT / "tests" / "fixtures" / "azure"))
    document = (ROOT / "docs" / "research" / "azure-baseline.md").read_text(encoding="utf-8")
    assert table in document


def test_only_alert_rules_with_a_resource_body_are_counted() -> None:
    rows = measure(ROOT / "tests" / "fixtures" / "azure")
    assert next(r.label for r in rows) == "Metric alerts"
    assert all(r.total > 0 for r in rows)
    for r in rows:  # every rule is exactly one of converted, manual or unsupported
        assert r.total == r.converted + r.count(Confidence.MANUAL, Confidence.UNSUPPORTED)
