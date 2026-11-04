"""Generate Terraform that forwards CloudWatch log groups to New Relic.

A converted log-metric alarm counts `Log` events in New Relic, so it returns nothing until the
log group is forwarded. This creates what New Relic's documentation describes: a Firehose delivery
stream to the New Relic logs endpoint (GZIP, 1 MiB buffer), a role CloudWatch Logs uses to write to
it, and one subscription filter per log group. Nothing is deployed by the importer.

Security notes match the metric setup: the license key is a sensitive variable that is never written
to a file (Terraform state will contain it), the failed-delivery bucket is private and encrypted,
and every log group name is validated and placed in HCL through hcl_string.
"""

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, field_validator

from importer.core.emit.terraform import hcl_string
from importer.sources.cloudwatch.logfilter import LOG_GROUP_ATTRIBUTE

# New Relic documentation: "Stream logs using Kinesis Data Firehose".
ENDPOINTS = {
    "us": "https://aws-api.newrelic.com/firehose/v1",
    "eu": "https://aws-api.eu.newrelic.com/firehose/v1",
}
MAX_GROUPS = 50
_PREFIX = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
_REGION = re.compile(r"^[a-z]{2}(-[a-z]+)+-[0-9]$")
_LOG_GROUP = re.compile(r"^[A-Za-z0-9_.\-/#]{1,512}$")


class LogIngestConfig(BaseModel):
    name_prefix: str = "newrelic-logs"
    aws_region: str = "us-east-1"
    nr_region: Literal["us", "eu"] = "us"
    log_groups: list[str]

    @field_validator("name_prefix")
    @classmethod
    def _check_prefix(cls, v: str) -> str:
        if not _PREFIX.match(v):
            raise ValueError("name_prefix must be 1-24 chars: lowercase letters, digits, hyphens")
        return v

    @field_validator("aws_region")
    @classmethod
    def _check_region(cls, v: str) -> str:
        if not _REGION.match(v):
            raise ValueError("aws_region must look like us-east-1")
        return v

    @field_validator("log_groups")
    @classmethod
    def _check_groups(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("at least one log group is required")
        if len(v) > MAX_GROUPS:
            raise ValueError(f"at most {MAX_GROUPS} log groups")
        for group in v:
            if not _LOG_GROUP.match(group):
                raise ValueError(f"invalid log group name {group[:40]!r}")
        return sorted(set(v))


def log_groups_from_ir(ir_dir: Path) -> list[str]:
    """Log groups behind the converted log-metric conditions in a `convert` output folder."""
    groups: set[str] = set()
    for path in sorted(ir_dir.glob("*.json")):
        data = json.loads(path.read_text())
        signal = data.get("signal", {})
        if data.get("confidence") not in ("exact", "approximate") or signal.get("kind") != "log":
            continue
        for f in (signal.get("count") or {}).get("filters", []):
            if f.get("key") == LOG_GROUP_ATTRIBUTE and f.get("value"):
                groups.add(str(f["value"]))
    return sorted(groups)


VARIABLES = """variable "newrelic_license_key" {
  description = "New Relic ingest (license) key used by Firehose. Pass with TF_VAR_newrelic_license_key; never commit it."
  type        = string
  sensitive   = true
}

variable "aws_region" {
  type    = string
  default = @@REGION@@
}

variable "name_prefix" {
  type    = string
  default = @@PREFIX@@
}

variable "s3_force_destroy" {
  description = "Allow terraform destroy to delete the backup bucket even if it holds failed-delivery objects."
  type        = bool
  default     = false
}
"""

MAIN = """terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

locals {
  endpoint   = @@ENDPOINT@@
  log_groups = [@@GROUPS@@]
}

# ---- backup bucket for failed deliveries -------------------------------------------------

resource "aws_s3_bucket" "backup" {
  bucket        = "${var.name_prefix}-backup-${data.aws_caller_identity.current.account_id}-${var.aws_region}"
  force_destroy = var.s3_force_destroy
}

resource "aws_s3_bucket_public_access_block" "backup" {
  bucket                  = aws_s3_bucket.backup.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backup" {
  bucket = aws_s3_bucket.backup.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# ---- Firehose -> New Relic logs ------------------------------------------------------------

data "aws_iam_policy_document" "firehose_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["firehose.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "firehose" {
  name               = "${var.name_prefix}-firehose"
  assume_role_policy = data.aws_iam_policy_document.firehose_trust.json
}

data "aws_iam_policy_document" "firehose_backup" {
  statement {
    actions = [
      "s3:AbortMultipartUpload",
      "s3:GetBucketLocation",
      "s3:GetObject",
      "s3:ListBucket",
      "s3:ListBucketMultipartUploads",
      "s3:PutObject",
    ]
    resources = [aws_s3_bucket.backup.arn, "${aws_s3_bucket.backup.arn}/*"]
  }
}

resource "aws_iam_role_policy" "firehose_backup" {
  name   = "backup-bucket"
  role   = aws_iam_role.firehose.id
  policy = data.aws_iam_policy_document.firehose_backup.json
}

resource "aws_kinesis_firehose_delivery_stream" "newrelic_logs" {
  name        = "${var.name_prefix}-stream"
  destination = "http_endpoint"

  http_endpoint_configuration {
    url                = local.endpoint
    name               = "New Relic logs"
    access_key         = var.newrelic_license_key
    buffering_size     = 1
    buffering_interval = 60
    role_arn           = aws_iam_role.firehose.arn
    s3_backup_mode     = "FailedDataOnly"

    s3_configuration {
      role_arn           = aws_iam_role.firehose.arn
      bucket_arn         = aws_s3_bucket.backup.arn
      buffering_size     = 10
      buffering_interval = 400
      compression_format = "GZIP"
    }

    request_configuration {
      content_encoding = "GZIP"
    }
  }

  depends_on = [aws_iam_role_policy.firehose_backup]
}

# ---- CloudWatch Logs -> Firehose --------------------------------------------------------------

data "aws_iam_policy_document" "logs_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["logs.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "logs_to_firehose" {
  name               = "${var.name_prefix}-logs"
  assume_role_policy = data.aws_iam_policy_document.logs_trust.json
}

data "aws_iam_policy_document" "logs_to_firehose" {
  statement {
    actions   = ["firehose:PutRecord", "firehose:PutRecordBatch"]
    resources = [aws_kinesis_firehose_delivery_stream.newrelic_logs.arn]
  }
}

resource "aws_iam_role_policy" "logs_to_firehose" {
  name   = "put-to-firehose"
  role   = aws_iam_role.logs_to_firehose.id
  policy = data.aws_iam_policy_document.logs_to_firehose.json
}

# One filter per log group. AWS allows two subscription filters per log group, so a group that
# already has two cannot be added until one is removed.
resource "aws_cloudwatch_log_subscription_filter" "newrelic" {
  for_each        = toset(local.log_groups)
  name            = "${var.name_prefix}-forward"
  log_group_name  = each.value
  filter_pattern  = ""
  destination_arn = aws_kinesis_firehose_delivery_stream.newrelic_logs.arn
  role_arn        = aws_iam_role.logs_to_firehose.arn

  depends_on = [aws_iam_role_policy.logs_to_firehose]
}
"""

OUTPUTS = """output "delivery_stream_arn" {
  value = aws_kinesis_firehose_delivery_stream.newrelic_logs.arn
}

output "forwarded_log_groups" {
  value = local.log_groups
}
"""


def _fill(template: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        template = template.replace(f"@@{key}@@", value)
    return template


def log_ingest_files(config: LogIngestConfig) -> dict[str, str]:
    values = {
        "REGION": hcl_string(config.aws_region),
        "PREFIX": hcl_string(config.name_prefix),
        "ENDPOINT": hcl_string(ENDPOINTS[config.nr_region]),
        "GROUPS": ", ".join(hcl_string(g) for g in config.log_groups),
    }
    return {
        "main.tf": _fill(MAIN, values),
        "variables.tf": _fill(VARIABLES, values),
        "outputs.tf": OUTPUTS,
    }
