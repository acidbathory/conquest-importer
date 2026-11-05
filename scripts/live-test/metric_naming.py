"""Helpers for checking New Relic metric names against real AWS services.

Run after `terraform apply` in scripts/live-test/metric-naming. Everything is prefixed ckv-.

    uv run python scripts/live-test/metric_naming.py alarms   # one alarm per metric to compare
    uv run python scripts/live-test/metric_naming.py traffic  # requests to the load balancers/S3
    uv run python scripts/live-test/metric_naming.py cleanup  # delete the ckv- alarms

COST: the stack costs roughly $0.15 per hour while it exists. Destroy it when done.
"""

import contextlib
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import boto3

STACK = Path(__file__).parent / "metric-naming"
PREFIX = "ckv-"
TAG = {"Key": "owner", "Value": "conquest-importer-verify"}
REGION = "us-east-1"


def outputs() -> dict[str, str]:
    done = subprocess.run(
        ["terraform", "output", "-json", "dimensions"],  # noqa: S607
        cwd=STACK,
        capture_output=True,
        text=True,
        check=True,
    )
    data: dict[str, str] = json.loads(done.stdout)
    return data


def alarm_specs(o: dict[str, str]) -> list[tuple[str, str, str, dict[str, str], str]]:
    """(name, namespace, metric, dimensions, statistic)."""
    rds = {"DBInstanceIdentifier": o["db_instance"]}
    cache = {"CacheClusterId": o["cache_cluster"]}
    ecs = {"ClusterName": o["ecs_cluster"], "ServiceName": o["ecs_service"]}
    vol = {"VolumeId": o["volume_root"]}
    nlb = {"LoadBalancer": o["nlb_dimension"]}
    clb = {"LoadBalancerName": o["classic_name"]}
    s3 = {"BucketName": o["bucket"], "FilterId": "EntireBucket"}
    return [
        ("rds-cpu", "AWS/RDS", "CPUUtilization", rds, "Average"),
        ("rds-memory", "AWS/RDS", "FreeableMemory", rds, "Average"),
        ("rds-connections", "AWS/RDS", "DatabaseConnections", rds, "Maximum"),
        ("rds-storage", "AWS/RDS", "FreeStorageSpace", rds, "Minimum"),
        ("cache-cpu", "AWS/ElastiCache", "CPUUtilization", cache, "Average"),
        ("cache-connections", "AWS/ElastiCache", "CurrConnections", cache, "Maximum"),
        ("cache-memory", "AWS/ElastiCache", "FreeableMemory", cache, "Average"),
        ("ecs-cpu", "AWS/ECS", "CPUUtilization", ecs, "Average"),
        ("ecs-memory", "AWS/ECS", "MemoryUtilization", ecs, "Average"),
        ("ebs-read-ops", "AWS/EBS", "VolumeReadOps", vol, "Sum"),
        ("ebs-write-ops", "AWS/EBS", "VolumeWriteOps", vol, "Sum"),
        ("ebs-idle", "AWS/EBS", "VolumeIdleTime", {"VolumeId": o["volume_extra"]}, "Sum"),
        ("nlb-flows", "AWS/NetworkELB", "ActiveFlowCount", nlb, "Average"),
        ("nlb-new-flows", "AWS/NetworkELB", "NewFlowCount", nlb, "Sum"),
        ("nlb-bytes", "AWS/NetworkELB", "ProcessedBytes", nlb, "Sum"),
        ("clb-requests", "AWS/ELB", "RequestCount", clb, "Sum"),
        ("clb-5xx", "AWS/ELB", "HTTPCode_ELB_5XX", clb, "Sum"),
        ("clb-latency", "AWS/ELB", "Latency", clb, "Average"),
        ("s3-requests", "AWS/S3", "AllRequests", s3, "Sum"),
        ("s3-gets", "AWS/S3", "GetRequests", s3, "Sum"),
    ]


def create_alarms() -> None:
    cw = boto3.client("cloudwatch", region_name=REGION)
    for name, ns, metric, dims, stat in alarm_specs(outputs()):
        cw.put_metric_alarm(
            AlarmName=PREFIX + name,
            Namespace=ns,
            MetricName=metric,
            Dimensions=[{"Name": k, "Value": v} for k, v in dims.items()],
            Statistic=stat,
            Period=300,
            EvaluationPeriods=1,
            Threshold=1.0,
            ComparisonOperator="GreaterThanThreshold",
            TreatMissingData="notBreaching",
            Tags=[TAG],
        )
    print(f"created {len(alarm_specs(outputs()))} alarms")


def traffic(rounds: int = 30) -> None:
    o = outputs()
    s3 = boto3.client("s3", region_name=REGION)
    key = "probe.txt"
    s3.put_object(Bucket=o["bucket"], Key=key, Body=b"hello")
    for _ in range(rounds):
        # A 503 from the classic ELB and a refused NLB flow are expected (nothing serves port
        # 80), and both still count as traffic.
        with contextlib.suppress(urllib.error.URLError, OSError):
            urllib.request.urlopen(f"http://{o['classic_dns']}/", timeout=5)
        with (
            contextlib.suppress(OSError),
            socket.create_connection((o["nlb_dns"], 80), timeout=3) as conn,
        ):
            conn.sendall(b"GET / HTTP/1.0\r\n\r\n")
        s3.get_object(Bucket=o["bucket"], Key=key)["Body"].read()
        time.sleep(1)
    print(f"sent {rounds} rounds of traffic")


def cleanup_alarms() -> None:
    cw = boto3.client("cloudwatch", region_name=REGION)
    names = [
        a["AlarmName"]
        for p in cw.get_paginator("describe_alarms").paginate(AlarmNamePrefix=PREFIX)
        for a in p["MetricAlarms"]
    ]
    for i in range(0, len(names), 100):
        cw.delete_alarms(AlarmNames=names[i : i + 100])
    print(f"deleted {len(names)} alarms")


if __name__ == "__main__":
    commands = {"alarms": create_alarms, "traffic": traffic, "cleanup": cleanup_alarms}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    commands[sys.argv[1]]()
