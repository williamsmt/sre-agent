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
from google.adk.models import Gemini  # kept for any non-model uses; GlobalGemini used for agents
from google.adk.skills import load_skill_from_dir
from google.adk.tools import skill_toolset
from google.adk.tools.base_toolset import BaseToolset
from dotenv import load_dotenv

# Load environment variables & trigger centralized runtime patches from config
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from app.config import (
    PROJECT_ID,
    GEMINI_LOCATION,
    GEMINI_MODEL_LOCATION,
    GEMINI_MODEL,
    GlobalGemini,
    GKE_CLUSTER_NAME,
    GKE_CLUSTER_REGION,
    LOGGING_MCP_SERVER,
    MONITORING_MCP_SERVER,
    TRACE_MCP_SERVER,
    ERROR_REPORTING_MCP_SERVER,
    GKE_MCP_SERVER,
    COMPUTE_MCP_SERVER,
    GCS_MCP_SERVER,
    BQ_MCP_SERVER,
    get_mcp_toolset,
    GlobalGemini,
)

# =========================================================================
# 1. MCP LAZY LOADING & SURGICAL TOOL PRUNING UTILITY
# =========================================================================
class FilteringLazyToolset(BaseToolset):
    """Helper to lazily load, filter, and resolve MCP tools at runtime to prevent token bloat."""
    def __init__(self, toolset_fn):
        super().__init__()
        self._toolset_fn = toolset_fn
        self._toolset = None

    async def get_tools(self, readonly_context=None):
        if self._toolset is None:
            self._toolset = self._toolset_fn()
        import inspect
        if inspect.iscoroutinefunction(self._toolset.get_tools):
            tools = await self._toolset.get_tools(readonly_context)
        else:
            tools = self._toolset.get_tools(readonly_context)
            
        # The exact read-only diagnostic tools used by SRE playbooks across OneMCP servers
        allowed_tool_names = {
            "list_log_entries",
            "list_timeseries",
            "list_metric_descriptors",
            "list_dashboards",
            "list_alert_policies",
            "get_alert_policy",
            "list_alerts",
            "get_alert",
            "query_range",
            "list_traces",
            "get_trace",
            # Universal Cloud Error Reporting OneMCP diagnostic tools
            "list_group_stats",
            "get_group",
            "list_events",
            # Universal GKE/Kubernetes OneMCP inspection tools
            "get_kubernetes_resource",
            "list_kubernetes_resources",
            "describe_kubernetes_resource",
            "get_pod_logs",
            "get_pod",
            "list_cluster_events",
            # Universal Compute/VPC OneMCP inspection tools
            "get_router",
            "list_routers",
            "get_firewall_rule",
            "list_firewall_rules",
            "get_url_map",
            "list_url_maps",
            # Universal BigQuery OneMCP query/inspection tools
            "execute_sql",
            "query",
            "list_tables",
            "get_table",
            "list_datasets",
            # Universal GCS OneMCP object inspection tools (for dynamic playbook loading)
            "list_objects",
            "get_object"
        }
        
        filtered_tools = []
        for t in tools:
            name = t.name
            # Keep custom Python tools OR allowed MCP tools
            if name in allowed_tool_names or not hasattr(t, "raw_mcp_tool"):
                # Surgical Schema Pruning: Remove outputSchema to prevent token bloat
                if hasattr(t, "raw_mcp_tool") and t.raw_mcp_tool:
                    t.raw_mcp_tool.outputSchema = None
                filtered_tools.append(t)
                
        return filtered_tools

# =========================================================================
# 2. LOAD SPECIALIST SKILLS (Layer 2)
# =========================================================================
_SKILLS_DIR = pathlib.Path(__file__).parent / "skills"

# Read-only diagnostic and triage skills for the RCA Telemetry Expert
_RCA_SKILLS = [
    # Baseline telemetry triage and generic entrypoint investigation skills
    load_skill_from_dir(_SKILLS_DIR / "diagnostics" / "gcp-logging"),
    load_skill_from_dir(_SKILLS_DIR / "diagnostics" / "gcp-monitoring"),
    load_skill_from_dir(_SKILLS_DIR / "diagnostics" / "investigation-entrypoint"),
    # Specialized domain diagnostic skills
    load_skill_from_dir(_SKILLS_DIR / "diagnostics" / "gke-workloads"),
    load_skill_from_dir(_SKILLS_DIR / "diagnostics" / "gcp-trace"),
    load_skill_from_dir(_SKILLS_DIR / "diagnostics" / "gcp-error-reporting"),
    load_skill_from_dir(_SKILLS_DIR / "diagnostics" / "sre-correlation"),
    load_skill_from_dir(_SKILLS_DIR / "diagnostics" / "gke-networking"),
    load_skill_from_dir(_SKILLS_DIR / "diagnostics" / "google-cloud-networking-observability"),
    load_skill_from_dir(_SKILLS_DIR / "diagnostics" / "google-cloud-global-frontend-configuration"),
    # Remediation playbooks
    load_skill_from_dir(_SKILLS_DIR / "playbooks" / "gke-scale-recovery"),
    load_skill_from_dir(_SKILLS_DIR / "playbooks" / "gke-crashloop-rollback"),
    load_skill_from_dir(_SKILLS_DIR / "playbooks" / "gke-pod-restart"),
    load_skill_from_dir(_SKILLS_DIR / "playbooks" / "gke-horizontal-upsize"),
    load_skill_from_dir(_SKILLS_DIR / "playbooks" / "gke-service-routing-recovery"),
    load_skill_from_dir(_SKILLS_DIR / "playbooks" / "gke-dns-recovery"),
    load_skill_from_dir(_SKILLS_DIR / "playbooks" / "gke-network-firewall-recovery"),
    load_skill_from_dir(_SKILLS_DIR / "playbooks" / "gcp-nat-port-recovery"),
]

# Documentation and reporting skills for the Incident Report Writer
_REPORTING_SKILLS = [
    load_skill_from_dir(_SKILLS_DIR / "reporting" / "postmortem-documentation"),
    load_skill_from_dir(_SKILLS_DIR / "reporting" / "postmortem-generator"),
    load_skill_from_dir(_SKILLS_DIR / "reporting" / "postmortem-aggregator"),
]

# =========================================================================
# AGENT 1: The Diagnostician (rca_telemetry_expert)
# =========================================================================
_RCA_INSTRUCTION = f"""
You are the SRE RCA Telemetry Expert (rca_telemetry_expert), an elite autonomous SRE agent specializing in root-cause analysis, system diagnostics, and automated remediation across Google Cloud environments.

**Persona:** Brilliant, highly technical, and precise. Emojis for dry humor. 🙄💥
**Target Environment:**
* Project ID: `{PROJECT_ID}`
* GKE Cluster Name: `{GKE_CLUSTER_NAME}`
* Location/Region: `{GKE_CLUSTER_REGION}`
* Default Namespace: `default`

**Your Operating Principles (Progressive SRE Triage & Conditional Skill Loading):**

1. **Step 1: Baseline Telemetry Triage & Investigation Entrypoint**:
   For any incoming alert or outage report, use `list_kubernetes_resources` (for GKE workload alerts) alongside logging and monitoring OneMCP servers to perform baseline triage. Confirm whether an anomaly is occurring, and identify the affected resource type (`GKE Workload`, `Compute Engine VM`, `Cloud Run Service`, etc.).
   * **Mandatory Skill Grounding for OneMCP Tools**: When trying to capture logs, metrics, traces, or exception group statistics via OneMCP servers (such as `list_log_entries`, `list_timeseries`, PromQL, `list_traces`, or `list_group_stats`), you MUST always consider and invoke the corresponding skill (`load_skill(skill_name="gcp-logging")`, `load_skill(skill_name="gcp-monitoring")`, `load_skill(skill_name="gcp-trace")`, or `load_skill(skill_name="gcp-error-reporting")`) beforehand. This ensures you utilize optimal filtering, pagination, and time interval windowing to optimize context window and token usage.
   * **Generic Investigation Entrypoint**: Right after completing your initial baseline telemetry triage with `gcp-logging` and `gcp-monitoring` (or when organizing your diagnostic plan), invoke `load_skill(skill_name="investigation-entrypoint")`. Use this generic framework to categorize the root cause domain (Workloads, Networking, Trace Latency, or Error Reporting) and guide your domain-specific skill calls for detailed analysis of categorized issues.
   * **Telemetry Error Prevention**: If a metric query returns `Cannot find metric` or an unknown metric type, this indicates a syntax discrepancy in the metric name — do NOT treat this as a monitoring system failure. Proceed directly to inspect workload health via `list_kubernetes_resources`.
   * If the environment is completely healthy and no anomaly is found, stop and report `remediation_status: "NOT_REQUIRED"`.

2. **Step 2: Follow Investigation Entrypoint for Specialist Skill Delegation**:
   * Strictly execute the domain categorization rules defined inside `investigation-entrypoint` to dynamically invoke the appropriate domain specialist skills (e.g., networking, workloads, tracing, error reporting) required for detailed root-cause analysis.
   * Never generate Python code blocks — invoke tools directly via standard tool calling.

3. **Step 3: Load Recovery Playbook & Execute (Tier 1 Auto-Recovery & Tier 2 Playbook HITL)**:
   Once your diagnostic skill inspection confirms the specific failure state, load the corresponding SRE playbook.

   **Handling operator approval responses (Tier 2 HITL):**
   When you receive any message — plain text, JSON string, or structured DataPart action event — containing "approve" or "reject" (including `{{"action": {{"name": "approve"}}}}` DataPart format sent by GE button clicks), AND `pending_remediation` exists in session state, you MUST immediately call `handle_approval(response=<action_name>)`. Pass the action name as a plain string ("approve" or "reject") — the tool normalizes all input formats. Do NOT investigate further or ask clarifying questions. Call the tool immediately.
   * **Playbook 1 (`gke-scale-recovery`)**: If `readyReplicas = 0` on `frontend` (or any deployment), invoke `load_skill(skill_name="gke-scale-recovery")` and automatically invoke `remediation_executor_remote` with parameter `request="scale deployment frontend in namespace default to 1 replica in cluster online-boutique in region {GKE_CLUSTER_REGION}"` (`Tier 1 Auto-Recovery`).
   * **Playbook 2 (`gke-crashloop-rollback`)**: If `cartservice` container rollout fails (`CrashLoopBackOff` / `ErrImagePull`), invoke `load_skill(skill_name="gke-crashloop-rollback")` and automatically invoke `remediation_executor_remote` with parameter `request="Revert GKE Deployment 'cartservice' in namespace 'default' in cluster 'online-boutique' in region '{GKE_CLUSTER_REGION}' to its previous stable container image revision (gcr.io/google-samples/microservices-demo/cartservice:v1.0.4) and verify replacement pods transition to a healthy Ready state."` (`Tier 1 Auto-Recovery`).
   * **Playbook 3 (`gke-pod-restart`)**: If `redis-cart` database locks or pod termination occur, invoke `load_skill(skill_name="gke-pod-restart")` and invoke `remediation_executor_hitl` with `request="restart deployment redis-cart in namespace default in cluster {GKE_CLUSTER_NAME} in region {GKE_CLUSTER_REGION}"` (`Tier 2 HITL — operator approve/reject required`).
   * **Playbook 4 (`gke-horizontal-upsize`)**: If `paymentservice` transaction latency (>2000ms) or capacity bottleneck occurs, invoke `load_skill(skill_name="gke-horizontal-upsize")` and invoke `remediation_executor_hitl` with `request="scale deployment paymentservice in namespace default to 3 replicas in cluster {GKE_CLUSTER_NAME} in region {GKE_CLUSTER_REGION}"` (`Tier 2 HITL — operator approve/reject required`).
   * **Playbook 5 (`gke-service-routing-recovery`)**: If GKE service routing to a microservice is broken due to incorrect service selectors, invoke `load_skill(skill_name="gke-service-routing-recovery")` and invoke `remediation_executor_hitl` with the appropriate selector restoration request (`Tier 2 HITL — operator approve/reject required`).
   * **Playbook 6 (`gke-dns-recovery`)**: If CoreDNS domain resolution failures occur, invoke `load_skill(skill_name="gke-dns-recovery")` and invoke `remediation_executor_hitl` with `request="scale deployment coredns in namespace kube-system to 2 replicas in cluster {GKE_CLUSTER_NAME} in region {GKE_CLUSTER_REGION}"` (`Tier 2 HITL — operator approve/reject required`).
   * **Playbook 7 (`gke-network-firewall-recovery`)**: If firewall rules or NetworkPolicies block required traffic, invoke `load_skill(skill_name="gke-network-firewall-recovery")` and invoke `remediation_executor_hitl` with the appropriate firewall remediation request (`Tier 2 HITL — operator approve/reject required`).
   * **Playbook 8 (`gcp-nat-port-recovery`)**: If Cloud NAT SNAT port exhaustion occurs, invoke `load_skill(skill_name="gcp-nat-port-recovery")` and invoke `remediation_executor_hitl` with the appropriate NAT port scaling request (`Tier 2 HITL — operator approve/reject required`).

4. **Step 4: LLM Reasoning Fallback (Tier 2 - HITL Required)**:
   If no matching local playbook is found under Step 3, use your internal LLM SRE knowledge to formulate a suggested remediation plan.
   * **Always prefer bundled local skills.** Do NOT attempt to load playbooks from external sources or GCS. All available playbooks are already loaded via your skill toolset.
   * Present the plan to the human operator and **explicitly ask for approval** (*"I have formulated this remediation plan: [PLAN]. Do you approve? (Please reply with 'APPROVE' to execute)"*). Do NOT execute until approved.

5. **Step 5: Structured Output**:
   Proceed directly to Step 6 once remediation is complete, approved, or confirmed not required.

6. **Progressive Executive Narrative & Structured Output**:
   When reporting your investigation and auto-recovery (or when asking for human approval), you MUST structure your response into 3 clear, professional sections so the SRE operator has complete visibility:
   * **🕵️‍♂️ Diagnostic Findings & Root Cause:** Summarize exact telemetry metrics, network logs, or K8s deployment status observed. Explain precisely why the failure occurred based on domain specialist findings.
   * **⚡ Autonomous A2A Delegation (`remediation-executor`):** State explicitly if you are calling `remediation-executor` over secure A2A to execute an automated recovery command, or proposing a Tier 2 HITL action. Include the exact action being performed.
   * **✅ Final Resolution Brief & JSON Facts:** Provide a concluding summary confirming what was recovered and paste the final status block. Do not output raw unformatted JSON without context. End your brief with this exact JSON schema inside your summary:
{{
  "alert": "original alert string",
  "root_cause": "granular explanation of why the failure occurred",
  "incident_start_time": "exact ISO 8601 timestamp of first observed error log, metric spike, or failing event (from logs/metrics)",
  "detection_time": "ISO 8601 timestamp when investigation commenced (via get_current_utc_time)",
  "remediation_status": "SUCCESS | FAILED | NOT_REQUIRED | AWAITING_APPROVAL",
  "recommended_action": "RESTART_POD | SCALE_UP | ROLLBACK | RESTART_SERVICE | UPDATE_FIREWALL | INCREASE_NAT_PORTS | RESTART_DNS | RESTORE_SELECTOR | NONE",
  "target_resource": "identifier of the resource (e.g. deployments/frontend, projects/x/instances/y)",
  "severity": "CRITICAL | WARNING | INFO"
}}
"""

async def remediation_executor_remote(request: str, justification: str = "") -> str:
    """Tier 1 Auto-Recovery: delegate a GKE remediation immediately without operator approval.
    Use this tool for Playbooks 1 & 2 only (scale-recovery, crashloop-rollback).

    Args:
        request: The SRE instruction describing the GKE remediation or rollback action to execute (e.g. "scale deployment frontend in namespace default to 1 replica").
        justification: Optional operator justification (from the HITL Approve click) forwarded
            to the remediator as a `[PAM_JUSTIFICATION]:` trailer so it can request a PAM grant.

    Returns:
        A string describing the execution result of the GKE remediation action.
    """
    import os
    import uuid
    import vertexai
    from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
    from google.adk.agents.invocation_context import InvocationContext, Session
    from google.adk.sessions.in_memory_session_service import InMemorySessionService
    from google.adk.events import Event as ADKEvent
    from google.genai import types as genai_types
    import logging
    
    logger = logging.getLogger("google_adk")
    
    remediation_urn = os.environ.get("REMEDIATION_AGENT_URN")
    if not remediation_urn or not remediation_urn.startswith("projects/"):
        try:
            from vertexai.preview.reasoning_engines import ReasoningEngine
            vertexai.init(project=PROJECT_ID, location=GEMINI_LOCATION)
            for engine in ReasoningEngine.list():
                if engine.display_name == "remediation-executor":
                    remediation_urn = engine.resource_name
                    logger.info("🔍 Dynamically discovered remediation-executor URN from Vertex AI registry: %s", remediation_urn)
                    break
        except Exception as e:
            logger.warning("Dynamic discovery registry lookup notice: %s", e)
            
    if not remediation_urn:
        remediation_urn = f"projects/{PROJECT_ID}/locations/{GEMINI_LOCATION}/reasoningEngines/remediation-executor"
    
    # Initialize Vertex AI with regional endpoint for A2A only
    vertexai.init(
        project=PROJECT_ID,
        location=GEMINI_LOCATION,
        api_endpoint=f"{GEMINI_LOCATION}-aiplatform.googleapis.com"
    )
    # Construct the A2A URL from the URN using the standard ADK REST pattern
    if remediation_urn.startswith("projects/"):
        a2a_url = f"https://{GEMINI_LOCATION}-aiplatform.googleapis.com/v1beta1/{remediation_urn}/a2a"
    else:
        a2a_url = remediation_urn
    # Restore global model endpoint so subsequent model inference calls use the correct location
    vertexai.init(project=PROJECT_ID, location=GEMINI_MODEL_LOCATION)
        
    if not hasattr(RemoteA2aAgent, "_patched_by_sre_agent"):
        original_ensure_httpx_client = RemoteA2aAgent._ensure_httpx_client
        async def patched_ensure_httpx_client(self, *args, **kwargs):
            client = await original_ensure_httpx_client(self, *args, **kwargs)
            self._config.request_interceptors = getattr(self._config, "request_interceptors", []) or []
            from google.adk.a2a.agent.config import RequestInterceptor
            has_auth = any(hasattr(i, "_is_google_bearer_auth") for i in self._config.request_interceptors)
            if not has_auth:
                async def inject_auth(ctx, req, params):
                    import google.auth
                    import google.auth.transport.requests
                    try:
                        credentials, _ = google.auth.default()
                        auth_request = google.auth.transport.requests.Request()
                        credentials.refresh(auth_request)
                        token = credentials.token
                        if params.client_call_context is None:
                            from a2a.client.middleware import ClientCallContext
                            params.client_call_context = ClientCallContext()
                        http_kwargs = params.client_call_context.state.setdefault("http_kwargs", {})
                        headers = http_kwargs.setdefault("headers", {})
                        headers["Authorization"] = f"Bearer {token}"
                        
                        # Inject active W3C trace context (Trace ID & Parent Span ID) for context propagation
                        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
                        TraceContextTextMapPropagator().inject(headers)
                        
                        logger.info("[inject_auth] Successfully injected Authorization and W3C Traceparent headers into A2A request")
                    except Exception as e:
                        logger.warning(f"[inject_auth] Failed to inject Authorization and Traceparent headers: {str(e)}")
                    return req, params

                auth_interceptor = RequestInterceptor(before_request=inject_auth)
                setattr(auth_interceptor, "_is_google_bearer_auth", True)
                self._config.request_interceptors.append(auth_interceptor)
            return client
        RemoteA2aAgent._ensure_httpx_client = patched_ensure_httpx_client
        setattr(RemoteA2aAgent, "_patched_by_sre_agent", True)
        
    from a2a.types import AgentCard, AgentCapabilities
    card = AgentCard(
        name="remediation-executor",
        description="The GKE Remediation Executor agent.",
        version="1.0",
        url=a2a_url,
        capabilities=AgentCapabilities(),
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        skills=[],
        preferredTransport="HTTP+JSON",
    )

    agent = RemoteA2aAgent(
        name="remediation_executor_remote",
        agent_card=card,
    )
    
    session = Session(
        id=f"session-{uuid.uuid4()}", 
        appName="rca-telemetry-expert", 
        user_id="sre-user"
    )
    session_service = InMemorySessionService()
    ctx = InvocationContext(
        session=session,
        invocation_id=f"inv-{uuid.uuid4()}",
        session_service=session_service,
        agent=agent
    )
    
    # Forward the operator justification (if any) as a trailer the remediator's PAM
    # callback parses out to request a JIT grant. Kept in-band on the request text so
    # it rides the existing A2A message path with no protocol change.
    outbound = request
    if justification and justification.strip():
        outbound = f"{request}\n\n[PAM_JUSTIFICATION]: {justification.strip()}"

    session.events.append(ADKEvent(
        author="user",
        content=genai_types.Content(parts=[genai_types.Part(text=outbound)]),
        invocation_id=ctx.invocation_id
    ))
    
    response_texts = []
    try:
        async for event in agent._run_async_impl(ctx):
            if event.error_message:
                raise RuntimeError(event.error_message)
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        response_texts.append(part.text)
        return "".join(response_texts)
    except Exception as e:
        return f"REMEDIATION_FAILED: Failed to execute automated scaling remediation. Error details: {str(e)}"

def _build_hitl_a2ui_messages(request: str) -> list:
    sid = "hitl-approval"
    # A2UI v0.8 requires components to be defined via surfaceUpdate BEFORE the
    # terminal beginRendering signal — the client buffers components and only
    # renders when beginRendering (referencing `root`) arrives. Emitting
    # beginRendering first leaves GE with an empty buffer and it fails to render.
    #
    # PHASE 0 (PAM justification spike): a TextField bound two-way to the data-model
    # path /justification captures the operator's justification. The Approve button's
    # action.context references that same path, so the client resolves the typed value
    # and ships it back inside the userAction click payload. dataModelUpdate seeds the
    # path before render so the binding exists.
    #
    # STATELESS DESIGN: because pending_remediation session state does NOT survive
    # between the render turn and the click turn on a multi-instance Reasoning Engine,
    # the Approve button also carries the full remediation `request` as a literalString
    # in action.context. This makes the click payload self-contained — the interceptor
    # reconstructs both the action AND the request+justification directly from the click,
    # with no dependency on session state. The [hitl][phase0] capture log dumps the raw
    # inbound payload so we can pin the exact context shape before finalizing the parser.
    return [
        {
            "surfaceUpdate": {
                "surfaceId": sid,
                "components": [
                    {"id": "root",    "component": {"Card":   {"child": "content"}}},
                    {"id": "content", "component": {"Column": {"children": {"explicitList": ["title", "desc", "justification", "actions"]}}}},
                    {"id": "title",   "component": {"Text":   {"text": {"literalString": "Remediation Approval Required"}}}},
                    {"id": "desc",    "component": {"Text":   {"text": {"literalString": request}}}},
                    {"id": "justification", "component": {"TextField": {
                        "label": {"literalString": "Justification (required for privileged access)"},
                        "text": {"path": "/justification"},
                        "textFieldType": "longText",
                    }}},
                    {"id": "actions", "component": {"Row":    {"children": {"explicitList": ["approve-btn", "reject-btn"]}}}},
                    {"id": "approve-btn",   "component": {"Button": {"child": "approve-label", "primary": True,  "action": {"name": "approve", "context": [{"key": "justification", "value": {"path": "/justification"}}, {"key": "remediation_request", "value": {"literalString": request}}]}}}},
                    {"id": "approve-label", "component": {"Text":   {"text": {"literalString": "Approve"}}}},
                    {"id": "reject-btn",    "component": {"Button": {"child": "reject-label",  "primary": False, "action": {"name": "reject"}}}},
                    {"id": "reject-label",  "component": {"Text":   {"text": {"literalString": "Reject"}}}},
                ],
            }
        },
        {"dataModelUpdate": {"surfaceId": sid, "path": "/", "contents": [{"key": "justification", "valueString": ""}]}},
        {"beginRendering": {"surfaceId": sid, "root": "root"}},
    ]

async def remediation_executor_hitl(request: str, tool_context) -> dict:
    """Tier 2 HITL: delegate a GKE remediation that REQUIRES operator approval before execution.
    Use this tool for Playbooks 3–8. Surfaces an Approve/Reject A2UI widget; executes only on approval.

    Args:
        request: The SRE instruction describing the GKE remediation action to execute (e.g. "restart deployment redis-cart in namespace default").

    Returns:
        A dict containing validated_a2ui_json for GE to render the Approve/Reject widget.
    """
    tool_context.state["pending_remediation"] = request
    tool_context.actions.skip_summarization = True
    return {"validated_a2ui_json": _build_hitl_a2ui_messages(request)}

async def handle_approval(response: str, tool_context) -> str:
    """Processes the operator's approve/reject response to a pending HITL remediation.

    Args:
        response: The operator response — "approve" to execute, anything else to reject.
                  Accepts plain text or JSON action format (e.g. {"action": {"name": "approve"}})
                  as sent by GE button-click DataParts.

    Returns:
        The remediation result or rejection message.
    """
    pending = tool_context.state.get("pending_remediation")
    if not pending:
        return "No pending remediation found."
    tool_context.state.pop("pending_remediation", None)

    # Normalize: GE sends button clicks as DataParts containing JSON action payloads
    normalized = response.strip().lower()
    try:
        import json
        parsed = json.loads(response)
        if isinstance(parsed, dict):
            action = parsed.get("action", parsed)
            normalized = (action.get("name", "") if isinstance(action, dict) else str(action)).lower()
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass

    if normalized == "approve":
        result = await remediation_executor_remote(pending)
        return f"Remediation executed: {result}"
    else:
        return "Operator rejected the remediation. No action taken."

def _find_action_name(obj):
    """Recursively locate an A2UI action name (userAction.name / action.name) in a parsed payload."""
    if isinstance(obj, dict):
        for key in ("userAction", "action"):
            sub = obj.get(key)
            if isinstance(sub, dict) and isinstance(sub.get("name"), str):
                return sub["name"]
        name = obj.get("name")
        if isinstance(name, str) and name.strip().lower() in ("approve", "reject"):
            return name
        for value in obj.values():
            found = _find_action_name(value)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_action_name(value)
            if found:
                return found
    return None

def _find_user_action(obj):
    """Recursively locate the A2UI userAction dict (has a string 'name', optional 'context')."""
    if isinstance(obj, dict):
        for key in ("userAction", "action"):
            sub = obj.get(key)
            if isinstance(sub, dict) and isinstance(sub.get("name"), str):
                return sub
        name = obj.get("name")
        if isinstance(name, str) and name.strip().lower() in ("approve", "reject"):
            return obj
        for value in obj.values():
            found = _find_user_action(value)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_user_action(value)
            if found:
                return found
    return None

def _extract_hitl_click(content):
    """Extract (action, remediation_request, justification) from a GE A2UI Approve/Reject click.

    STATELESS: everything needed to resolve the approval rides in the click payload, so this
    does NOT depend on pending_remediation session state surviving between turns.

    GE delivers the click as a userAction DataPart (wrapped in <a2a_datapart_json>). The
    button's action.context — which we sent as an array of {key,value} — is resolved by the
    client into a {key: value} MAP inside userAction.context. Empirically confirmed GE quirk:
    a context entry whose value was a path binding (our /justification field) comes back with
    its KEY mangled to the JS string "[object Object]" while the resolved VALUE is intact;
    literalString-valued entries (remediation_request) keep their real key. We parse around
    that, and also tolerate the original array form in case a future GE build changes.

    Returns ("", "", "") on non-HITL turns.
    """
    import json
    if not content or not getattr(content, "parts", None):
        return "", "", ""
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
            blobs.append(str(raw).replace("<a2a_datapart_json>", "").replace("</a2a_datapart_json>", ""))
    for blob in blobs:
        stripped = blob.strip().lower()
        if stripped in ("approve", "reject"):
            return stripped, "", ""
        try:
            parsed = json.loads(blob)
        except (json.JSONDecodeError, TypeError):
            continue
        ua = _find_user_action(parsed)
        if not ua:
            continue
        action = str(ua.get("name", "")).strip().lower()
        if action not in ("approve", "reject"):
            continue
        context = ua.get("context") or {}
        request, justification = "", ""
        if isinstance(context, dict):
            request = context.get("remediation_request") or ""
            justification = context.get("justification") or context.get("[object Object]") or ""
            if not justification:
                # last resort: any string context value that isn't the request
                for k, v in context.items():
                    if k != "remediation_request" and isinstance(v, str) and v:
                        justification = v
                        break
        elif isinstance(context, list):
            for entry in context:
                if not isinstance(entry, dict):
                    continue
                k, v = entry.get("key"), entry.get("value")
                v = v if isinstance(v, str) else ""
                if k == "remediation_request":
                    request = v or request
                elif k == "justification":
                    justification = v or justification
        return action, str(request), str(justification)
    return "", "", ""

def _extract_hitl_action(content) -> str:
    """Extract 'approve'/'reject' from a GE A2UI button-click message.

    GE delivers the click as an A2UI userAction DataPart, which ADK's default inbound
    converter turns into an opaque inline_data blob (wrapped in <a2a_datapart_json> tags)
    that the LLM cannot read — the only human-visible text is a generic 'user action
    triggered' label. This digs the real action name out of every part shape we might
    receive (datapart blob, JSON text, or plain text).
    """
    import json
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
            blobs.append(str(raw).replace("<a2a_datapart_json>", "").replace("</a2a_datapart_json>", ""))
    for blob in blobs:
        stripped = blob.strip().lower()
        if stripped in ("approve", "reject"):
            return stripped
        try:
            name = _find_action_name(json.loads(blob))
        except (json.JSONDecodeError, TypeError):
            name = None
        if name:
            return name.strip().lower()
    return ""

async def _hitl_action_interceptor(callback_context):
    """Deterministically resolve HITL Approve/Reject button clicks before the LLM runs.

    Returning Content short-circuits the agent (ADK skips the model turn), so the
    remediation decision never depends on the LLM parsing the opaque action payload.
    Returns None on non-HITL turns so normal investigation proceeds unchanged.
    """
    from google.genai import types as genai_types
    import logging
    logger = logging.getLogger("google_adk")

    # STATELESS resolution: the Approve/Reject click carries the action, the full
    # remediation_request, and the operator justification in its userAction.context, so
    # we resolve entirely from the click payload and never depend on pending_remediation
    # surviving between the render turn and the click turn (which is unreliable across
    # multi-instance Reasoning Engine routing).
    action, request, justification = _extract_hitl_click(callback_context.user_content)

    if action not in ("approve", "reject"):
        return None

    logger.info(
        "[hitl] resolved operator action=%r via stateless click payload (request=%r, justification_len=%d)",
        action, request, len(justification or ""),
    )

    callback_context.state["pending_remediation"] = ""
    if action == "reject":
        return genai_types.Content(role="model", parts=[genai_types.Part(
            text="🛑 Operator rejected the remediation. No action taken.")])

    # approve — prefer the request from the click; fall back to session state only if an
    # older/context-less click omitted it.
    if not request:
        request = callback_context.state.get("pending_remediation") or ""
    if not request:
        logger.warning("[hitl] approve click carried no remediation_request and no pending state was available")
        return genai_types.Content(role="model", parts=[genai_types.Part(
            text="⚠️ Approval received but the remediation request was missing from the click payload. Please re-run the investigation and approve again.")])

    result = await remediation_executor_remote(request, justification)
    return genai_types.Content(role="model", parts=[genai_types.Part(
        text=f"✅ Operator approved. Remediation executed: {result}")])

def get_current_utc_time() -> str:
    """Returns the current UTC date and time as an ISO 8601 string (e.g. 2026-07-18T06:56:00Z). Use this tool to get current timestamps for log and metric filtering queries."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def utcnow() -> str:
    """Returns the current UTC date and time as an ISO 8601 string (e.g. 2026-07-18T06:56:00Z). Use this tool when querying current log or metric timestamps."""
    return get_current_utc_time()

def list_kubernetes_resources(resource_type: str = "pods", namespace: str = "default", label_selector: str = "") -> str:
    """Lists Kubernetes resources (e.g. pods, deployments) across a namespace. Checks local kubectl first and falls back to GKE API client when inside serverless containers."""
    import shutil, subprocess
    if shutil.which("kubectl"):
        cmd = ["kubectl", "get", resource_type, "-n", namespace]
        if label_selector:
            cmd.extend(["-l", label_selector])
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if res.returncode == 0:
                return res.stdout
        except Exception:
            pass

    # Serverless Python fallback across GKE API when kubectl binary is not installed
    try:
        from google.cloud import container_v1
        import google.auth, google.auth.transport.requests
        from kubernetes import client
        from app.config import PROJECT_ID, GEMINI_LOCATION, GKE_CLUSTER_NAME, GKE_CLUSTER_REGION

        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh(google.auth.transport.requests.Request())
        client_gke = container_v1.ClusterManagerClient()
        cluster = client_gke.get_cluster(name=f"projects/{PROJECT_ID}/locations/{GKE_CLUSTER_REGION}/clusters/{GKE_CLUSTER_NAME}")

        configuration = client.Configuration()
        configuration.host = f"https://{cluster.endpoint}"
        configuration.api_key = {"authorization": "Bearer " + credentials.token}
        configuration.verify_ssl = False
        api_client = client.ApiClient(configuration)

        core_v1 = client.CoreV1Api(api_client)
        if resource_type in ["deployments", "deployment", "deploy"]:
            apps_v1 = client.AppsV1Api(api_client)
            deps = apps_v1.list_namespaced_deployment(namespace=namespace)
            lines = ["NAME\tREADY\tUP-TO-DATE\tAVAILABLE"]
            for d in deps.items:
                lines.append(f"{d.metadata.name}\t{d.status.ready_replicas or 0}/{d.spec.replicas}\t{d.status.updated_replicas or 0}\t{d.status.available_replicas or 0}")
            return "\n".join(lines)
        elif resource_type in ["events", "event", "ev"]:
            evs = core_v1.list_namespaced_event(namespace=namespace)
            lines = ["LAST SEEN\tTYPE\tREASON\tOBJECT\tMESSAGE"]
            for e in sorted(evs.items, key=lambda x: (str(x.last_timestamp or x.event_time or x.metadata.creation_timestamp)), reverse=True)[:30]:
                ts = e.last_timestamp or e.event_time or e.metadata.creation_timestamp
                obj = f"{e.involved_object.kind}/{e.involved_object.name}" if e.involved_object else "N/A"
                lines.append(f"{str(ts)}\t{e.type}\t{e.reason}\t{obj}\t{e.message}")
            return "\n".join(lines)
        elif resource_type in ["services", "service", "svc"]:
            svcs = core_v1.list_namespaced_service(namespace=namespace)
            lines = ["NAME\tTYPE\tCLUSTER-IP\tPORT(S)"]
            for s in svcs.items:
                ports = ",".join([f"{p.port}/{p.protocol}" for p in (s.spec.ports or [])])
                lines.append(f"{s.metadata.name}\t{s.spec.type}\t{s.spec.cluster_ip}\t{ports}")
            return "\n".join(lines)
        elif resource_type in ["pods", "pod", "po"]:
            pods = core_v1.list_namespaced_pod(namespace=namespace)
            lines = ["NAME\tREADY\tSTATUS\tRESTARTS"]
            for p in pods.items:
                ready_cnt = sum(1 for c in (p.status.container_statuses or []) if c.ready)
                total_cnt = len(p.spec.containers or [])
                restarts = sum((c.restart_count or 0) for c in (p.status.container_statuses or []))
                lines.append(f"{p.metadata.name}\t{ready_cnt}/{total_cnt}\t{p.status.phase}\t{restarts}")
            return "\n".join(lines)
        else:
            return f"Resource type '{resource_type}' not supported in serverless inspection fallback. Supported types: pods, deployments, services, events."
    except Exception as e:
        return f"Execution failed across kubernetes inspection: {str(e)}"

_rca_tools = [
    FilteringLazyToolset(lambda: get_mcp_toolset(LOGGING_MCP_SERVER)),
    FilteringLazyToolset(lambda: get_mcp_toolset(MONITORING_MCP_SERVER)),
    FilteringLazyToolset(lambda: get_mcp_toolset(TRACE_MCP_SERVER)),
    FilteringLazyToolset(lambda: get_mcp_toolset(ERROR_REPORTING_MCP_SERVER)),
    FilteringLazyToolset(lambda: get_mcp_toolset(GKE_MCP_SERVER)),
    FilteringLazyToolset(lambda: get_mcp_toolset(COMPUTE_MCP_SERVER)),
    FilteringLazyToolset(lambda: get_mcp_toolset(BQ_MCP_SERVER)),
    skill_toolset.SkillToolset(skills=_RCA_SKILLS),
    remediation_executor_remote,
    remediation_executor_hitl,
    handle_approval,
    get_current_utc_time,
    utcnow,
    list_kubernetes_resources
]

rca_telemetry_expert = Agent(
    name="rca_telemetry_expert",
    model=GlobalGemini(
        model=GEMINI_MODEL,
    ),
    instruction=_RCA_INSTRUCTION,
    tools=_rca_tools,
    before_agent_callback=_hitl_action_interceptor,
)

# =========================================================================
# AGENT 2: The Documentation Compiler (incident_report_writer)
# =========================================================================
_REPORTING_INSTRUCTION = f"""
You are the SRE Incident Report Writer (incident_report_writer), an autonomous asynchronous technical writer and post-mortem expert.

**Persona:** Highly analytical, clear, and structured. 📝🔍
**Target Project:** Always operate within the project `{PROJECT_ID}`.

**Your Job:**
Given diagnostic investigation details or HITL remediation execution outcomes, compile a comprehensive, highly styled Markdown post-mortem report or investigation summary, and archive it to GCS.

**Operating Principles & Reporting Skill Usage:**
1. **Leverage Reporting Skills:** When generating reports for investigations or HITL remediations, ALWAYS load and follow your post-mortem skills:
   - **`postmortem-generator`**: Use to construct rigorous, standardized postmortem documents with timeline reconstruction, root cause analysis, and actionable remediation items.
   - **`postmortem-documentation`**: Use for premium GitHub-style markdown formatting and visual structure.
   - **`postmortem-aggregator`**: Use when synthesizing diagnostic findings from across multiple OneMCP tools, logs, metrics, or previous events.
2. **Premium Markdown Structure:** Build clear reports with incident metadata blocks (`> [!IMPORTANT]`), clean comparison tables, and structured JSON SRE fact blocks at the conclusion.
3. **Archive Documentation:** Use your GCS tools to save the compiled report to Cloud Storage under a unique, timestamped path.
4. **Output Format:** Return the complete compiled Markdown report in your final output along with confirmation of archival.
"""

_reporting_tools = [
    FilteringLazyToolset(lambda: get_mcp_toolset(GCS_MCP_SERVER)),
    skill_toolset.SkillToolset(skills=_REPORTING_SKILLS)
]

incident_report_writer = Agent(
    name="incident_report_writer",
    model=GlobalGemini(
        model=GEMINI_MODEL,
    ),
    instruction=_REPORTING_INSTRUCTION,
    tools=_reporting_tools,
)

from vertexai.preview.reasoning_engines import A2aAgent
from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
from google.adk.runners import Runner

def _get_rca_agent_card():
    from a2a import types as a2a_types
    a2ui_extension = a2a_types.AgentExtension(
        uri="https://a2ui.org/a2a-extension/a2ui/v0.8",
        description="Provides agent driven UI using the A2UI JSON format.",
    )
    return a2a_types.AgentCard(
        name="rca-telemetry-expert",
        description="The SRE RCA Telemetry Expert agent. Performs root-cause analysis, cross-correlates observability signals, and delegates GKE remediation under HITL gating.",
        version="1.0",
        url="https://dummy.com",
        capabilities=a2a_types.AgentCapabilities(
            extensions=[a2ui_extension],
        ),
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        skills=[],
        preferredTransport="HTTP+JSON",
    )

def build_rca_agent():
    import vertexai
    from app.config import PROJECT_ID, GEMINI_MODEL_LOCATION
    # RE framework resets vertexai.global_config before each request;
    # re-init here so model calls use the global endpoint.
    vertexai.init(project=PROJECT_ID, location=GEMINI_MODEL_LOCATION)

    from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
    from google.adk.sessions.in_memory_session_service import InMemorySessionService
    from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
    from google.adk.auth.credential_service.in_memory_credential_service import InMemoryCredentialService
    from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutorConfig
    from a2ui.basic_catalog.provider import BasicCatalog
    from a2ui.schema.manager import A2uiSchemaManager
    from a2ui.adk.a2a.part_converter import A2uiPartConverter

    runner = Runner(
        app_name="rca-telemetry-expert",
        agent=rca_telemetry_expert,
        artifact_service=InMemoryArtifactService(),
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
        credential_service=InMemoryCredentialService(),
    )

    cfg = BasicCatalog.get_config("0.8")
    mgr = A2uiSchemaManager(version="0.8", catalogs=[cfg])
    catalog = mgr.get_selected_catalog()
    a2ui_converter = A2uiPartConverter(catalog, bypass_tool_check=True, version="0.8")
    config = A2aAgentExecutorConfig(gen_ai_part_converter=a2ui_converter.convert)

    return A2aAgentExecutor(runner=runner, config=config, force_new_version=True)

# Expose the pure A2A Agent template for Vertex AI Agent Engine deployment so Agent Registry registers Agent Type: A2A
agent_engine = A2aAgent(
    agent_card=_get_rca_agent_card(),
    agent_executor_builder=build_rca_agent
)
