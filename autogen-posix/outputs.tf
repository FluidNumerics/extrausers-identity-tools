output "function_name" { value = google_cloudfunctions2_function.fn.name }
output "function_region" { value = google_cloudfunctions2_function.fn.location }
output "scheduler_job" { value = google_cloud_scheduler_job.job.name }
output "function_sa" { value = google_service_account.fn.email }
