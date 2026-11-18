locals {
  artifact_repo = "ai-dataops"
  bucket_name   = "${var.project_id}-ai-dataops-serving"
  job_image = var.job_image != "" ? var.job_image : (
    "${var.region}-docker.pkg.dev/${var.project_id}/${local.artifact_repo}/job:${var.image_tag}"
  )
  dashboard_image = var.dashboard_image != "" ? var.dashboard_image : (
    "${var.region}-docker.pkg.dev/${var.project_id}/${local.artifact_repo}/dashboard:${var.image_tag}"
  )
  datasets = toset([
    "dataops_meta",
    "dataops_obs",
    "dataops_quality",
    "dataops_detect",
    "dataops_ai",
    "demo",
  ])
  job_roles = toset([
    "roles/bigquery.dataEditor",
    "roles/bigquery.jobUser",
    "roles/aiplatform.user",
    "roles/logging.logWriter",
  ])
  deploy_roles = toset([
    "roles/run.admin",
    "roles/artifactregistry.writer",
    "roles/iam.serviceAccountUser",
    "roles/logging.viewer",
  ])
}

resource "google_project_service" "services" {
  for_each = toset([
    "bigquery.googleapis.com",
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "aiplatform.googleapis.com",
    "cloudscheduler.googleapis.com",
    "storage.googleapis.com",
    "iam.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "billingbudgets.googleapis.com",
  ])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

data "google_project" "this" {
  project_id = var.project_id
}

# --------------------------------------------------------------------- identities
resource "google_service_account" "job" {
  account_id   = "dataops-job"
  display_name = "AI DataOps job"
  project      = var.project_id
}

resource "google_service_account" "dashboard" {
  account_id   = "dataops-dashboard"
  display_name = "AI DataOps dashboard"
  project      = var.project_id
}

resource "google_service_account" "scheduler" {
  account_id   = "dataops-scheduler"
  display_name = "AI DataOps scheduler"
  project      = var.project_id
}

resource "google_service_account" "build" {
  account_id   = "dataops-build"
  display_name = "AI DataOps image build"
  project      = var.project_id
}

resource "google_project_iam_member" "build" {
  for_each = toset([
    "roles/artifactregistry.writer",
    "roles/cloudbuild.builds.builder",
    "roles/logging.logWriter",
    "roles/storage.admin",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.build.email}"
}

resource "google_service_account" "deploy" {
  account_id   = "dataops-deploy"
  display_name = "AI DataOps GitHub deploy"
  project      = var.project_id
}

resource "google_project_iam_member" "job" {
  for_each = local.job_roles
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.job.email}"
}

resource "google_project_iam_member" "dashboard_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.dashboard.email}"
}

resource "google_project_iam_member" "scheduler_run" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_project_iam_member" "deploy" {
  for_each = local.deploy_roles
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.deploy.email}"
}

resource "google_service_account_iam_member" "scheduler_act_as_job" {
  service_account_id = google_service_account.job.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_service_account_iam_member" "deploy_act_as_job" {
  service_account_id = google_service_account.job.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deploy.email}"
}

resource "google_service_account_iam_member" "deploy_act_as_dashboard" {
  service_account_id = google_service_account.dashboard.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deploy.email}"
}

# ---------------------------------------------------------------------- storage
resource "google_storage_bucket" "serving" {
  name                        = local.bucket_name
  location                    = var.region
  project                     = var.project_id
  uniform_bucket_level_access = true
  force_destroy               = true

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.services]
}

resource "google_storage_bucket_iam_member" "job_object_admin" {
  bucket = google_storage_bucket.serving.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.job.email}"
}

resource "google_storage_bucket_iam_member" "dashboard_viewer" {
  bucket = google_storage_bucket.serving.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.dashboard.email}"
}

# --------------------------------------------------------------------- BigQuery
resource "google_bigquery_dataset" "datasets" {
  for_each = local.datasets

  dataset_id                 = each.value
  friendly_name              = "AI DataOps ${each.value}"
  location                   = var.region
  project                    = var.project_id
  delete_contents_on_destroy = true

  depends_on = [google_project_service.services]
}

# ------------------------------------------------------------- artifact registry
resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = local.artifact_repo
  format        = "DOCKER"
  description   = "AI DataOps job and dashboard images"
  project       = var.project_id

  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-three"
    action = "KEEP"
    most_recent_versions {
      keep_count = 3
    }
  }

  depends_on = [google_project_service.services]
}

# ------------------------------------------------------------------- Cloud Run
resource "google_cloud_run_v2_job" "dataops" {
  name     = "dataops-job"
  location = var.region
  project  = var.project_id

  template {
    parallelism = 1
    task_count  = 1

    template {
      service_account = google_service_account.job.email
      timeout         = "14400s"
      max_retries     = 1

      containers {
        image   = local.job_image
        command = ["dataops"]
        args    = ["run-daily"]

        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }

        env {
          name  = "DATAOPS_ENV"
          value = "gcp"
        }
        env {
          name  = "DATAOPS_ENGINE"
          value = "bigquery"
        }
        env {
          name  = "DATAOPS_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "DATAOPS_REGION"
          value = var.region
        }
        env {
          name  = "DATAOPS_SNAPSHOT_BUCKET"
          value = google_storage_bucket.serving.name
        }
        env {
          name  = "DATAOPS_GEMINI_MODEL"
          value = var.gemini_model
        }
        env {
          name  = "DATAOPS_GEMINI_LOCATION"
          value = "global"
        }
        env {
          name  = "DATAOPS_DIAGNOSIS_BUDGET_USD"
          value = "2"
        }
        env {
          name  = "DATAOPS_DEMO_DAILY_VOLUME"
          value = "1500"
        }
        env {
          name  = "DATAOPS_LOG_FORMAT"
          value = "json"
        }
      }
    }
  }

  depends_on = [
    google_project_service.services,
    google_artifact_registry_repository.images,
    google_bigquery_dataset.datasets,
  ]
}

resource "google_cloud_run_v2_service" "dashboard" {
  name     = "dataops-dashboard"
  location = var.region
  project  = var.project_id
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.dashboard.email

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      image   = local.dashboard_image
      command = ["streamlit"]
      args = [
        "run",
        "src/ai_dataops/dashboard/app.py",
        "--server.port=8080",
        "--server.address=0.0.0.0",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
      ]

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      env {
        name  = "DATAOPS_SNAPSHOT_URI"
        value = "gs://${google_storage_bucket.serving.name}/serving/snapshot.json"
      }
      env {
        name  = "DATAOPS_PROJECT_ID"
        value = var.project_id
      }
    }
  }

  depends_on = [google_project_service.services]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.dashboard.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ------------------------------------------------------------------- Scheduler
resource "google_cloud_scheduler_job" "daily" {
  name      = "dataops-daily"
  project   = var.project_id
  region    = var.region
  schedule  = var.scheduler_cron
  time_zone = var.scheduler_time_zone

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.dataops.name}:run"
    body        = base64encode("{}")
    headers = {
      "Content-Type" = "application/json"
    }

    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  depends_on = [google_project_service.services]
}

# ---------------------------------------------------------------- budget alerts
resource "google_billing_budget" "demo" {
  count           = var.billing_account == "" ? 0 : 1
  billing_account = var.billing_account
  display_name    = "AI DataOps demo budget"

  amount {
    specified_amount {
      currency_code = "USD"
      units         = var.budget_usd
    }
  }

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.8
  }
  threshold_rules {
    threshold_percent = 1.0
  }
}

# ------------------------------------------------------- GitHub OIDC (deploy)
resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions"
  description               = "OIDC pool for ai-dataops deployments"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  attribute_condition = "assertion.repository == \"${var.github_owner}/${var.github_repo}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_deploy" {
  service_account_id = google_service_account.deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_owner}/${var.github_repo}"
}
