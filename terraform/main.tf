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

/**
 * Root Terraform Configuration for SRE Agent Platform & Microservices Demo
 *
 * Master Switches:
 * - var.deploy_infrastructure: Controls VPC, Subnet, and GKE Autopilot cluster creation.
 * - var.deploy_agents: Controls Vertex AI Reasoning Engine container deployment.
 */

resource "google_project" "new_project" {
  count           = var.create_project ? 1 : 0
  name            = var.gcp_project_id
  project_id      = var.gcp_project_id
  billing_account = var.billing_account_id
  deletion_policy = "DELETE"
}

module "foundation" {
  source         = "./modules/foundation"
  gcp_project_id = var.gcp_project_id
  gcp_region     = var.gcp_region
  depends_on     = [google_project.new_project]
}

module "bq_seed" {
  source          = "./modules/bq_seed"
  gcp_project_id  = var.gcp_project_id
  gcp_region      = var.gcp_region
  depends_on_apis = module.foundation.apis_enabled
  depends_on      = [module.foundation]
}

module "playbooks_gcs" {
  source          = "./modules/playbooks_gcs"
  gcp_project_id  = var.gcp_project_id
  gcp_region      = var.gcp_region
  depends_on_apis = module.foundation.apis_enabled
  depends_on      = [module.foundation]
}

module "networking" {
  source                = "./modules/networking"
  gcp_project_id        = var.gcp_project_id
  gcp_region            = var.gcp_region
  deploy_infrastructure = var.deploy_infrastructure
  depends_on_apis       = module.foundation.apis_enabled
  depends_on            = [module.foundation]
}

module "iam" {
  source          = "./modules/iam"
  gcp_project_id  = var.gcp_project_id
  depends_on_apis = module.foundation.apis_enabled
  depends_on      = [module.foundation]
}

module "gke_demo" {
  source                = "./modules/gke_demo"
  gcp_project_id        = var.gcp_project_id
  gcp_region            = var.gcp_region
  network_name          = module.networking.network_name
  subnetwork_name       = module.networking.subnetwork_name
  deploy_infrastructure = var.deploy_infrastructure
  depends_on_apis       = module.foundation.apis_enabled
  depends_on            = [module.foundation, module.networking, module.iam]
}

module "agent_deployer" {
  source                = "./modules/agent_deployer"
  gcp_project_id        = var.gcp_project_id
  gcp_region            = var.gcp_region
  staging_bucket_name   = module.foundation.staging_bucket_name
  service_account_email = module.iam.sre_agent_sa_email
  gemini_model          = var.gemini_model
  deploy_agents         = var.deploy_agents
  depends_on_apis       = module.foundation.apis_enabled
  depends_on_iam        = module.iam.sre_agent_sa_email
  depends_on            = [module.foundation, module.iam]
}
