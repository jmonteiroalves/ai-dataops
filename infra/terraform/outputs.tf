output "job_name" {
  value = google_cloud_run_v2_job.dataops.name
}

output "dashboard_url" {
  value = google_cloud_run_v2_service.dashboard.uri
}

output "snapshot_bucket" {
  value = google_storage_bucket.serving.name
}

output "artifact_repository" {
  value = google_artifact_registry_repository.images.name
}

output "job_image" {
  value = local.job_image
}

output "dashboard_image" {
  value = local.dashboard_image
}

output "deploy_service_account" {
  value = google_service_account.deploy.email
}

output "workload_identity_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}

output "scheduler_service_account" {
  value = google_service_account.scheduler.email
}
