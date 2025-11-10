terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.38"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ----------------------------
# Enable required APIs
# ----------------------------
locals {
  required_services = [
    "cloudfunctions.googleapis.com",
    "run.googleapis.com",
    "eventarc.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "logging.googleapis.com",
    "secretmanager.googleapis.com",
    "pubsub.googleapis.com",
    "cloudscheduler.googleapis.com",
    "iamcredentials.googleapis.com"
  ]
}

resource "google_project_service" "services" {
  for_each           = toset(local.required_services)
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# ----------------------------
# "Admin" Service Account with DWD
# ----------------------------
module "admin_sa" {
  source="./admin_sa"
}

# ----------------------------
# Secret Manager (DWD service account key JSON)
# ----------------------------
resource "google_secret_manager_secret" "dwd_sa_key" {
  secret_id  = "workspace-dwd-sa-key"
  replication { 
    auto {}
  }
}

resource "google_secret_manager_secret_version" "dwd_sa_key_v" {
  secret      = google_secret_manager_secret.dwd_sa_key.id
  secret_data = module.admin_sa.service_account_key_json
}

# ----------------------------
# Runtime service account
# ----------------------------
resource "google_service_account" "fn" {
  account_id   = "autogen-posix-fn"
  display_name = "Cloud Function SA for posixAccounts populator"
}

# Allow function SA to access the Secret Manager secret
resource "google_project_iam_member" "fn_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.fn.email}"
}

# Allow function SA to invoke runs
resource "google_project_iam_member" "fn_secret_accessor" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.fn.email}"
}

# ----------------------------
# Pub/Sub topic for scheduled trigger
# ----------------------------
resource "google_pubsub_topic" "topic" {
  name = "autogen-posix-trigger"
}

# Let Cloud Scheduler publish to the topic
resource "google_service_account" "scheduler" {
  account_id   = "autogen-posix-scheduler"
  display_name = "Scheduler SA for posix populator"
}

resource "google_pubsub_topic_iam_member" "allow_scheduler_publish" {
  topic  = google_pubsub_topic.topic.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.scheduler.email}"
}

# Cloud Scheduler job → Pub/Sub
resource "google_cloud_scheduler_job" "job" {
  name        = "autogen-posix-cron"
  schedule    = var.cron_schedule
  time_zone   = "Etc/UTC"

  pubsub_target {
    topic_name = google_pubsub_topic.topic.id
    data       = base64encode("{}")
    attributes = {
      "source" = "terraform"
    }
  }

  attempt_deadline = "320s"
}

# ----------------------------
# Function source packaging
# ----------------------------
# Layout:
#   ./src/main.py
#   ./src/requirements.txt

data "archive_file" "src_zip" {
  type        = "zip"
  source_dir  = "${path.module}/src"
  output_path = "${path.module}/src.zip"
}

# Artifact Registry is the default for Gen2 builds; we can deploy from local zip directly.
resource "google_cloudfunctions2_function" "fn" {
  name        = "autogen-posix"
  location    = var.region
  description = "Populate Workspace posixAccounts for users missing it"

  build_config {
    runtime     = "python311"
    entry_point = "run" # main.run
    source {
      storage_source {
        bucket = google_storage_bucket.src_bucket.name
        object = google_storage_bucket_object.src_zip.name
      }
    }
  }

  service_config {
    available_memory      = "512M"
    timeout_seconds       = 540
    max_instance_count    = 2
    service_account_email = google_service_account.fn.email

    # Env config for the function
    environment_variables = {
      CUSTOMER            = var.customer
      DOMAIN              = var.domain
      START_UID           = tostring(var.start_uid)
      START_GID           = tostring(var.start_gid)
      GID_EQUALS_UID      = var.gid_equals_uid ? "true" : "false"
      DEFAULT_SHELL       = var.default_shell
      HOME_TEMPLATE       = var.home_template
      STRIP_SUFFIX        = var.strip_suffix
      RPS                 = tostring(var.rps)
      MAX_RETRIES         = tostring(var.max_retries)
      IMPERSONATE_EMAIL   = var.sa_email_to_impersonate
      SECRET_RESOURCE_ID  = google_secret_manager_secret.dwd_sa_key.id
      SECRET_VERSION      = "latest"
    }
  }

  event_trigger {
    trigger_region        = var.region
    event_type            = "google.cloud.pubsub.topic.v1.messagePublished"
    pubsub_topic          = google_pubsub_topic.topic.id
    retry_policy          = "RETRY_POLICY_RETRY"
    service_account_email = google_service_account.fn.email
  }

  depends_on = [google_project_service.services]
}

# GCS bucket to stage the zip
resource "google_storage_bucket" "src_bucket" {
  name          = "${var.project_id}-posix-src-${var.region}"
  location      = var.region
  uniform_bucket_level_access = true
  force_destroy = true
}

resource "google_storage_bucket_object" "src_zip" {
  name   = "function-src-${data.archive_file.src_zip.output_md5}.zip"
  bucket = google_storage_bucket.src_bucket.name
  source = data.archive_file.src_zip.output_path
}
