output "service_account_key_json" {
    description = "The JSON private key of the service account."
    value       = base64decode(google_service_account_key.admin_key.private_key)
    sensitive   = true
}
output "admin_sa" { value = google_service_account.admin.email }
output "admin_sa_unique_id" { value = google_service_account.admin.unique_id }
