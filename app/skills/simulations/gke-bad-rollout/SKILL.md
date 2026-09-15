---
name: gke-bad-rollout
description: Simulate a GKE bad container rollout on the cartservice deployment by updating its image tag to an invalid/broken revision (`bad-v2`), forcing pods into a CrashLoopBackOff state. Use this when asked to simulate a software rollout failure or crash loop on GKE.
---

# Outage Simulation: GKE Bad Rollout (`cartservice`)

You are executing a controlled **Chaos Engineering / Outage Simulation** on GKE to test how NovaSRE handles broken container rollouts and pod crash loops.

## Target Resource
* **Resource Type**: GKE Deployment
* **Resource Name**: `cartservice`
* **Namespace**: `default`
* **Cluster**: `online-boutique`

## Simulation Instructions
1. When asked to execute the `gke-bad-rollout` simulation, you must update the container image of `cartservice` to an invalid or broken tag (`cartservice:broken-v2` from the microservices-demo registry).
2. Call your `execute_chaos_action` tool with arguments: `action_type="rollout"`, `resource_name="cartservice"`, `namespace="default"`.
3. This triggers Kubernetes to attempt a rolling update where new replacement pods enter `CrashLoopBackOff` or `ErrImagePull`.
4. Output a clear confirmation brief: `"OUTAGE SIMULATION SUCCESSFUL: GKE Deployment 'cartservice' updated to broken image revision. Pod CrashLoop condition triggered."`
