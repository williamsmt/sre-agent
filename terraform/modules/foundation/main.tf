# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

locals {
  services = toset([
    "aiplatform.googleapis.com",
    "agentregistry.googleapis.com",
    "apphub.googleapis.com",
    "cloudapiregistry.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "dataform.googleapis.com",
    "iam.googleapis.com",
    "iamconnectors.googleapis.com",
    "iap.googleapis.com",
    "logging.googleapis.com",
    "modelarmor.googleapis.com",
    "monitoring.googleapis.com",
    "networksecurity.googleapis.com",
    "networkservices.googleapis.com",
    "notebooks.googleapis.com",
    "observability.googleapis.com",
    "privilegedaccessmanager.googleapis.com",
    "cloudtrace.googleapis.com",
    "clouderrorreporting.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "storage.googleapis.com",
    "bigquery.googleapis.com",
    "run.googleapis.com"
  ])
}

resource "google_project_service" "required_apis" {
  for_each                   = local.services
  project                    = var.gcp_project_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false
}

resource "google_storage_bucket" "telemetry_bucket" {
  name                        = "${var.gcp_project_id}-telemetry"
  project                     = var.gcp_project_id
  location                    = var.gcp_region
  uniform_bucket_level_access = true
  force_destroy               = true

  depends_on = [google_project_service.required_apis]
}

resource "terraform_data" "enable_onemcp_servers" {
  triggers_replace = [
    google_project_service.required_apis["logging.googleapis.com"].id,
    google_project_service.required_apis["monitoring.googleapis.com"].id,
    google_project_service.required_apis["container.googleapis.com"].id
  ]

  provisioner "local-exec" {
    command = <<-EOT
      export PATH=$PATH:/google/google-cloud-sdk/bin:/usr/lib/google-cloud-sdk/bin:/opt/google-cloud-sdk/bin:/usr/local/google-cloud-sdk/bin:/root/google-cloud-sdk/bin:/usr/local/bin:/usr/bin:/bin
      GCLOUD_CMD=$(command -v gcloud || find / -name gcloud -type f 2>/dev/null | head -n 1)
      if [ -n "$GCLOUD_CMD" ] && [ -f "$GCLOUD_CMD" ]; then
        echo "Enabling OneMCP servers on project ${var.gcp_project_id}..."
        for service in logging.googleapis.com monitoring.googleapis.com container.googleapis.com run.googleapis.com cloudresourcemanager.googleapis.com; do
          $GCLOUD_CMD beta services mcp enable $service --project=${var.gcp_project_id} --quiet || true
        done
      fi
    EOT
  }

  depends_on = [google_project_service.required_apis]
}
