output "gke_cluster_name" {
  description = "The deployed GKE cluster name."
  value       = module.gke_demo.cluster_name
}

output "gke_cluster_endpoint" {
  description = "The deployed GKE cluster API endpoint."
  value       = module.gke_demo.cluster_endpoint
}

output "sre_agent_service_account" {
  description = "The dedicated execution Service Account email for Reasoning Engines."
  value       = module.iam.sre_agent_sa_email
}

output "staging_bucket" {
  description = "The GCS telemetry bucket for reports and traces."
  value       = module.foundation.staging_bucket_name
}

output "playbooks_gcs_bucket" {
  description = "The central GCS bucket containing modular SRE markdown playbooks."
  value       = module.playbooks_gcs.playbooks_bucket_name
}
