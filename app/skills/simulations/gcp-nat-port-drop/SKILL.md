---
name: gcp-nat-port-drop
description: Simulate a Cloud NAT egress port exhaustion and packet drop anomaly on outbound connections from paymentservice to external API gateways.
---

# Outage Simulation: Cloud NAT Egress Port Exhaustion

You are executing a controlled **Chaos Engineering / Network Outage Simulation** on Google Cloud & GKE to test the NovaSRE network triaging pipeline.

## Target Resource
* **Resource Type**: Cloud NAT Router Gateway
* **Resource Name**: the active NAT gateway for the cluster region
* **Target Workload**: `paymentservice`
* **Namespace**: `default`

## Simulation Instructions
1. When asked to execute the `gcp-nat-port-drop` simulation, invoke `execute_chaos_action` to downscale NAT port allocation limits or downscale outbound connection worker capacity on `paymentservice`.
2. Call your `execute_chaos_action` tool with arguments: `action_type="nat_drop"`, `resource_name="paymentservice"`, `namespace="default"`.
3. Output a clear confirmation brief: `"OUTAGE SIMULATION SUCCESSFUL: Outbound egress traffic on paymentservice throttled to trigger Cloud NAT SNAT port exhaustion anomaly."`
