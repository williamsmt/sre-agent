---
name: gke-network-firewall-recovery
description: Playbook 5 (Tier 2 HITL Approval) — Use when a GKE microservice (like checkoutservice or frontend) is isolated by a blocking NetworkPolicy or GKE firewall rule dropping ingress/egress connections.
---

# SRE Playbook 5: GKE Network Isolation & Firewall Drop Recovery

## Target Scenario
* **Target Microservice**: `checkoutservice` / `frontend` (or any service blocked by NetworkPolicy/firewall).
* **Diagnostic Trigger**: `rca_telemetry_expert` isolates dropped traffic, TCP SYN timeouts, or blocked ports via NetworkPolicy `chaos-block-checkoutservice` or GCP Firewall DENY logs.

## Remediation Action (Tier 2 - HITL Approval Required)
1. Present the recommended network unblock plan to the human operator: `"Remove restrictive NetworkPolicy 'chaos-block-checkoutservice' in namespace 'default' to restore pod ingress/egress network connectivity."`
2. Upon operator approval (`APPROVED`), call `remediation_executor_remote("delete networkpolicy chaos-block-checkoutservice in namespace default in cluster online-boutique")`.
3. Verify that Pod-to-Pod connectivity is restored and report `SUCCESS`.
