# Small Google Cloud resources that emit metrics, to check New Relic's metric names against.
# COST: one e2-micro VM (about $0.01 per hour) and a 10 GB standard disk (about $0.40 per month);
# Pub/Sub and a tiny bucket cost effectively nothing at this volume. Destroy when done:
#   terraform destroy
# Everything is prefixed ckg- and labelled so it is easy to find.

terraform {
  required_version = ">= 1.5"

  required_providers {
    google = { source = "hashicorp/google", version = "~> 8.10" }
  }
}

variable "gcp_project_id" {
  type = string
}

variable "zone" {
  type    = string
  default = "us-central1-a"
}

provider "google" {
  project = var.gcp_project_id
  region  = "us-central1"
  zone    = var.zone
}

resource "google_compute_instance" "vm" {
  name         = "ckg-vm"
  machine_type = "e2-micro"
  labels       = { purpose = "conquest-importer-test" }

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 10
      type  = "pd-standard"
    }
  }

  network_interface {
    network = "default"
  }
}

resource "google_pubsub_topic" "topic" {
  name   = "ckg-topic"
  labels = { purpose = "conquest-importer-test" }
}

resource "google_pubsub_subscription" "sub" {
  name                 = "ckg-sub"
  topic                = google_pubsub_topic.topic.id
  ack_deadline_seconds = 20
  labels               = { purpose = "conquest-importer-test" }
}

resource "google_storage_bucket" "bucket" {
  name                        = "ckg-${var.gcp_project_id}"
  location                    = "US-CENTRAL1"
  force_destroy               = true
  uniform_bucket_level_access = true
  labels                      = { purpose = "conquest-importer-test" }
}
