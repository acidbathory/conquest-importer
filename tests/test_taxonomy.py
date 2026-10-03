from pathlib import Path

import pytest

from importer.core.taxonomy import Category, classify
from importer.sources import REGISTRY

FIXTURES = Path(__file__).parent / "fixtures"
C = Category


@pytest.mark.parametrize(
    ("reason", "category"),
    [
        ("no New Relic mapping for metric 'x'; add it to metric_map.json", C.MISSING_MAPPING),
        ("no New Relic mapping for namespace 'Custom/A'; add it", C.MISSING_MAPPING),
        ("not emitted: evaluation window 172800s is outside 60s-24h", C.CANNOT_EMIT),
        ("monitor type 'slo alert'", C.UNSUPPORTED_FEATURE),
        ("composite alarm combines 2 alarms (a, b); no composite condition", C.UNSUPPORTED_FEATURE),
        ("anomaly detection alarms are not supported yet", C.UNSUPPORTED_FEATURE),
        ("percentile statistic 'p99' is not supported yet", C.UNSUPPORTED_FEATURE),
        ("no New Relic equivalent for service check 'x'", C.UNSUPPORTED_FEATURE),
        ("forecast monitors are not supported", C.UNSUPPORTED_FEATURE),
        ("statistic 'x' is not supported yet", C.UNSUPPORTED_QUERY),
        ("metric math functions are not supported yet", C.UNSUPPORTED_QUERY),
        ("query uses functions or syntax that is not supported yet", C.UNSUPPORTED_QUERY),
        ("service check last(n) must be between 1 and 60", C.UNSUPPORTED_QUERY),
        ("scope value contains unsupported characters", C.UNSUPPORTED_QUERY),
        ("alarm evaluates more than one metric query", C.UNSUPPORTED_QUERY),
        ("metrics in the expression have different periods", C.UNSUPPORTED_QUERY),
        ("alarm has no metric", C.UNSUPPORTED_QUERY),
        # ordering pitfalls
        ("critical threshold in options differs from the query; using the query", C.APPROXIMATION),
        (
            "AWS metric names follow the Metric Streams convention: assumed for other",
            C.UNVERIFIED_NAMES,
        ),
        ("check interval is assumed to be 1 minute", C.APPROXIMATION),
        ("TreatMissingData=x has no equivalent; New Relic leaves it unchanged", C.APPROXIMATION),
        ("3 of 5 datapoints is approximated as 3 consecutive breaches", C.APPROXIMATION),
        ("recovery thresholds are not supported and were dropped", C.APPROXIMATION),
        ("log attribute names are unverified", C.UNVERIFIED_NAMES),
        ("alarm actions (such as SNS topics) must be configured manually", C.MANUAL_FOLLOWUP),
        ("settings not migrated: priority", C.MANUAL_FOLLOWUP),
        ("message uses template variables that may not exist in New Relic", C.MANUAL_FOLLOWUP),
        ("something nobody has seen before", C.OTHER),
    ],
)
def test_classify(reason: str, category: Category) -> None:
    assert classify(reason) == category


@pytest.mark.parametrize(
    ("source", "folder"),
    [("datadog", "datadog"), ("cloudwatch", "cloudwatch"), ("cloudwatch", "cloudwatch-real")],
)
def test_every_reason_in_the_fixtures_is_categorised(source: str, folder: str) -> None:
    monitors = REGISTRY[source].convert(FIXTURES / folder)
    reasons = {r for m in monitors for r in m.reasons}
    assert reasons, "fixture should produce reasons"
    uncategorised = sorted(r for r in reasons if classify(r) == Category.OTHER)
    assert not uncategorised, f"add a taxonomy rule for: {uncategorised}"
