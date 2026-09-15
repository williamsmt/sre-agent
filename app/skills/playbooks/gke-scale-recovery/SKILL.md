---
name: gke-scale-recovery
description: Playbook 1 (Tier 1 Auto-Recovery) — Use when a GKE deployment (like frontend) has 0 active replicas and requires immediate horizontal scale-up back to its target replica count.
---

# SRE Playbook 1: GKE Infrastructure Replica Scale Outage (`Active Replicas = 0`)

## Target Scenario
* **Target Microservices**: `frontend` (or any deployment scaled down to 0).
* **Diagnostic Trigger**: `list_kubernetes_resources` confirms `status.readyReplicas` is `0` while `spec.replicas` or target service capacity requires `1`.

## Remediation Action (Tier 1 - Fast-Path Auto-Recovery)
1. You are pre-approved to **automatically scale the resource back up to its target replica count** (`1`).
2. Call `remediation_executor_remote("scale deployment frontend in namespace default to 1 replica in cluster online-boutique")` automatically.
3. Verify that `status.readyReplicas` returns to `1/1` and report `SUCCESS`.
