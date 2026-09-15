---
name: gke-horizontal-upsize
description: Playbook 4 (Tier 2 Manual HITL Approval Required) — Use when a checkout service (like paymentservice) experiences high transaction latency (>2000ms) and CPU bottlenecking requiring an operator-approved horizontal upsize.
---

# SRE Playbook 4: GKE High Transaction Latency & Concurrency Bottleneck (`paymentservice`)

## Target Scenario
* **Target Microservices**: `paymentservice` (or checkout services experiencing CPU starvation and HTTP 504 gateway timeouts under peak user spikes).
* **Diagnostic Trigger**: Cloud Monitoring metrics and `list_kubernetes_resources` show `paymentservice` constrained to `1 replica` while handling massive synthetic checkout load.

## Remediation Action (Tier 2 - Manual HITL Horizontal Upsize Required)
1. Recommend a **Horizontal Upsize (`scale up to 3 replicas`)** to increase service capacity and absorb the transaction surge.
2. Present the plan to the operator requiring explicit UI confirmation: `Proposed Action: Scale up GKE deployment 'paymentservice' in namespace 'default' to 3 active replicas`.
3. Once approved (`[ ✅ Approve Horizontal Upsize ]`), call `remediation_executor_remote("scale deployment paymentservice in namespace default to 3 replicas in cluster online-boutique")`.
4. Verify that `status.readyReplicas` reaches `3/3` and report `SUCCESS`.
