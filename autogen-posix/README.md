# autogen-posix
This directory contains terraform infrastructure as code in addition to python source code (`src/`) that defines a service for automatically populating missing `posixAccounts` fields for users in your Google Workspace organization. 

The infrastructure as code deploys a Cloud Run Function that is triggered by a Cloud Scheduler job. The source code is deployed on-the-fly as a tarball in a Google Cloud Storage bucket. A function-specific service account is created for this function.

## Quickstart

1. Set the values in `sample.auto.tfvars` appropriate for your organization.
2. Initialize terraform - `terraform init`
3. Create a terraform plan - `terraform plan -out=tfplan`
4. Apply the plan - `terraform apply "tfplan"`
5. [Assign Domain Wide Delegation (DWD)](https://support.google.com/a/answer/162106?hl=en#zippy=%2Cset-up-domain-wide-delegation-for-a-client) to the service account listed in the `admin_sa_unique_id` output field. When assigning DWD, specify the `"https://www.googleapis.com/auth/admin.directory.user"` scope.
