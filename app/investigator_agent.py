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
from google.adk.tools.base_toolset import BaseToolset
from dotenv import load_dotenv

# Load environment variables & trigger centralized runtime patches from config
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from app.config import (
    PROJECT_ID,
    GEMINI_LOCATION,
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
    get_mcp_toolset
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
   Once your diagnostic skill inspection confirms the specific failure state, load the corresponding SRE playbook:
   * **Playbook 1 (`gke-scale-recovery`)**: If `readyReplicas = 0` on `frontend` (or any deployment), invoke `load_skill(skill_name="gke-scale-recovery")` and automatically invoke `remediation_executor_remote` with parameter `request="scale deployment frontend in namespace default to 1 replica in cluster online-boutique in region us-central1"` (`Tier 1 Auto-Recovery`).
   * **Playbook 2 (`gke-crashloop-rollback`)**: If `cartservice` container rollout fails (`CrashLoopBackOff` / `ErrImagePull`), invoke `load_skill(skill_name="gke-crashloop-rollback")` and automatically invoke `remediation_executor_remote` with parameter `request="Revert GKE Deployment 'cartservice' in namespace 'default' in cluster 'online-boutique' in region 'us-central1' to its previous stable container image revision (gcr.io/google-samples/microservices-demo/cartservice:v1.0.4) and verify replacement pods transition to a healthy Ready state."` (`Tier 1 Auto-Recovery`).
   * **Playbook 3 (`gke-pod-restart`)**: If `redis-cart` database locks or pod termination occur, invoke `load_skill(skill_name="gke-pod-restart")` and present the recommended pod restart plan to the human operator under `Tier 2 (HITL Approval Required)`.
   * **Playbook 4 (`gke-horizontal-upsize`)**: If `paymentservice` transaction latency (>2000ms) or capacity bottleneck occurs, invoke `load_skill(skill_name="gke-horizontal-upsize")` and present the recommended horizontal upsize plan (`scale deployment paymentservice in namespace default to 3 replicas`) to the human operator under `Tier 2 (HITL Approval Required)`.
   * **Playbook 5 (`gke-service-routing-recovery`)**: If GKE service routing to a microservice is broken due to incorrect service selectors, invoke `load_skill(skill_name="gke-service-routing-recovery")` and present the recommended selector restoration plan under `Tier 2 (HITL Approval Required)`.
   * **Playbook 6 (`gke-dns-recovery`)**: If CoreDNS domain resolution failures occur, invoke `load_skill(skill_name="gke-dns-recovery")` and present the recommended CoreDNS recovery plan under `Tier 2 (HITL Approval Required)`.
   * **Playbook 7 (`gke-network-firewall-recovery`)**: If firewall rules or NetworkPolicies block required traffic, invoke `load_skill(skill_name="gke-network-firewall-recovery")` and present the recommended firewall remediation plan under `Tier 2 (HITL Approval Required)`.
   * **Playbook 8 (`gcp-nat-port-recovery`)**: If Cloud NAT SNAT port exhaustion occurs, invoke `load_skill(skill_name="gcp-nat-port-recovery")` and present the recommended port scaling plan under `Tier 2 (HITL Approval Required)`.

4. **Step 4: Consult Developer Knowledge (Tier 2 - Dynamic RAG - HITL Required)**:
   If no matching local playbook is found under Step 3, search the `gcp_developer_knowledge` MCP server (if available) to retrieve the relevant guide.
   * If a runbook is retrieved: Present the plan to the human operator in the chat and **explicitly ask for approval** (*"I have retrieved this remediation plan under Tier 2 (RAG): [PLAN]. Do you approve? (Please reply with 'APPROVE' to execute)"*). Do NOT execute until approved.

5. **Step 5: LLM Zero-Shot Fallback (Tier 3 - LLM Reasoning - HITL Required)**:
   If no playbook or runbook is found in the previous steps, use your internal LLM SRE knowledge to formulate a suggested plan.
   * Present the plan to the human operator in the chat and **explicitly ask for approval** (*"I have formulated this remediation plan under Tier 3 (LLM Fallback): [PLAN]. Do you approve? (Please reply with 'APPROVE' to execute)"*). Do NOT execute until approved.

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

async def remediation_executor_remote(request: str) -> str:
    """The GKE Remediation Executor agent. Use this tool to delegate approved GKE remediations and rollback actions.

    Args:
        request: The SRE instruction describing the GKE remediation or rollback action to execute (e.g. "scale deployment frontend in namespace default to 1 replica").

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
    
    # Initialize Vertex AI with regional endpoint
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
    
    session.events.append(ADKEvent(
        author="user",
        content=genai_types.Content(parts=[genai_types.Part(text=request)]),
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
    FilteringLazyToolset(lambda: get_mcp_toolset(GCS_MCP_SERVER)),
    skill_toolset.SkillToolset(skills=_RCA_SKILLS),
    remediation_executor_remote,
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

from vertexai.agent_engines.templates.adk import AdkApp
from google.adk.sessions.in_memory_session_service import InMemorySessionService

# Expose the ADK wrapped Agent Engine app for Vertex AI deployment
agent_engine = AdkApp(
    agent=rca_telemetry_expert,
    app_name="investigator_agent_app",
    enable_tracing=True,
    session_service_builder=lambda **kwargs: InMemorySessionService(),
)
