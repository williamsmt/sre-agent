---
name: gke-dns-recovery
description: Playbook 6 (Tier 1 Auto-Recovery) — Use when GKE Cluster DNS (coredns / kube-dns in namespace kube-system) has 0 active replicas, causing cluster-wide domain resolution timeouts across microservices.
---

# SRE Playbook 6: GKE Cluster DNS Outage (`CoreDNS Replicas = 0`)

## Target Scenario
* **Target Component**: GKE CoreDNS (`coredns` / `kube-dns` in namespace `kube-system`).
* **Diagnostic Trigger**: `rca_telemetry_expert` confirms `coredns` deployment in namespace `kube-system` has `readyReplicas = 0` and domain resolution queries for `.svc.cluster.local` are timing out.

## Remediation Action (Tier 1 - Fast-Path Auto-Recovery)
1. You are pre-approved to **automatically scale CoreDNS back up to healthy capacity** (`2 replicas`).
2. Call `remediation_executor_remote("scale deployment coredns in namespace kube-system to 2 replicas in cluster online-boutique")` automatically.
3. Verify that `status.readyReplicas` returns to `2/2` and internal DNS resolution succeeds. Report `SUCCESS`.
