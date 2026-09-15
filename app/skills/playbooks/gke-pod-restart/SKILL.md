---
name: gke-pod-restart
description: Playbook 3 (Tier 2 Manual HITL Approval Required) — Use when a database deployment (like redis-cart) enters a stuck connection lock or pod crash state and requires an operator-approved pod restart.
---

# SRE Playbook 3: GKE Database Pool Lock & Pod Restart (`redis-cart`)

## Target Scenario
* **Target Microservices**: `redis-cart` (or shopping cart database pods that have terminated or entered a stuck connection pool timeout state).
* **Diagnostic Trigger**: Shopping cart database connection exceptions across `cartservice` combined with missing or stuck `redis-cart` pods (`readyReplicas = 0`).

## Remediation Action (Tier 2 - Manual HITL Approval Required)
1. Recommend a clean **Pod Rolling Restart** to clear stuck database locks and spin up fresh replacement instances.
2. Present the plan to the operator requiring explicit UI confirmation: `Proposed Action: Restart pods for GKE deployment 'redis-cart' in namespace 'default' to clear database connection locks`.
3. Once approved (`[ ✅ Approve Pod Restart ]`), call `remediation_executor_remote("restart deployment redis-cart in namespace default in cluster online-boutique")`.
4. Verify pod recovery and report `SUCCESS`.
