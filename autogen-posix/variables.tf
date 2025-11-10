variable "project_id" {
  type        = string
  description = "GCP project ID"
}

variable "region" {
  type        = string
  description = "Region for Cloud Functions Gen2 (e.g., us-central1)"
  default     = "us-central1"
}

variable "sa_email_to_impersonate" {
  type        = string
  description = "Admin user to impersonate for DWD (e.g., admin@yourdomain.com)"
}

variable "customer" {
  type        = string
  description = "Google customer ID (or 'my_customer'); set either this OR domain"
  default = ""
}

variable "domain" {
  type        = string
  description = "Optional: restrict to a specific domain (e.g., example.com). Leave empty to use 'customer'."
  default     = ""
}

variable "start_uid" {
  type = number
  default = 30000 
}

variable "start_gid" { 
  type = number
  default = 30000 
}
variable "gid_equals_uid" {
  type = bool
  default = true
}

variable "default_shell" {
  type = string
  default = "/bin/bash"
}

variable "home_template" {
  type = string
  default = "/home/{username}"
}

variable "strip_suffix"  {
  type = string
  default = ""
} # e.g., "_mydomain_com" or empty to use generic cleanup

variable "rps" {
  type = number
  default = 5
}

variable "max_retries" {
  type = number
  default = 5
}

# Cron schedule (Cloud Scheduler format)
variable "cron_schedule" {
  type        = string
  description = "When to run (UTC). Example: every 2h -> '0 */2 * * *'"
  default     = "0 2 * * *"
}

