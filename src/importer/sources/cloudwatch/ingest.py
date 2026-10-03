"""Generate Terraform that streams CloudWatch metrics into New Relic (AWS Metric Streams).

Creates: an S3 bucket for failed deliveries, a Firehose delivery stream to New Relic, a
CloudWatch metric stream limited to the chosen namespaces, their IAM roles, and a read-only
IAM role that New Relic assumes (with an external id) to link the AWS account.

Security notes:
- The New Relic license key is a sensitive variable with no default and is never written to a
  file. Terraform state WILL contain it, so use a protected backend for real deployments.
- The S3 bucket blocks public access and is encrypted.
- Every user-supplied value is validated here and placed in HCL through hcl_string.
"""

import re
from typing import Literal

from pydantic import BaseModel, field_validator

from importer.core.emit.terraform import hcl_string

ENDPOINTS = {
    "us": "https://aws-api.newrelic.com/cloudwatch-metrics/v1",
    "eu": "https://aws-api.eu01.nr-data.net/cloudwatch-metrics/v1",
}
# AWS account that New Relic's integration assumes a role from (New Relic documentation).
NEW_RELIC_AWS_ACCOUNT = "754728514883"

_PREFIX = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
_REGION = re.compile(r"^[a-z]{2}(-[a-z]+)+-[0-9]$")
_NAMESPACE = re.compile(r"^[A-Za-z0-9_.\-/#:]{1,255}$")


class IngestConfig(BaseModel):
    name_prefix: str = "newrelic-metrics"
    aws_region: str = "us-east-1"
    nr_region: Literal["us", "eu"] = "us"
    namespaces: list[str]

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

    @field_validator("namespaces")
    @classmethod
    def _check_namespaces(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("at least one namespace is required (streaming everything is costly)")
        if len(v) > 50:
            raise ValueError("at most 50 namespaces")
        for ns in v:
            if not _NAMESPACE.match(ns):
                raise ValueError(f"invalid namespace {ns[:40]!r}")
        return sorted(set(v))


VARIABLES = """variable "newrelic_license_key" {
  description = "New Relic ingest (license) key used by Firehose. Pass with TF_VAR_newrelic_license_key; never commit it."
  type        = string
  sensitive   = true
}

variable "newrelic_api_key" {
  description = "New Relic USER API key (NRAK-...) used to link the account. Pass with TF_VAR_newrelic_api_key."
  type        = string
  sensitive   = true
}

variable "newrelic_account_id" {
  description = "New Relic account id to link this AWS account to."
  type        = string
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
    newrelic = {
      source = "newrelic/newrelic"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

provider "newrelic" {
  account_id = var.newrelic_account_id
  api_key    = var.newrelic_api_key
  region     = @@NR_REGION@@
}

data "aws_caller_identity" "current" {}

locals {
  endpoint = @@ENDPOINT@@
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

# ---- Firehose -> New Relic ----------------------------------------------------------------

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

resource "aws_kinesis_firehose_delivery_stream" "newrelic" {
  name        = "${var.name_prefix}-stream"
  destination = "http_endpoint"

  http_endpoint_configuration {
    url                = local.endpoint
    name               = "New Relic"
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

# ---- CloudWatch metric stream ----------------------------------------------------------------

data "aws_iam_policy_document" "stream_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["streams.metrics.cloudwatch.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "metric_stream" {
  name               = "${var.name_prefix}-metric-stream"
  assume_role_policy = data.aws_iam_policy_document.stream_trust.json
}

data "aws_iam_policy_document" "stream_to_firehose" {
  statement {
    actions   = ["firehose:PutRecord", "firehose:PutRecordBatch"]
    resources = [aws_kinesis_firehose_delivery_stream.newrelic.arn]
  }
}

resource "aws_iam_role_policy" "metric_stream" {
  name   = "put-to-firehose"
  role   = aws_iam_role.metric_stream.id
  policy = data.aws_iam_policy_document.stream_to_firehose.json
}

resource "aws_cloudwatch_metric_stream" "newrelic" {
  name          = "${var.name_prefix}-stream"
  role_arn      = aws_iam_role.metric_stream.arn
  firehose_arn  = aws_kinesis_firehose_delivery_stream.newrelic.arn
  output_format = "opentelemetry0.7"
@@FILTERS@@
  depends_on = [aws_iam_role_policy.metric_stream]
}

# ---- link the AWS account in New Relic --------------------------------------------------------

data "aws_iam_policy_document" "newrelic_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::@@NR_AWS_ACCOUNT@@:root"]
    }
    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [var.newrelic_account_id]
    }
  }
}

resource "aws_iam_role" "newrelic_link" {
  name               = "${var.name_prefix}-newrelic-link"
  assume_role_policy = data.aws_iam_policy_document.newrelic_trust.json
}

# Broad read access is what New Relic documents for tagging and entity relationships. Narrow it
# if your security policy requires.
resource "aws_iam_role_policy_attachment" "newrelic_link" {
  role       = aws_iam_role.newrelic_link.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

resource "newrelic_cloud_aws_link_account" "this" {
  arn                    = aws_iam_role.newrelic_link.arn
  metric_collection_mode = "PUSH"
  name                   = "${var.name_prefix}-${data.aws_caller_identity.current.account_id}"

  depends_on = [aws_iam_role_policy_attachment.newrelic_link]
}
"""

OUTPUTS = """output "metric_stream_arn" {
  value = aws_cloudwatch_metric_stream.newrelic.arn
}

output "newrelic_link_role_arn" {
  value = aws_iam_role.newrelic_link.arn
}

output "backup_bucket" {
  value = aws_s3_bucket.backup.bucket
}
"""


def _fill(template: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        template = template.replace(f"@@{key}@@", value)
    return template


def ingest_files(config: IngestConfig) -> dict[str, str]:
    filters = "".join(
        f"\n  include_filter {{\n    namespace = {hcl_string(ns)}\n  }}\n"
        for ns in config.namespaces
    )
    values = {
        "REGION": hcl_string(config.aws_region),
        "PREFIX": hcl_string(config.name_prefix),
        "NR_REGION": hcl_string(config.nr_region.upper()),
        "ENDPOINT": hcl_string(ENDPOINTS[config.nr_region]),
        "FILTERS": filters,
        "NR_AWS_ACCOUNT": NEW_RELIC_AWS_ACCOUNT,
    }
    return {
        "main.tf": _fill(MAIN, values),
        "variables.tf": _fill(VARIABLES, values),
        "outputs.tf": OUTPUTS,
    }
