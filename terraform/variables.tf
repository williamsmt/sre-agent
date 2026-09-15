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

variable "gcp_project_id" {
  description = "The Google Cloud Project ID where the SRE Agent Platform will be deployed."
  type        = string
}

variable "create_project" {
  description = "Set to true (default false) if you want Terraform to create a brand new GCP project and link billing."
  type        = bool
  default     = false
}

variable "billing_account_id" {
  description = "The GCP Billing Account ID to link when create_project is set to true."
  type        = string
  default     = "01DC72-509B36-1FD878"
}

variable "gcp_region" {
  description = "The target Google Cloud region for deploying resources and Vertex AI Reasoning Engines."
  type        = string
  default     = "us-central1"
}

variable "gcp_zone" {
  description = "The primary Google Cloud zone for GKE resources."
  type        = string
  default     = "us-central1-a"
}

variable "gemini_model" {
  description = "The Gemini model ID to use for the Investigator and Remediation agents."
  type        = string
  default     = "gemini-2.5-pro"
}

variable "deploy_infrastructure" {
  description = "Set to true (default) to deploy the entire solution: VPC, Subnet, GKE Autopilot cluster, and Online Boutique microservices. Set to false if you ONLY want to deploy the cloud agents."
  type        = bool
  default     = true
}

variable "deploy_agents" {
  description = "Set to true (default) to build, package, and deploy the SRE Reasoning Engines (rca-telemetry-expert and remediation-executor) to Vertex AI."
  type        = bool
  default     = true
}
