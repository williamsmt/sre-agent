---
name: gcp-nat-port-recovery
description: Playbook 7 (Tier 2 HITL Approval) — Use when Cloud NAT egress gateway experiences SNAT port exhaustion or dropped packets on outbound connections from GKE workloads.
---

# SRE Playbook 7: Cloud NAT Egress Port Exhaustion & Packet Drop Recovery

## Target Scenario
* **Target Component**: Cloud NAT Router (the active NAT gateway for the cluster's region).
* **Diagnostic Trigger**: `rca_telemetry_expert` isolates SNAT port allocation exhaustion (`allocated_ports` metric cap reached) or `dropped_sent_packets_count > 0` on Cloud NAT router.

## Remediation Action (Tier 2 - HITL Approval Required)
1. Present the recommended Cloud NAT scaling plan to the human operator: `"Increase minimum allocated ports per VM from 64 to 256 on the active Cloud NAT gateway."`
2. Upon operator approval (`APPROVED`), call `remediation_executor_remote("Locate the active Cloud NAT gateway and its router for the cluster region, then increase minimum allocated ports per VM to 256")`.
3. Verify that dropped packet count returns to `0` and report `SUCCESS`.
