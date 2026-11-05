# Link a Google Cloud project to New Relic with Workload Identity Federation (no keys).
# Follows New Relic's "Automate with Terraform" guide for Google Cloud integrations.
# Costs: nothing to create. Google bills the Cloud Monitoring API reads that New Relic
# makes every 5 minutes to the linked project, so keep the service list short and
# `terraform destroy` when the test is over.

terraform {
  required_providers {
    google   = { source = "hashicorp/google" }
    newrelic = { source = "newrelic/newrelic" }
  }
}

variable "gcp_project_id" {
  type = string
}

variable "nr_account_id" {
  type = string
}

provider "google" {
  project = var.gcp_project_id
}

provider "newrelic" {
  account_id = var.nr_account_id
}

locals {
  apis = [
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudasset.googleapis.com",
    "serviceusage.googleapis.com",
    "monitoring.googleapis.com",
  ]
  roles = [
    "roles/viewer",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/cloudasset.viewer",
  ]
}

resource "google_project_service" "api" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

resource "google_service_account" "newrelic" {
  account_id   = "newrelic-gcp-integration"
  display_name = "New Relic GCP Integration"
  depends_on   = [google_project_service.api]
}

resource "google_project_iam_member" "newrelic" {
  for_each = toset(local.roles)
  project  = var.gcp_project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.newrelic.email}"
}

resource "google_iam_workload_identity_pool" "newrelic" {
  workload_identity_pool_id = "newrelic-pool"
  display_name              = "New Relic Pool"
  depends_on                = [google_project_service.api]
}

resource "google_iam_workload_identity_pool_provider" "newrelic" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.newrelic.workload_identity_pool_id
  workload_identity_pool_provider_id = "newrelic-provider"
  display_name                       = "New Relic OIDC Provider"

  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.nr_account_id" = "assertion.nr_account_id"
  }
  attribute_condition = "assertion.nr_account_id == '${var.nr_account_id}'"

  oidc {
    issuer_uri        = "https://oidc.newrelic.com/r/gcp-cmp" # US region
    allowed_audiences = ["newrelic-gcp-integrations"]
  }
}

resource "google_service_account_iam_member" "impersonate" {
  service_account_id = google_service_account.newrelic.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.newrelic.name}/attribute.nr_account_id/${var.nr_account_id}"
}

resource "newrelic_cloud_gcp_link_account" "this" {
  name                             = "conquest-test-${var.gcp_project_id}"
  project_id                       = var.gcp_project_id
  use_workload_identity_federation = true
  audience                         = "//iam.googleapis.com/${google_iam_workload_identity_pool_provider.newrelic.name}"
  service_account_email            = google_service_account.newrelic.email
  depends_on                       = [google_service_account_iam_member.impersonate, google_project_iam_member.newrelic]
}

# Only the services the test objects use, polled every 5 minutes.
resource "newrelic_cloud_gcp_dm_integrations" "this" {
  linked_account_id = newrelic_cloud_gcp_link_account.this.id

  virtual_machines {
    metrics_polling_interval = 300
  }
  pub_sub {
    metrics_polling_interval = 300
  }
  storage {
    metrics_polling_interval = 300
  }
}
