# Live test stack #2: common services, to verify New Relic metric naming per service and to give the
# end-to-end `migrate` run realistic alarms. Everything is new, prefixed and tagged.
# Remove with:  terraform destroy      (state is local and git-ignored)

terraform {
  required_providers {
    aws = { source = "hashicorp/aws" }
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
  p = "conquest-svc"
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

data "aws_ssm_parameter" "ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# --- messaging ---------------------------------------------------------------------------------

resource "aws_sns_topic" "t" {
  name = "${local.p}-topic"
}

resource "aws_sqs_queue" "q" {
  name = "${local.p}-queue"
}

resource "aws_sqs_queue_policy" "q" {
  queue_url = aws_sqs_queue.q.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sns.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.q.arn
      Condition = { ArnEquals = { "aws:SourceArn" = aws_sns_topic.t.arn } }
    }]
  })
}

# An SQS subscription: has no New Relic notification equivalent, so it exercises the "manual" path
# without sending anything to an outside address.
resource "aws_sns_topic_subscription" "to_queue" {
  topic_arn            = aws_sns_topic.t.arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.q.arn
  raw_message_delivery = true
  depends_on           = [aws_sqs_queue_policy.q]
}

resource "aws_kinesis_stream" "k" {
  name             = "${local.p}-stream"
  shard_count      = 1
  retention_period = 24
}

# --- compute and network -----------------------------------------------------------------------

resource "aws_instance" "i" {
  ami           = data.aws_ssm_parameter.ami.value
  instance_type = "t3.micro"
  subnet_id     = sort(data.aws_subnets.default.ids)[0]
  metadata_options {
    http_tokens = "required"
  }
  tags = { Name = "${local.p}-instance" }
}

resource "aws_security_group" "alb" {
  name        = "${local.p}-alb"
  description = "Live test ALB: serves a fixed ok response, no targets"
  vpc_id      = data.aws_vpc.default.id
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_lb" "alb" {
  name               = "${local.p}-alb"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = slice(sort(data.aws_subnets.default.ids), 0, 2)
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.alb.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "ok"
      status_code  = "200"
    }
  }
}

# --- API Gateway (REST, mock integration) ------------------------------------------------------

resource "aws_api_gateway_rest_api" "api" {
  name = "${local.p}-api"
}

resource "aws_api_gateway_method" "get" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = aws_api_gateway_rest_api.api.root_resource_id
  http_method   = "GET"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "mock" {
  rest_api_id       = aws_api_gateway_rest_api.api.id
  resource_id       = aws_api_gateway_rest_api.api.root_resource_id
  http_method       = aws_api_gateway_method.get.http_method
  type              = "MOCK"
  request_templates = { "application/json" = jsonencode({ statusCode = 200 }) }
}

resource "aws_api_gateway_method_response" "ok" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_rest_api.api.root_resource_id
  http_method = aws_api_gateway_method.get.http_method
  status_code = "200"
}

resource "aws_api_gateway_integration_response" "ok" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_rest_api.api.root_resource_id
  http_method = aws_api_gateway_method.get.http_method
  status_code = aws_api_gateway_method_response.ok.status_code
  depends_on  = [aws_api_gateway_integration.mock]
}

resource "aws_api_gateway_deployment" "d" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  triggers    = { redeploy = sha1(jsonencode([aws_api_gateway_integration.mock.id, aws_api_gateway_integration_response.ok.id])) }
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "test" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  deployment_id = aws_api_gateway_deployment.d.id
  stage_name    = "test"
}

# --- alarms (all notify the SNS topic) ---------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "ec2_cpu" {
  alarm_name          = "${local.p}-ec2-cpu-high"
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  dimensions          = { InstanceId = aws_instance.i.id }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 1
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [aws_sns_topic.t.arn]
}

resource "aws_cloudwatch_metric_alarm" "sqs_depth" {
  alarm_name          = "${local.p}-sqs-depth-high"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.q.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 100
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [aws_sns_topic.t.arn]
}

resource "aws_cloudwatch_metric_alarm" "sns_published" {
  alarm_name          = "${local.p}-sns-published-high"
  namespace           = "AWS/SNS"
  metric_name         = "NumberOfMessagesPublished"
  dimensions          = { TopicName = aws_sns_topic.t.name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 100
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [aws_sns_topic.t.arn]
}

resource "aws_cloudwatch_metric_alarm" "kinesis_in" {
  alarm_name          = "${local.p}-kinesis-incoming-high"
  namespace           = "AWS/Kinesis"
  metric_name         = "IncomingRecords"
  dimensions          = { StreamName = aws_kinesis_stream.k.name }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1000
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [aws_sns_topic.t.arn]
}

resource "aws_cloudwatch_metric_alarm" "apigw_count" {
  alarm_name          = "${local.p}-apigw-count-high"
  namespace           = "AWS/ApiGateway"
  metric_name         = "Count"
  dimensions          = { ApiName = aws_api_gateway_rest_api.api.name }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 100
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [aws_sns_topic.t.arn]
}

resource "aws_cloudwatch_metric_alarm" "alb_requests" {
  alarm_name          = "${local.p}-alb-requests-high"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "RequestCount"
  dimensions          = { LoadBalancer = aws_lb.alb.arn_suffix }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 100
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [aws_sns_topic.t.arn]
}

output "alb_dns" { value = aws_lb.alb.dns_name }
output "apigw_url" { value = "https://${aws_api_gateway_rest_api.api.id}.execute-api.${var.region}.amazonaws.com/${aws_api_gateway_stage.test.stage_name}/" }
output "queue_url" { value = aws_sqs_queue.q.url }
output "topic_arn" { value = aws_sns_topic.t.arn }
output "stream_name" { value = aws_kinesis_stream.k.name }
output "instance_id" { value = aws_instance.i.id }
