---
name: gke-crashloop-rollback
description: Playbook 2 (Tier 1 Auto-Rollback) — Use when a GKE deployment (like cartservice) enters CrashLoopBackOff right after a code rollout and requires immediate rollback to the previous stable release.
---

# SRE Playbook 2: GKE Bad Rollout & CrashLoopBackOff (`cartservice`)

## Target Scenario
* **Target Microservices**: `cartservice` (or any deployment whose container status shows `CrashLoopBackOff` or `ErrImagePull` right after a code push or image rollout).
* **Diagnostic Trigger**: `get_pod_logs` shows initialization exceptions or `sre-correlation` (BigQuery `sre_releases.recent_releases`) verifies a recent deployment (`REL-042: broken-v2`) pushed right before crashes started.

## Remediation Action (Tier 1 - Fast-Path Auto-Rollback)
1. You are pre-approved to automatically execute a deployment rollback to immediately revert the broken image and restore checkout functionality.
2. Call `remediation_executor_remote("Revert GKE Deployment 'cartservice' in namespace 'default' in cluster 'online-boutique' to its previous stable container image revision (gcr.io/google-samples/microservices-demo/cartservice:v1.0.4) and verify replacement pods transition to a healthy Ready state.")` automatically.
3. Verify that replacement pods reach `Running` (`Ready: 1/1`) and report `SUCCESS`.
