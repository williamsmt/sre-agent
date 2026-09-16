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
from google.adk.agents import Agent
from google.adk.apps import App
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
    COMPUTE_MCP_SERVER,
    get_mcp_toolset,
    LazyToolset,
    GlobalGemini,
    PAM_ENABLED,
    PAM_ENTITLEMENT_NAME,
    PAM_GRANT_DURATION_SECONDS,
    PAM_GRANT_ACTIVE_TIMEOUT_SECONDS,
)

# =========================================================================
# PAM JIT ELEVATION (before_agent_callback)
# =========================================================================
# Before the remediator touches GKE/GCP, it requests a temporary PAM grant so the
# privileged access is JIT and auditable. The operator's justification arrives in-band
# as a `[PAM_JUSTIFICATION]:` trailer on the request text (forwarded by the RCA agent);
# if absent we synthesize a Tier-1 default (D6) so the audit trail is never empty.
#
# The PAM grant elevates the identity that CALLS create_grant — which is this remediator's
# own service account (the entitlement's sole eligibleUser) — so the call MUST run here.
#
# Per D3, standing roles/container.developer is retained for now, so PAM is additive/
# audit-only: a grant failure is logged loudly but does NOT block healing. Once the
# standing role is removed (in Terraform), this becomes the hard gate.
_PAM_JUSTIFICATION_MARKER = "[PAM_JUSTIFICATION]:"
_PAM_TIER1_DEFAULT = (
    "Automated SRE remediation initiated by remediation_executor; no operator justification "
    "was supplied. JIT elevation requested to execute an approved healing action."
)

def _extract_pam_justification(content) -> str:
    """Pull the operator justification out of the inbound request's [PAM_JUSTIFICATION]: trailer."""
    if not content or not getattr(content, "parts", None):
        return ""
    blobs = []
    for part in content.parts:
        text = getattr(part, "text", None)
        if text:
            blobs.append(text)
        inline = getattr(part, "inline_data", None)
        if inline is not None and getattr(inline, "data", None):
            raw = inline.data
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "ignore")
            blobs.append(str(raw))
    for blob in blobs:
        idx = blob.find(_PAM_JUSTIFICATION_MARKER)
        if idx != -1:
            return blob[idx + len(_PAM_JUSTIFICATION_MARKER):].strip()
    return ""

def _request_pam_grant_sync(justification: str, logger) -> None:
    """Create a JIT PAM grant and poll until ACTIVE. Blocking; run via asyncio.to_thread."""
    import time
    from google.cloud import privilegedaccessmanager_v1 as pam

    client = pam.PrivilegedAccessManagerClient()
    grant = pam.Grant()
    grant.requested_duration = {"seconds": PAM_GRANT_DURATION_SECONDS}
    grant.justification.unstructured_justification = justification[:4000]

    logger.info(
        "[pam] requesting JIT grant on %s (duration=%ss) justification=%r",
        PAM_ENTITLEMENT_NAME, PAM_GRANT_DURATION_SECONDS, justification[:200],
    )
    created = client.create_grant(parent=PAM_ENTITLEMENT_NAME, grant=grant)
    logger.info("[pam] grant created: %s (state=%s)", created.name, created.state.name)

    terminal_bad = {
        pam.Grant.State.DENIED, pam.Grant.State.ACTIVATION_FAILED, pam.Grant.State.REVOKED,
        pam.Grant.State.EXPIRED, pam.Grant.State.ENDED, pam.Grant.State.WITHDRAWN,
    }
    state, name = created.state, created.name
    deadline = time.time() + PAM_GRANT_ACTIVE_TIMEOUT_SECONDS
    while state != pam.Grant.State.ACTIVE and state not in terminal_bad and time.time() < deadline:
        time.sleep(2)
        state = client.get_grant(name=name).state
    if state == pam.Grant.State.ACTIVE:
        logger.info("[pam] ✅ JIT grant ACTIVE: %s", name)
    else:
        logger.warning(
            "[pam] ⚠️ grant %s did not reach ACTIVE (state=%s); proceeding on standing role (D3)",
            name, state.name,
        )

async def _pam_grant_callback(callback_context):
    """before_agent_callback: obtain a JIT PAM grant before the remediator executes.

    Non-blocking on failure (D3): logs and returns None so healing proceeds on the
    standing role. Returns None always — never short-circuits the agent.
    """
    import asyncio
    import logging
    logger = logging.getLogger("google_adk")

    if not PAM_ENABLED:
        return None

    justification = _extract_pam_justification(callback_context.user_content) or _PAM_TIER1_DEFAULT
    try:
        await asyncio.to_thread(_request_pam_grant_sync, justification, logger)
    except Exception as e:
        logger.warning("[pam] ⚠️ JIT grant request failed: %s; proceeding on standing role (D3)", e)
    return None

# =========================================================================
# AGENT: The Secure Healing Worker (remediation_executor)
# =========================================================================
_REMEDIATION_INSTRUCTION = f"""
You are the GKE & GCP Remediation Executor (remediation_executor), an elite infrastructure engineering healing worker.

**Persona:** Highly disciplined, operationally focused, and precise. Emojis for dry armor. 🤖🛡️
**Target Environment:**
* Project ID: `{PROJECT_ID}`
* GKE Cluster Name: `{GKE_CLUSTER_NAME}`
* Location/Region: `{GKE_CLUSTER_REGION}`
* Default Namespace: `default`

**Your Job:**
Given an approved action and target GKE workload or GCP cloud infrastructure resource, execute the healing maneuvers safely and validate system recovery using your GKE and Compute OneMCP tools against project `{PROJECT_ID}` and cluster `{GKE_CLUSTER_NAME}` in `{GKE_CLUSTER_REGION}`.

**OneMCP Schema Awareness & API Mechanics:**
1. **Declarative API Operation (GKE & Compute Engine):** The OneMCP servers strictly expose standardized declarative REST API endpoints for Kubernetes (`get_k8s_resource`, `patch_k8s_resource`, `apply_k8s_manifest`, `delete_k8s_resource`, `get_k8s_rollout_status`) and Google Cloud Compute/VPC infrastructure (`update_router_nat`, `patch_firewall_rule`, `update_url_map`, `get_router`). They do NOT implement higher-level CLI wrappers or raw shell macro executions (such as `kubectl rollout undo` or raw bash `gcloud compute routers nats update` string execution).
2. **Tool Selection Guardrail:** Before executing any modifying action, evaluate the exact tool definitions in your loaded schema array. NEVER construct or infer tool names based on conversational verbs or CLI strings in your instructions (e.g., do not attempt to call non-existent tools like `undo_k8s_rollout` or execute untethered gcloud shell strings).
3. **Resource State Translation:** Apply your autonomous engineering judgment to translate requested remediation goals into declarative OneMCP parameter modifications:
   - **Kubernetes Workloads & Overlay:** Patch container images to revert rollouts, update deployment replica counts for scaling, modify Service selector labels, or delete blocking K8s NetworkPolicies.
   - **GCP Network Infrastructure:** Translate cloud infrastructure healing instructions (such as scaling Cloud NAT minimum allocated ports or updating VPC firewall DENY rules) directly into the corresponding declarative Compute Engine OneMCP API calls against project `{PROJECT_ID}` in region `{GKE_CLUSTER_REGION}`.

**Operating Principles:**
1. **Strict HITL Compliance:** You operate under strict Human-in-the-Loop gating. You MUST ONLY execute the action that has been explicitly approved in your prompt. Never improvise outside the requested recovery scope.
2. **Safety & Validation:** After executing a resource state modification or deletion, query status (`get_k8s_rollout_status`, `get_k8s_resource`, or Compute inspection tools) to verify that the target workload or network infrastructure has successfully reached a stable and healed state.
3. **Output Format & Timestamp Verification:** Return a concise, structured brief confirming the action taken, the resource targeted, the post-remediation health validation status, and explicit chronological timestamps:
   - `mitigation_executed_time`: ISO 8601 timestamp when the healing command was applied.
   - `recovery_verified_time`: ISO 8601 timestamp when workload or infrastructure health was successfully validated.
   (These verified execution timestamps are passed directly to downstream postmortem reporting agents without secondary log calls).
"""

_remediation_tools = [
    LazyToolset(lambda: get_mcp_toolset(GKE_MCP_SERVER)),
    LazyToolset(lambda: get_mcp_toolset(COMPUTE_MCP_SERVER))
]

remediation_executor = Agent(
    name="remediation_executor",
    model=GlobalGemini(
        model=GEMINI_MODEL,
    ),
    instruction=_REMEDIATION_INSTRUCTION,
    tools=_remediation_tools,
    before_agent_callback=_pam_grant_callback,
)

# =========================================================================
# CENTRALIZED A2A AGENT DECLARATION (Vertex AI A2aAgent Template)
# =========================================================================
from vertexai.preview.reasoning_engines import A2aAgent
from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
from google.adk.runners import Runner

def _get_remediation_agent_card():
    from a2a import types as a2a_types
    return a2a_types.AgentCard(
        name="remediation-executor",
        description="The GKE & GCP Remediation Executor agent. Executes approved GKE workload and GCP network infrastructure healing actions.",
        version="1.0",
        url="https://dummy.com",
        capabilities=a2a_types.AgentCapabilities(),
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        skills=[],
        preferredTransport="HTTP+JSON",
    )

def build_remediation_executor():
    import vertexai
    from app.config import PROJECT_ID, GEMINI_MODEL_LOCATION
    # RE framework resets vertexai.global_config to us-east1 before each request;
    # re-init here (inside agent_executor_builder) so model calls use the global endpoint.
    vertexai.init(project=PROJECT_ID, location=GEMINI_MODEL_LOCATION)

    from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
    from google.adk.sessions.in_memory_session_service import InMemorySessionService
    from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
    from google.adk.auth.credential_service.in_memory_credential_service import InMemoryCredentialService

    runner = Runner(
        app_name="remediation-executor",
        agent=remediation_executor,
        artifact_service=InMemoryArtifactService(),
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
        credential_service=InMemoryCredentialService(),
    )
    return A2aAgentExecutor(runner=runner)

# Expose the pure A2A Agent template for Vertex AI Agent Engine deployment so Agent Registry registers Agent Type: A2A
agent_engine = A2aAgent(
    agent_card=_get_remediation_agent_card(),
    agent_executor_builder=build_remediation_executor
)
