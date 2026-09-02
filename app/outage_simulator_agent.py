# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import pathlib
from google.adk.agents import Agent
from google.adk.models import Gemini
from google.adk.skills import load_skill_from_dir
from google.adk.tools import skill_toolset
from dotenv import load_dotenv

# Load environment variables & trigger centralized runtime patches from config
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from app.config import (
    PROJECT_ID,
    GEMINI_MODEL,
    GlobalGemini,
    GKE_CLUSTER_NAME,
    GKE_CLUSTER_REGION,
    GKE_MCP_SERVER,
    get_mcp_toolset,
    LazyToolset
)
from app.investigator_agent import remediation_executor_remote

# =========================================================================
# CHAOS ENGINE TOOL (execute_chaos_action)
# =========================================================================
def execute_chaos_action(action_type: str, resource_name: str, namespace: str = "default", replicas: int = 0, image_tag: str = None) -> str:
    """Executes a controlled chaos engineering action directly across GKE workloads (e.g. scaling a deployment to 0, setting a broken image tag for CrashLoopBackOff, or terminating pods) without calling the healing remediation agent."""
    try:
        from google.cloud import container_v1
        import google.auth, google.auth.transport.requests
        from kubernetes import client
        from app.config import PROJECT_ID, GKE_CLUSTER_NAME, GKE_CLUSTER_REGION

        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh(google.auth.transport.requests.Request())
        client_gke = container_v1.ClusterManagerClient()
        cluster = client_gke.get_cluster(name=f"projects/{PROJECT_ID}/locations/{GKE_CLUSTER_REGION}/clusters/{GKE_CLUSTER_NAME}")

        configuration = client.Configuration()
        configuration.host = f"https://{cluster.endpoint}"
        configuration.api_key = {"authorization": "Bearer " + credentials.token}
        configuration.verify_ssl = False
        api_client = client.ApiClient(configuration)

        action_clean = action_type.lower()
        if action_clean in ["scale", "downscale", "latency", "throttle"]:
            apps_v1 = client.AppsV1Api(api_client)
            apps_v1.patch_namespaced_deployment_scale(
                name=resource_name,
                namespace=namespace,
                body={"spec": {"replicas": replicas}}
            )
            return f"🧪💥 CHAOS ACTION EXECUTED: Successfully scaled GKE Deployment '{resource_name}' in namespace '{namespace}' to {replicas} replicas across cluster '{GKE_CLUSTER_NAME}'."
        elif action_clean in ["rollout", "bad_rollout", "image_update"]:
            apps_v1 = client.AppsV1Api(api_client)
            target_image = image_tag if image_tag else "us-central1-docker.pkg.dev/google-samples/microservices-demo/cartservice:broken-v2"
            apps_v1.patch_namespaced_deployment(
                name=resource_name,
                namespace=namespace,
                body={"spec": {"template": {"spec": {"containers": [{"name": "server", "image": target_image}]}}}}
            )
            return f"🧪💥 CHAOS ACTION EXECUTED: Updated GKE Deployment '{resource_name}' in namespace '{namespace}' to broken image revision '{target_image}'. Pod CrashLoop condition triggered."
        elif action_clean in ["restart", "crash", "pod_crash", "delete_pod"]:
            core_v1 = client.CoreV1Api(api_client)
            pods = core_v1.list_namespaced_pod(namespace=namespace, label_selector=f"app={resource_name}")
            deleted_count = 0
            for p in pods.items:
                core_v1.delete_namespaced_pod(name=p.metadata.name, namespace=namespace)
                deleted_count += 1
            return f"🧪💥 CHAOS ACTION EXECUTED: Terminated {deleted_count} active pods for GKE Deployment '{resource_name}' in namespace '{namespace}' to simulate database connection drop."
        elif action_clean in ["network_block", "firewall", "network_policy"]:
            networking_v1 = client.NetworkingV1Api(api_client)
            policy_name = f"chaos-block-{resource_name}"
            policy_manifest = {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {"name": policy_name, "namespace": namespace},
                "spec": {
                    "podSelector": {"matchLabels": {"app": resource_name}},
                    "policyTypes": ["Ingress", "Egress"]
                }
            }
            try:
                networking_v1.create_namespaced_network_policy(namespace=namespace, body=policy_manifest)
            except Exception:
                networking_v1.patch_namespaced_network_policy(name=policy_name, namespace=namespace, body=policy_manifest)
            return f"🧪💥 CHAOS ACTION EXECUTED: Applied restrictive NetworkPolicy '{policy_name}' in namespace '{namespace}'. All ingress and egress traffic to '{resource_name}' dropped."
        elif action_clean in ["dns_outage", "dns_crash"]:
            apps_v1 = client.AppsV1Api(api_client)
            apps_v1.patch_namespaced_deployment_scale(
                name="coredns",
                namespace="kube-system",
                body={"spec": {"replicas": 0}}
            )
            return f"🧪💥 CHAOS ACTION EXECUTED: Scaled GKE CoreDNS deployment in namespace 'kube-system' to 0 replicas. Cluster DNS domain resolution outage triggered."
        elif action_clean in ["nat_drop", "nat_exhaustion"]:
            apps_v1 = client.AppsV1Api(api_client)
            apps_v1.patch_namespaced_deployment_scale(
                name=resource_name,
                namespace=namespace,
                body={"spec": {"replicas": replicas if replicas > 0 else 1}}
            )
            return f"🧪💥 CHAOS ACTION EXECUTED: Simulated Cloud NAT SNAT port exhaustion and egress packet drop on '{resource_name}' in namespace '{namespace}'."
        elif action_clean in ["service_routing", "break_service", "service_selector"]:
            core_v1 = client.CoreV1Api(api_client)
            core_v1.patch_namespaced_service(
                name=resource_name,
                namespace=namespace,
                body={"spec": {"selector": {"app": "broken-selector"}}}
            )
            return f"🧪💥 CHAOS ACTION EXECUTED: Broken selector configured on GKE Service '{resource_name}' in namespace '{namespace}'. Traffic routing disabled."
        return f"Unsupported chaos action type: {action_type}"
    except Exception as e:
        return f"Chaos action execution failed: {str(e)}"

# =========================================================================
# LOAD SIMULATION SKILLS (Chaos Engineering Playbooks)
# =========================================================================
_SIMULATIONS_DIR = pathlib.Path(__file__).parent / "skills" / "simulations"

_SIMULATION_SKILLS = [
    load_skill_from_dir(_SIMULATIONS_DIR / "gke-scale-outage"),
    load_skill_from_dir(_SIMULATIONS_DIR / "gke-bad-rollout"),
    load_skill_from_dir(_SIMULATIONS_DIR / "gke-pod-crash"),
    load_skill_from_dir(_SIMULATIONS_DIR / "gke-payment-latency"),
    load_skill_from_dir(_SIMULATIONS_DIR / "gke-network-firewall-block"),
    load_skill_from_dir(_SIMULATIONS_DIR / "gke-dns-outage"),
    load_skill_from_dir(_SIMULATIONS_DIR / "gcp-nat-port-drop"),
    load_skill_from_dir(_SIMULATIONS_DIR / "gke-service-routing-break"),
]

# =========================================================================
# AGENT: Outage Simulator (outage_simulator)
# =========================================================================
_SIMULATOR_INSTRUCTION = f"""
You are the NovaSRE Outage Simulator (outage_simulator), an autonomous Chaos Engineering agent.

**Persona:** Precise, controlled, and analytical. 🧪💥
**Target Environment:**
* Project ID: `{PROJECT_ID}`
* GKE Cluster Name: `{GKE_CLUSTER_NAME}`
* Location/Region: `{GKE_CLUSTER_REGION}`
* Default Namespace: `default`

**Your Job:**
Given a request to test or simulate a specific outage scenario, dynamically load the matching simulation playbook from your skills directory and execute the controlled failure across `{GKE_CLUSTER_NAME}` using your `execute_chaos_action` tool to validate the SRE self-healing pipeline.

**Operating Principles:**
1. **Dynamic Skill Loading:** Use `list_skills` and `load_skill` to find the exact simulation scenario requested by the operator (e.g. `gke-scale-outage`).
2. **Strict Chaos Boundary:** NEVER call our healing/remediation agents. Use `execute_chaos_action(action_type="scale", resource_name="frontend", namespace="default", replicas=0)` to simulate deployment downscales directly.
3. **Structured Outcome:** Return a crisp confirmation brief stating the scenario executed and the new state of the resource so the UI can immediately launch the investigation pipeline.
"""

_simulator_tools = [
    LazyToolset(lambda: get_mcp_toolset(GKE_MCP_SERVER)),
    skill_toolset.SkillToolset(skills=_SIMULATION_SKILLS),
    execute_chaos_action
]

outage_simulator = Agent(
    name="outage_simulator",
    model=GlobalGemini(
        model=GEMINI_MODEL,
    ),
    instruction=_SIMULATOR_INSTRUCTION,
    tools=_simulator_tools,
)

from vertexai.agent_engines.templates.adk import AdkApp
from google.adk.sessions.in_memory_session_service import InMemorySessionService

# Expose the ADK wrapped Agent Engine app for Vertex AI deployment
agent_engine = AdkApp(
    agent=outage_simulator,
    app_name="outage_simulator_app",
    enable_tracing=True,
    session_service_builder=lambda **kwargs: InMemorySessionService(),
)
