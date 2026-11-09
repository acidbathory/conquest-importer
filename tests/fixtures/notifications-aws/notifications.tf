terraform {
  required_providers {
    newrelic = {
      source = "newrelic/newrelic"
    }
  }
}

provider "newrelic" {}

resource "newrelic_notification_destination" "n_8bf04c329f_0" {
  name = "Imported: ops-alerts email 1"
  type = "EMAIL"

  property {
    key   = "email"
    value = "ops@example.com"
  }
}

resource "newrelic_notification_channel" "n_8bf04c329f_0" {
  name           = "Imported: ops-alerts email 1"
  type           = "EMAIL"
  destination_id = newrelic_notification_destination.n_8bf04c329f_0.id
  product        = "IINT"

  property {
    key   = "subject"
    value = "{{ issueTitle }}"
  }
}

resource "newrelic_notification_destination" "n_8bf04c329f_1" {
  name = "Imported: ops-alerts webhook 2"
  type = "WEBHOOK"

  property {
    key   = "url"
    value = "https://h.example.com/x"
  }
}

resource "newrelic_notification_channel" "n_8bf04c329f_1" {
  name           = "Imported: ops-alerts webhook 2"
  type           = "WEBHOOK"
  destination_id = newrelic_notification_destination.n_8bf04c329f_1.id
  product        = "IINT"

  property {
    key   = "payload"
    value = "{\n  \"issueId\": {{ json issueId }},\n  \"title\": {{ json annotations.title.[0] }},\n  \"state\": {{ json state }},\n  \"priority\": {{ json priority }}\n}"
  }
}

resource "newrelic_workflow" "n_8bf04c329f" {
  name                  = "Imported: ops-alerts"
  muting_rules_handling = "NOTIFY_ALL_ISSUES"
  enabled               = false

  issues_filter {
    name = "Imported: ops-alerts"
    type = "FILTER"

    predicate {
      attribute = "accumulations.conditionName"
      operator  = "EXACTLY_MATCHES"
      values    = ["Hostile \"name\" $${evil} %%{if true} withnewline", "alb-5xx-any", "alb-latency-p99", "custom-app-queue-depth", "dynamodb-throttled", "ec2-cpu-anomaly", "ec2-cpu-high", "ec2-network-in", "ec2-status-check-failed", "ecs-service-cpu", "lambda-duration-high", "lambda-error-rate", "lambda-errors", "rds-connections-3-of-5", "rds-cpu-high", "rds-free-storage-low", "sqs-oldest-message"]
    }
  }

  destination {
    channel_id = newrelic_notification_channel.n_8bf04c329f_0.id
  }

  destination {
    channel_id = newrelic_notification_channel.n_8bf04c329f_1.id
  }
}
