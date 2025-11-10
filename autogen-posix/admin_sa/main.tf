# ----------------------------
# "Admin" Service Account with DWD
# After provisioning, the end user needs to provide DWD for this account
# ----------------------------
resource "google_service_account" "admin" {
  account_id   = "autogen-posix-admin"
  display_name = "Admin SA for posixAccounts populator"
}

# Create a service account key
resource "google_service_account_key" "admin_key" {
  service_account_id = google_service_account.admin.name
  public_key_type    = "TYPE_X509_PEM_FILE"
  private_key_type   = "TYPE_GOOGLE_CREDENTIALS_FILE"
}


