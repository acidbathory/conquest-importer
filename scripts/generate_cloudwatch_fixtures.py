"""Generate synthetic CloudWatch alarms in the shape of `aws cloudwatch describe-alarms`.

SYNTHETIC: written from the documented API shape, not exported from a real AWS account.
Replace or extend with real `aws cloudwatch describe-alarms --output json` output when a
customer or test account is available (check it for sensitive values first).

Usage:  uv run python scripts/generate_cloudwatch_fixtures.py
"""

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).parent.parent / "tests" / "fixtures" / "cloudwatch" / "describe-alarms.json"
ACCOUNT = "123456789012"
REGION = "us-east-1"
SNS = f"arn:aws:sns:{REGION}:{ACCOUNT}:ops-alerts"


def dims(**kv: str) -> list[dict[str, str]]:
    return [{"Name": k, "Value": v} for k, v in kv.items()]


def alarm(name: str, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "AlarmName": name,
        "AlarmArn": f"arn:aws:cloudwatch:{REGION}:{ACCOUNT}:alarm:{name}",
        "AlarmDescription": "Synthetic sample alarm. Safe to ignore.",
        "AlarmConfigurationUpdatedTimestamp": "2026-10-01T12:00:00.000000+00:00",
        "ActionsEnabled": True,
        "OKActions": [],
        "AlarmActions": [SNS],
        "InsufficientDataActions": [],
        "StateValue": "OK",
        "StateReason": "Threshold Crossings: synthetic",
        "StateUpdatedTimestamp": "2026-10-01T12:00:00.000000+00:00",
        "EvaluationPeriods": 1,
        "TreatMissingData": "missing",
    }
    base.update(fields)
    return base


def metric_query(
    qid: str, ns: str, metric: str, stat: str, period: int, **d: str
) -> dict[str, Any]:
    return {
        "Id": qid,
        "MetricStat": {
            "Metric": {"Namespace": ns, "MetricName": metric, "Dimensions": dims(**d)},
            "Period": period,
            "Stat": stat,
        },
        "ReturnData": False,
    }


GT, GE, LT = "GreaterThanThreshold", "GreaterThanOrEqualToThreshold", "LessThanThreshold"

METRIC_ALARMS: list[dict[str, Any]] = [
    alarm(
        "ec2-cpu-high",
        Namespace="AWS/EC2",
        MetricName="CPUUtilization",
        Statistic="Average",
        Dimensions=dims(InstanceId="i-0abc123def4567890"),
        Period=300,
        EvaluationPeriods=3,
        DatapointsToAlarm=3,
        Threshold=80.0,
        ComparisonOperator=GT,
        Unit="Percent",
    ),
    alarm(
        "ec2-status-check-failed",
        Namespace="AWS/EC2",
        MetricName="StatusCheckFailed",
        Statistic="Maximum",
        Dimensions=dims(InstanceId="i-0abc123def4567890"),
        Period=60,
        EvaluationPeriods=2,
        Threshold=1.0,
        ComparisonOperator=GE,
    ),
    alarm(
        "rds-cpu-high",
        Namespace="AWS/RDS",
        MetricName="CPUUtilization",
        Statistic="Average",
        Dimensions=dims(DBInstanceIdentifier="prod-db-1"),
        Period=300,
        EvaluationPeriods=2,
        Threshold=90.0,
        ComparisonOperator=GT,
    ),
    alarm(
        "rds-free-storage-low",
        Namespace="AWS/RDS",
        MetricName="FreeStorageSpace",
        Statistic="Minimum",
        Dimensions=dims(DBInstanceIdentifier="prod-db-1"),
        Period=300,
        EvaluationPeriods=1,
        Threshold=5000000000.0,
        ComparisonOperator=LT,
        TreatMissingData="breaching",
    ),
    alarm(
        "rds-connections-3-of-5",
        Namespace="AWS/RDS",
        MetricName="DatabaseConnections",
        Statistic="Average",
        Dimensions=dims(DBInstanceIdentifier="prod-db-1"),
        Period=60,
        EvaluationPeriods=5,
        DatapointsToAlarm=3,
        Threshold=500.0,
        ComparisonOperator=GT,
    ),
    alarm(
        "alb-5xx-any",
        Namespace="AWS/ApplicationELB",
        MetricName="HTTPCode_Target_5XX_Count",
        Statistic="Sum",
        Dimensions=dims(LoadBalancer="app/my-alb/50dc6c495c0c9188"),
        Period=60,
        EvaluationPeriods=5,
        DatapointsToAlarm=1,
        Threshold=10.0,
        ComparisonOperator=GT,
        TreatMissingData="notBreaching",
    ),
    alarm(
        "alb-latency-p99",
        Namespace="AWS/ApplicationELB",
        MetricName="TargetResponseTime",
        ExtendedStatistic="p99",
        Dimensions=dims(LoadBalancer="app/my-alb/50dc6c495c0c9188"),
        Period=60,
        EvaluationPeriods=3,
        Threshold=2.0,
        ComparisonOperator=GT,
    ),
    alarm(
        "lambda-errors",
        Namespace="AWS/Lambda",
        MetricName="Errors",
        Statistic="Sum",
        Dimensions=dims(FunctionName="checkout"),
        Period=60,
        EvaluationPeriods=1,
        Threshold=0.0,
        ComparisonOperator=GT,
    ),
    alarm(
        "lambda-duration-high",
        Namespace="AWS/Lambda",
        MetricName="Duration",
        Statistic="Average",
        Dimensions=dims(FunctionName="checkout"),
        Period=300,
        EvaluationPeriods=2,
        Threshold=3000.0,
        ComparisonOperator=GT,
        Unit="Milliseconds",
    ),
    alarm(
        "dynamodb-throttled",
        Namespace="AWS/DynamoDB",
        MetricName="ThrottledRequests",
        Statistic="Sum",
        Dimensions=dims(TableName="orders", Operation="PutItem"),
        Period=60,
        EvaluationPeriods=1,
        Threshold=1.0,
        ComparisonOperator=GE,
    ),
    alarm(
        "sqs-oldest-message",
        Namespace="AWS/SQS",
        MetricName="ApproximateAgeOfOldestMessage",
        Statistic="Maximum",
        Dimensions=dims(QueueName="jobs"),
        Period=300,
        EvaluationPeriods=1,
        Threshold=600.0,
        ComparisonOperator=GT,
    ),
    alarm(
        "ecs-service-cpu",
        Namespace="AWS/ECS",
        MetricName="CPUUtilization",
        Statistic="Average",
        Dimensions=dims(ClusterName="prod", ServiceName="api"),
        Period=60,
        EvaluationPeriods=5,
        Threshold=85.0,
        ComparisonOperator=GT,
    ),
    # metric math: error rate
    alarm(
        "lambda-error-rate",
        Metrics=[
            metric_query("m1", "AWS/Lambda", "Errors", "Sum", 60, FunctionName="checkout"),
            metric_query("m2", "AWS/Lambda", "Invocations", "Sum", 60, FunctionName="checkout"),
            {"Id": "e1", "Expression": "m1 / m2 * 100", "Label": "Error rate", "ReturnData": True},
        ],
        EvaluationPeriods=3,
        DatapointsToAlarm=3,
        Threshold=5.0,
        ComparisonOperator=GT,
    ),
    # console-style single metric inside a Metrics array
    alarm(
        "ec2-network-in",
        Metrics=[
            {
                **metric_query(
                    "m1", "AWS/EC2", "NetworkIn", "Average", 300, InstanceId="i-0abc123def4567890"
                ),
                "ReturnData": True,
            },
        ],
        EvaluationPeriods=2,
        Threshold=100000000.0,
        ComparisonOperator=GT,
    ),
    alarm(
        "lambda-fill-function",
        Metrics=[
            metric_query("m1", "AWS/Lambda", "Errors", "Sum", 60, FunctionName="checkout"),
            {"Id": "e1", "Expression": "FILL(m1, 0)", "ReturnData": True},
        ],
        EvaluationPeriods=1,
        Threshold=1.0,
        ComparisonOperator=GT,
    ),
    alarm(
        "ec2-cpu-anomaly",
        Metrics=[
            {
                "Id": "m1",
                "ReturnData": True,
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/EC2",
                        "MetricName": "CPUUtilization",
                        "Dimensions": dims(InstanceId="i-0abc"),
                    },
                    "Period": 300,
                    "Stat": "Average",
                },
            },
            {"Id": "ad1", "Expression": "ANOMALY_DETECTION_BAND(m1, 2)", "ReturnData": True},
        ],
        EvaluationPeriods=2,
        ThresholdMetricId="ad1",
        ComparisonOperator="LessThanLowerOrGreaterThanUpperThreshold",
    ),
    alarm(
        "ec2-high-resolution",
        Namespace="AWS/EC2",
        MetricName="CPUUtilization",
        Statistic="Average",
        Dimensions=dims(InstanceId="i-0abc"),
        Period=10,
        EvaluationPeriods=3,
        Threshold=90.0,
        ComparisonOperator=GT,
    ),
    alarm(
        "s3-daily-size",
        Namespace="AWS/S3",
        MetricName="BucketSizeBytes",
        Statistic="Average",
        Dimensions=dims(BucketName="media", StorageType="StandardStorage"),
        Period=86400,
        EvaluationPeriods=1,
        Threshold=1000000000000.0,
        ComparisonOperator=GT,
    ),
    alarm(
        "custom-app-queue-depth",
        Namespace="Custom/MyApp",
        MetricName="QueueDepth",
        Statistic="Average",
        Period=60,
        EvaluationPeriods=1,
        Threshold=100.0,
        ComparisonOperator=GT,
    ),
    alarm(
        "actions-disabled-with-extras",
        Namespace="AWS/SNS",
        MetricName="NumberOfNotificationsFailed",
        Statistic="Sum",
        Dimensions=dims(TopicName="orders"),
        Period=300,
        EvaluationPeriods=1,
        Threshold=0.0,
        ComparisonOperator=GT,
        ActionsEnabled=False,
        OKActions=[SNS],
        AlarmActions=[],
        EvaluateLowSampleCountPercentile="ignore",
    ),
    alarm(
        'Hostile "name" ${evil} %{if true} with\nnewline',
        Namespace="AWS/EC2",
        MetricName="CPUUtilization",
        Statistic="Average",
        Dimensions=dims(InstanceId="i-x' OR 1=1 --"),
        Period=60,
        EvaluationPeriods=1,
        Threshold=50.0,
        ComparisonOperator=GT,
        AlarmDescription='Hostile text: ${evil} "quotes" `ticks` | pipes',
    ),
    alarm(
        "dimension-with-slash-name",
        Namespace="AWS/EC2",
        MetricName="CPUUtilization",
        Statistic="Average",
        Dimensions=dims(**{"Bad/Dim": "x"}),
        Period=60,
        EvaluationPeriods=1,
        Threshold=50.0,
        ComparisonOperator=GT,
    ),
]

COMPOSITE_ALARMS: list[dict[str, Any]] = [
    {
        "AlarmName": "ec2-and-rds-both-bad",
        "AlarmArn": f"arn:aws:cloudwatch:{REGION}:{ACCOUNT}:alarm:ec2-and-rds-both-bad",
        "AlarmDescription": "Synthetic composite",
        "ActionsEnabled": True,
        "AlarmActions": [SNS],
        "AlarmRule": 'ALARM("ec2-cpu-high") AND ALARM("rds-cpu-high")',
        "StateValue": "OK",
    }
]


def main() -> None:
    payload = {"MetricAlarms": METRIC_ALARMS, "CompositeAlarms": COMPOSITE_ALARMS}
    OUT.write_text(json.dumps(payload, indent=2) + "\n")
    print(
        f"Wrote {len(METRIC_ALARMS)} metric and {len(COMPOSITE_ALARMS)} composite alarms to {OUT}"
    )


if __name__ == "__main__":
    main()
