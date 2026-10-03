"""Tiny test function: writes an item to DynamoDB; fails or sleeps on request."""

import os
import time
import uuid

import boto3

table = boto3.resource("dynamodb").Table(os.environ["TABLE"])


def handler(event, context):
    if event.get("sleep_ms"):
        time.sleep(event["sleep_ms"] / 1000)
    if event.get("fail"):
        raise RuntimeError("requested failure (conquest live test)")
    table.put_item(Item={"id": str(uuid.uuid4()), "note": "conquest live test"})
    return {"ok": True}
