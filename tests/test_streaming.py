from importer.sources.cloudwatch.dashboard_models import CloudWatchDashboard
from importer.sources.cloudwatch.models import CloudWatchAlarm
from importer.sources.cloudwatch.streaming import streaming_inputs

SQS = {"Namespace": "AWS/SQS", "MetricName": "M"}


def alarm(**fields: object) -> CloudWatchAlarm:
    base: dict[str, object] = {
        "AlarmName": "a",
        "EvaluationPeriods": 1,
        "ComparisonOperator": "GreaterThanThreshold",
        "Namespace": "AWS/EC2",
        "MetricName": "CPUUtilization",
        "Statistic": "Average",
    }
    return CloudWatchAlarm.model_validate({**base, **fields})


def dash(*rows: object, stat: str = "Average") -> CloudWatchDashboard:
    widget = {"type": "metric", "properties": {"metrics": list(rows), "stat": stat}}
    return CloudWatchDashboard(name="d", widgets=[widget, {"type": "text", "properties": {}}])


def test_namespaces_come_from_alarms_and_dashboards() -> None:
    ns, specs = streaming_inputs(
        [alarm()],
        [dash(["AWS/Lambda", "Errors", "FunctionName", "f"], [".", "Invocations", ".", "."])],
    )
    assert ns == ["AWS/EC2", "AWS/Lambda"] and specs == []


def test_percentile_alarms_and_widgets_ask_for_the_percentile() -> None:
    pct = alarm(
        Namespace="AWS/ApiGateway", MetricName="Latency", Statistic=None, ExtendedStatistic="p99"
    )
    widget = dash(["AWS/ApiGateway", "Latency", "ApiName", "a", {"stat": "p95"}])
    _, specs = streaming_inputs([pct], [widget])
    assert specs == [
        {"namespace": "AWS/ApiGateway", "metric_name": "Latency", "statistics": ["p95", "p99"]}
    ]


def test_metric_math_queries_and_odd_rows_are_handled() -> None:
    math = alarm(
        Namespace=None,
        MetricName=None,
        Statistic=None,
        Metrics=[
            {
                "Id": "m1",
                "MetricStat": {
                    "Metric": {"Namespace": "AWS/SQS", "MetricName": "M"},
                    "Period": 60,
                    "Stat": "Sum",
                },
            },
            {"Id": "e", "Expression": "m1*2"},
        ],
    )
    widget = dash(
        [{"expression": "m1"}], "junk", [], ["AWS/SNS", "NumberOfMessagesPublished"], ["...", "x"]
    )
    ns, specs = streaming_inputs([math], [widget])
    assert ns == ["AWS/SNS", "AWS/SQS"] and specs == []
