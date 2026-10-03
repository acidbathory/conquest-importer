"""Generate synthetic CloudWatch dashboards in the shape of `aws cloudwatch get-dashboard`.

SYNTHETIC: written from the documented widget format, not exported from a real AWS account.
Replace or extend with real `aws cloudwatch get-dashboard --dashboard-name NAME` output when a
customer or test account is available (check it for sensitive values first).

Usage:  uv run python scripts/generate_cloudwatch_dashboard_fixtures.py
"""

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).parent.parent / "tests" / "fixtures" / "cloudwatch-dashboards"
ACCOUNT = "123456789012"
REGION = "us-east-1"


def metric_widget(
    x: int, y: int, title: str, metrics: list[Any], w: int = 12, h: int = 6, **props: Any
) -> dict[str, Any]:
    return {
        "type": "metric",
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "properties": {
            "metrics": metrics,
            "view": "timeSeries",
            "stacked": False,
            "region": REGION,
            "title": title,
            "period": 300,
            "stat": "Average",
            **props,
        },
    }


def dashboard(name: str, widgets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "DashboardName": name,
        "DashboardArn": f"arn:aws:cloudwatch::{ACCOUNT}:dashboard/{name}",
        "DashboardBody": json.dumps({"widgets": widgets}),
    }


DASHBOARDS = [
    dashboard(
        "conquest-ops-overview",
        [
            {
                "type": "text",
                "x": 0,
                "y": 0,
                "width": 24,
                "height": 2,
                "properties": {"markdown": "# Ops overview\nSynthetic sample dashboard."},
            },
            metric_widget(
                0,
                2,
                "EC2 CPU",
                [
                    ["AWS/EC2", "CPUUtilization", "InstanceId", "i-0abc1234567890def"],
                    ["...", "i-0fed0987654321cba"],
                ],
            ),
            metric_widget(
                12,
                2,
                "RDS CPU and connections",
                [
                    ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", "orders-db"],
                    [".", "DatabaseConnections", ".", ".", {"stat": "Maximum", "label": "conns"}],
                ],
            ),
            metric_widget(
                0,
                8,
                "Lambda errors",
                [["AWS/Lambda", "Errors", "FunctionName", "checkout", {"stat": "Sum"}]],
                stat="Sum",
                stacked=True,
            ),
            metric_widget(
                12,
                8,
                "DynamoDB read capacity",
                [["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", "conquest-test-table"]],
                view="singleValue",
                stat="Sum",
            ),
        ],
    ),
    dashboard(
        "conquest-hard-cases",
        [
            metric_widget(
                0,
                0,
                "Error rate (metric math)",
                [
                    [{"expression": "m1/m2*100", "label": "error %", "id": "e1"}],
                    [
                        "AWS/ApplicationELB",
                        "HTTPCode_Target_5XX_Count",
                        "LoadBalancer",
                        "app/x/1",
                        {"id": "m1", "visible": False},
                    ],
                    [".", "RequestCount", ".", ".", {"id": "m2", "visible": False}],
                ],
            ),
            metric_widget(
                12,
                0,
                "Latency p99 and average",
                [
                    [
                        "AWS/ApplicationELB",
                        "TargetResponseTime",
                        "LoadBalancer",
                        "app/x/1",
                        {"stat": "p99"},
                    ],
                    ["...", {"stat": "Average"}],
                    [
                        "AWS/ApplicationELB",
                        "TargetResponseTime",
                        "LoadBalancer",
                        "app/x/1",
                        {"stat": "Average"},
                    ],
                ],
            ),
            metric_widget(
                0,
                6,
                "Custom app metric",
                [["Custom/MyApp", "Orders", "Service", "checkout"]],
            ),
            {
                "type": "log",
                "x": 12,
                "y": 6,
                "width": 12,
                "height": 6,
                "properties": {
                    "query": "SOURCE '/aws/lambda/checkout' | fields @timestamp | limit 20",
                    "region": REGION,
                    "title": "Recent checkout logs",
                    "view": "table",
                },
            },
            {
                "type": "alarm",
                "x": 0,
                "y": 12,
                "width": 12,
                "height": 3,
                "properties": {
                    "title": "Alarm status",
                    "alarms": [f"arn:aws:cloudwatch:{REGION}:{ACCOUNT}:alarm:x"],
                },
            },
            metric_widget(
                12,
                12,
                "High-resolution and gauge",
                [
                    [
                        "AWS/EC2",
                        "CPUUtilization",
                        "InstanceId",
                        "i-0abc1234567890def",
                        {"period": 10},
                    ]
                ],
                view="gauge",
                yAxis={"left": {"min": 0, "max": 100}},
                annotations={"horizontal": [{"value": 80}]},
            ),
        ],
    ),
    dashboard(
        "conquest-empty",
        [],
    ),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for d in DASHBOARDS:
        (OUT / f"{d['DashboardName']}.json").write_text(json.dumps(d, indent=2) + "\n")
    print(f"wrote {len(DASHBOARDS)} dashboards to {OUT}")


if __name__ == "__main__":
    main()
