---
name: gke-service-routing-recovery
description: Playbook 8 (Tier 2 HITL Approval) — Use when GKE service routing to a microservice (like checkoutservice) is broken due to incorrect or modified service selectors.
---

# SRE Playbook 8: GKE Service Routing Recovery

## Target Scenario
* **Target Component**: GKE Service `checkoutservice`
* **Diagnostic Trigger**: `rca_telemetry_expert` detects incoming requests timeout or fail to reach pods because the service selector matches `broken-selector` instead of `checkoutservice`.

## Remediation Action (Tier 2 - HITL Approval Required)
1. Present the recommended service selector restoration plan to the human operator: `"Restore GKE Service 'checkoutservice' selector in namespace 'default' to target pods with label 'app=checkoutservice'."`
2. Upon operator approval (`APPROVED`), call `remediation_executor_remote("apply k8s manifest to update service checkoutservice selector to app=checkoutservice in namespace default in cluster online-boutique")`.
3. Verify that Pod-to-Pod connectivity is restored and report `SUCCESS`.
