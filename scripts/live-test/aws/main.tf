# Live test resources for Conquest Importer. Everything is new, prefixed and tagged.
# Remove with:  terraform destroy      (state is local and git-ignored)

terraform {
  required_providers {
    aws     = { source = "hashicorp/aws" }
    archive = { source = "hashicorp/archive" }
  }
}

variable "region" {
  default = "us-east-1"
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "conquest-importer-live-test" }
  }
}

locals {
  p          = "conquest-live"
  namespace  = "Custom/ConquestLive"
}

# --- data sources of metrics and traces -----------------------------------------------------

resource "aws_dynamodb_table" "t" {
  name         = "${local.p}-table"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"
  attribute {
    name = "id"
    type = "S"
  }
}

resource "aws_iam_role" "fn" {
  name = "${local.p}-fn-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.fn.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "xray" {
  role       = aws_iam_role.fn.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

resource "aws_iam_role_policy" "ddb" {
  name = "${local.p}-ddb-put"
  role = aws_iam_role.fn.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:PutItem"]
      Resource = aws_dynamodb_table.t.arn
    }]
  })
}

data "archive_file" "fn" {
  type        = "zip"
  source_file = "${path.module}/handler.py"
  output_path = "${path.module}/handler.zip"
}

resource "aws_cloudwatch_log_group" "fn" {
  name              = "/aws/lambda/${local.p}-fn"
  retention_in_days = 7
}

resource "aws_lambda_function" "fn" {
  function_name    = "${local.p}-fn"
  role             = aws_iam_role.fn.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.fn.output_path
  source_code_hash = data.archive_file.fn.output_base64sha256
  timeout          = 10
  environment {
    variables = { TABLE = aws_dynamodb_table.t.name }
  }
  tracing_config {
    mode = "Active"
  }
  depends_on = [aws_cloudwatch_log_group.fn, aws_iam_role_policy_attachment.logs]
}

# --- alarms ------------------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "ddb_writes" {
  alarm_name          = "${local.p}-ddb-write-capacity-high"
  alarm_description   = "Live test: DynamoDB write capacity"
  namespace           = "AWS/DynamoDB"
  metric_name         = "ConsumedWriteCapacityUnits"
  dimensions          = { TableName = aws_dynamodb_table.t.name }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "fn_errors" {
  alarm_name          = "${local.p}-lambda-errors"
  alarm_description   = "Live test: Lambda errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.fn.function_name }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "fn_p99" {
  alarm_name          = "${local.p}-lambda-duration-p99"
  alarm_description   = "Live test: percentile statistic (expected: not converted)"
  namespace           = "AWS/Lambda"
  metric_name         = "Duration"
  dimensions          = { FunctionName = aws_lambda_function.fn.function_name }
  extended_statistic  = "p99"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1000
  comparison_operator = "GreaterThanThreshold"
}

resource "aws_cloudwatch_metric_alarm" "fn_error_rate" {
  alarm_name          = "${local.p}-lambda-error-rate"
  alarm_description   = "Live test: metric math"
  evaluation_periods  = 1
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  metric_query {
    id          = "rate"
    expression  = "errors / invocations * 100"
    label       = "Error rate %"
    return_data = true
  }
  metric_query {
    id = "errors"
    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Errors"
      dimensions  = { FunctionName = aws_lambda_function.fn.function_name }
      period      = 60
      stat        = "Sum"
    }
  }
  metric_query {
    id = "invocations"
    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Invocations"
      dimensions  = { FunctionName = aws_lambda_function.fn.function_name }
      period      = 60
      stat        = "Sum"
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "custom_orders" {
  alarm_name          = "${local.p}-custom-orders-low"
  alarm_description   = "Live test: custom namespace (expected: no mapping)"
  namespace           = local.namespace
  metric_name         = "Orders"
  dimensions          = { Service = "checkout" }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "fn_duration_3of5" {
  alarm_name          = "${local.p}-lambda-duration-3of5"
  alarm_description   = "Live test: M of N datapoints"
  namespace           = "AWS/Lambda"
  metric_name         = "Duration"
  dimensions          = { FunctionName = aws_lambda_function.fn.function_name }
  statistic           = "Average"
  period              = 60
  evaluation_periods  = 5
  datapoints_to_alarm = 3
  threshold           = 500
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "breaching"
}

resource "aws_cloudwatch_composite_alarm" "either" {
  alarm_name        = "${local.p}-composite-ddb-or-errors"
  alarm_description = "Live test: composite (expected: unsupported)"
  alarm_rule        = "ALARM(\"${aws_cloudwatch_metric_alarm.ddb_writes.alarm_name}\") OR ALARM(\"${aws_cloudwatch_metric_alarm.fn_errors.alarm_name}\")"
}

# --- dashboard ---------------------------------------------------------------------------------

resource "aws_cloudwatch_dashboard" "d" {
  dashboard_name = "${local.p}-overview"
  dashboard_body = jsonencode({
    widgets = [
      { type = "text", x = 0, y = 0, width = 24, height = 2,
      properties = { markdown = "# Conquest live test\nCreated for the importer. Safe to delete." } },
      { type = "metric", x = 0, y = 2, width = 12, height = 6,
        properties = {
          title = "DynamoDB writes", region = var.region, view = "timeSeries", stat = "Sum", period = 60,
          metrics = [["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", aws_dynamodb_table.t.name]]
      } },
      { type = "metric", x = 12, y = 2, width = 12, height = 6,
        properties = {
          title = "Lambda invocations and errors", region = var.region, view = "timeSeries", stat = "Sum", period = 60,
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.fn.function_name],
            [".", "Errors", ".", "."]
          ]
      } },
      { type = "metric", x = 0, y = 8, width = 8, height = 6,
        properties = {
          title = "Lambda duration p99 and average", region = var.region, view = "timeSeries", period = 60,
          metrics = [
            ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.fn.function_name, { stat = "p99" }],
            ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.fn.function_name, { stat = "Average" }]
          ]
      } },
      { type = "metric", x = 8, y = 8, width = 8, height = 6,
        properties = {
          title = "Custom orders", region = var.region, view = "singleValue", stat = "Sum", period = 60,
          metrics = [[local.namespace, "Orders", "Service", "checkout"]]
      } },
      { type = "log", x = 16, y = 8, width = 8, height = 6,
        properties = {
          title = "Function logs", region = var.region, view = "table",
          query = "SOURCE '${aws_cloudwatch_log_group.fn.name}' | fields @timestamp, @message | limit 20"
      } },
    ]
  })
}

output "function" { value = aws_lambda_function.fn.function_name }
output "table" { value = aws_dynamodb_table.t.name }
