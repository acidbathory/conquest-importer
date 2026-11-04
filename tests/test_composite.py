import pytest

from importer.core.ir import Confidence
from importer.sources.cloudwatch import translate
from importer.sources.cloudwatch.composite import Shape, analyse_rule, remediation
from importer.sources.cloudwatch.models import CloudWatchAlarm, CompositeAlarm


@pytest.mark.parametrize(
    ("rule", "shape", "children"),
    [
        ('ALARM("a")', Shape.ANY, ("a",)),
        ('ALARM("a") OR ALARM("b") OR ALARM("a")', Shape.ANY, ("a", "b")),
        ('ALARM("a") AND ALARM("b")', Shape.ALL, ("a", "b")),
        ('ALARM("a") AND ALARM("b") OR ALARM("c")', Shape.COMPLEX, ("a", "b", "c")),
        ('ALARM("a") AND NOT ALARM("b")', Shape.COMPLEX, ("a", "b")),
        ('ALARM("a") AND OK("b")', Shape.COMPLEX, ("a", "b")),
        ('(ALARM("a") OR ALARM("b")) AND ALARM("c")', Shape.COMPLEX, ("a", "b", "c")),
        ("TRUE", Shape.COMPLEX, ()),
        (
            "ALARM(arn:aws:cloudwatch:us-east-1:111122223333:alarm:my-alarm)",
            Shape.ANY,
            ("my-alarm",),
        ),
        ('ALARM("a") AND', Shape.UNPARSED, ()),
        ('ALARM("a") ALARM("b")', Shape.UNPARSED, ()),
        ('(ALARM("a")', Shape.UNPARSED, ()),
        ('ALARM("a"))', Shape.UNPARSED, ()),
        ("", Shape.UNPARSED, ()),
        ("rm -rf /", Shape.UNPARSED, ()),
    ],
)
def test_rule_shapes(rule: str, shape: Shape, children: tuple[str, ...]) -> None:
    info = analyse_rule(rule)
    assert info.shape is shape
    assert info.children == children


def test_or_composite_says_no_combined_condition_is_needed() -> None:
    info = analyse_rule('ALARM("a") OR ALARM("b")')
    steps = remediation(info, {"a": Confidence.APPROXIMATE, "b": Confidence.APPROXIMATE}, [])
    assert "No combined condition is needed" in steps[0]
    assert any("convert to conditions: a, b" in s for s in steps)


def test_and_composite_lists_unconverted_children_and_options() -> None:
    info = analyse_rule('ALARM("a") AND ALARM("b")')
    steps = remediation(info, {"a": Confidence.APPROXIMATE, "b": Confidence.MANUAL}, ["arn:x"])
    text = " ".join(steps)
    assert "ONE NRQL condition" in text
    assert "do NOT convert (fix these first, see their own entries): b" in text
    assert "1 action target" in text


def test_translate_wires_child_status_into_the_composite() -> None:
    child = CloudWatchAlarm.model_validate(
        {
            "AlarmName": "cpu",
            "Namespace": "AWS/EC2",
            "MetricName": "CPUUtilization",
            "Dimensions": [{"Name": "InstanceId", "Value": "i-1"}],
            "Statistic": "Average",
            "Period": 300,
            "EvaluationPeriods": 1,
            "Threshold": 80,
            "ComparisonOperator": "GreaterThanThreshold",
        }
    )
    comp = CompositeAlarm(AlarmName="both", AlarmRule='ALARM("cpu") AND ALARM("gone")')
    result = translate([child, comp])
    composite = result[1]
    assert composite.confidence is Confidence.UNSUPPORTED
    assert any("convert to conditions: cpu" in s for s in composite.remediation)
    assert any("not found in this export" in s and "gone" in s for s in composite.remediation)
