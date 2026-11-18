variable "project_id" {
  description = "GCP project id (demo project: ai-dataops-demo-jma)"
  type        = string
}

variable "region" {
  description = "Cloud Run and BigQuery location"
  type        = string
  default     = "us-central1"
}

variable "billing_account" {
  description = "Billing account id for the budget alert (empty disables the budget resource)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "budget_usd" {
  description = "Monthly budget for the demo, in USD"
  type        = string
  default     = "5"
}

variable "image_tag" {
  description = "Image tag to deploy (built by CI or locally)"
  type        = string
  default     = "latest"
}

variable "job_image" {
  description = "Optional full image reference for the Cloud Run Job"
  type        = string
  default     = ""
}

variable "dashboard_image" {
  description = "Optional full image reference for the dashboard service"
  type        = string
  default     = ""
}

variable "scheduler_cron" {
  description = "Daily orchestration schedule"
  type        = string
  default     = "0 6 * * *"
}

variable "scheduler_time_zone" {
  description = "Business timezone for the schedule"
  type        = string
  default     = "America/Sao_Paulo"
}

variable "gemini_model" {
  description = "Pinned Gemini model used by the diagnosis service"
  type        = string
  default     = "gemini-3.5-flash-lite"
}

variable "github_owner" {
  description = "GitHub user or organization allowed to deploy through Workload Identity Federation"
  type        = string
  default     = "jmonteiroalves"
}

variable "github_repo" {
  description = "GitHub repository allowed to deploy"
  type        = string
  default     = "ai-dataops"
}
