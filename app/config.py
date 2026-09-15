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
import logging
import google.auth
import google.auth.transport.requests
from dotenv import load_dotenv
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.base_toolset import BaseToolset

logger = logging.getLogger(__name__)

# Load local environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# =========================================================================
# OPENTELEMETRY CLOUD TRACE & GENAI INSTRUMENTATION SETUP (ce408 pattern)
# =========================================================================
try:
    from google.adk.telemetry.google_cloud import get_gcp_exporters, get_gcp_resource
    from google.adk.telemetry.setup import maybe_set_otel_providers

    os.environ.setdefault("GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", "true")
    os.environ.setdefault("OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED", "true")
    os.environ.setdefault("OTEL_INSTRUMENTATION_A2A_SDK_ENABLED", "false")

    _credentials, _project_id = google.auth.default()
    _target_proj = _project_id or os.environ.get("GCP_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    _otel_hooks = get_gcp_exporters(
        enable_cloud_tracing=True,
        enable_cloud_metrics=False,
        enable_cloud_logging=True,
        google_auth=(_credentials, _target_proj),
    )
    _otel_resource = get_gcp_resource(_target_proj)
    maybe_set_otel_providers(
        otel_hooks_to_setup=[_otel_hooks],
        otel_resource=_otel_resource,
    )
    try:
        from opentelemetry.instrumentation.google_genai import GoogleGenAiInstrumentor
        GoogleGenAiInstrumentor().instrument()
    except Exception:
        pass
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    except Exception:
        pass
    try:
        from opentelemetry.instrumentation.grpc import GrpcInstrumentorClient
        GrpcInstrumentorClient().instrument()
    except Exception:
        pass
    logger.info("✅ OpenTelemetry Cloud Trace & GenAI instrumentation initialized.")
except Exception as _otel_err:
    logger.warning(f"⚠️ Could not initialize OpenTelemetry Cloud Trace: {_otel_err}")

# =========================================================================
# 1. CENTRALIZED RUNTIME MONKEYPATCHES & FRAMEWORK COMPATIBILITY
# =========================================================================
try:
    # Relax session_id validation in google-adk to allow slashes
    import google.adk.sessions.vertex_ai_session_service as service_module
    def relaxed_validate_session_id(session_id: str) -> None:
        if not isinstance(session_id, str):
            raise ValueError(f"Invalid session_id: {session_id}")
    service_module._validate_session_id = relaxed_validate_session_id
except Exception:
    pass

try:
    # Strip forbidden 'part_metadata' parameter from GenAI Parts when serializing A2A parts
    import google.adk.a2a.converters.part_converter as part_converter
    original_convert_a2a_part_to_genai_part = part_converter.convert_a2a_part_to_genai_part

    def patched_convert_a2a_part_to_genai_part(a2a_part):
        genai_part = original_convert_a2a_part_to_genai_part(a2a_part)
        if genai_part is not None and hasattr(genai_part, "part_metadata"):
            genai_part.part_metadata = None
        return genai_part

    part_converter.convert_a2a_part_to_genai_part = patched_convert_a2a_part_to_genai_part
except Exception:
    pass

try:
    # Fix google-cloud-aiplatform / a2a-sdk import mismatch for RemoteA2aAgent
    import types
    import a2a.types
    import a2a.utils.constants as constants_module
    
    constants_module.TransportProtocol = a2a.types.TransportProtocol
    constants_module.PROTOCOL_VERSION_CURRENT = '0.3.0'
    
    def get_supported_interfaces(self):
        return self.additional_interfaces
    def set_supported_interfaces(self, value):
        self.additional_interfaces = value
    a2a.types.AgentCard.supported_interfaces = property(get_supported_interfaces, set_supported_interfaces)
    
    a2a.types.TransportProtocol.HTTP_JSON = a2a.types.TransportProtocol.http_json
    a2a.types.TransportProtocol.JSONRPC = a2a.types.TransportProtocol.jsonrpc
    a2a.types.TransportProtocol.GRPC = a2a.types.TransportProtocol.grpc
    
    original_agent_interface_init = a2a.types.AgentInterface.__init__
    def patched_agent_interface_init(self, *args, **kwargs):
        if "protocol_binding" in kwargs:
            binding = kwargs.pop("protocol_binding")
            kwargs["transport"] = getattr(binding, "value", str(binding))
        if "protocol_version" in kwargs:
            kwargs.pop("protocol_version")
        if "url" not in kwargs:
            kwargs["url"] = "https://dummy-interface.com"
        original_agent_interface_init(self, *args, **kwargs)
    a2a.types.AgentInterface.__init__ = patched_agent_interface_init
    
    original_client_factory_create = a2a.client.ClientFactory.create
    def patched_client_factory_create(self, agent_card, *args, **kwargs):
        if hasattr(agent_card, "supported_interfaces") and agent_card.supported_interfaces:
            agent_card.url = agent_card.supported_interfaces[0].url
        return original_client_factory_create(self, agent_card, *args, **kwargs)
    a2a.client.ClientFactory.create = patched_client_factory_create
    
    import httpx
    original_async_client_init = httpx.AsyncClient.__init__
    def patched_async_client_init(self, *args, **kwargs):
        if "timeout" not in kwargs:
            kwargs["timeout"] = 60.0
        original_async_client_init(self, *args, **kwargs)
    httpx.AsyncClient.__init__ = patched_async_client_init
except Exception:
    pass

try:
    # OpenTelemetry trace cleaning: Custom Noise-Filtering Sampler
    from opentelemetry.sdk.trace.sampling import Sampler, Decision, SamplingResult
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from google.adk.agents import Agent

    class NoiseFilteringSampler(Sampler):
        """Custom sampler to filter out low-level framework noise from Cloud Trace."""
        def should_sample(self, parent_context, trace_id, name, *args, **kwargs):
            if name.startswith("a2a.server.events.") or name.startswith("a2a.server.request_handlers."):
                return SamplingResult(decision=Decision.DROP)
            if name.startswith("google.adk.sessions."):
                return SamplingResult(decision=Decision.DROP)
            return SamplingResult(decision=Decision.RECORD_AND_SAMPLE)

        def get_description(self):
            return "NoiseFilteringSampler"

    provider = trace.get_tracer_provider()
    if hasattr(provider, "_tracer_provider"):
        provider = provider._tracer_provider
    if isinstance(provider, TracerProvider):
        provider._sampler = NoiseFilteringSampler()

    original_run_async_impl = Agent._run_async_impl
    async def patched_run_async_impl(self, *args, **kwargs):
        try:
            p = trace.get_tracer_provider()
            if hasattr(p, "_tracer_provider"):
                p = p._tracer_provider
            if isinstance(p, TracerProvider):
                p._sampler = NoiseFilteringSampler()
        except Exception:
            pass
        async for event in original_run_async_impl(self, *args, **kwargs):
            yield event
    Agent._run_async_impl = patched_run_async_impl
except Exception:
    pass

# =========================================================================
# 2. DYNAMIC PROJECT AND MODEL RESOLUTION
# =========================================================================
try:
    _, default_project_id = google.auth.default()
except Exception:
    default_project_id = None

PROJECT_ID = os.environ.get(
    "GCP_PROJECT_ID", 
    os.environ.get("GOOGLE_CLOUD_PROJECT", default_project_id)
)
if PROJECT_ID:
    os.environ["GOOGLE_CLOUD_PROJECT"] = str(PROJECT_ID)

os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
os.environ.setdefault("GOOGLE_API_USE_CLIENT_CERTIFICATE", "false")
os.environ.setdefault("GOOGLE_API_USE_MTLS_ENDPOINT", "never")

# Support generic environment variables for Gemini model selection
GEMINI_MODEL = (
    os.environ.get("GEMINI_MODEL")
    or os.environ.get("GOOGLE_GENAI_MODEL")
    or os.environ.get("MODEL_NAME")
    or os.environ.get("MODEL_ID")
    or "gemini-3.7-flash"
)
os.environ["GEMINI_MODEL"] = str(GEMINI_MODEL)
GEMINI_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", os.environ.get("GOOGLE_CLOUD_REGION", "us-central1"))
# Separate location for model inference (agent A2A code overrides GOOGLE_CLOUD_LOCATION
# to the deployment region; model calls must stay on the global endpoint).
GEMINI_MODEL_LOCATION = os.environ.get("GEMINI_MODEL_LOCATION", "global")
GKE_CLUSTER_NAME = os.environ.get("GKE_CLUSTER_NAME", "online-boutique")
GKE_CLUSTER_REGION = os.environ.get("GKE_CLUSTER_REGION", GEMINI_LOCATION)

from google.adk.models import Gemini
from google.genai import Client, types

class GlobalGemini(Gemini):
    """Gemini model routed directly to Vertex AI global publisher models with per-request event loop isolation and exponential retry backoff."""
    @property
    def api_client(self) -> Client:
        return Client(
            vertexai=True,
            location=GEMINI_MODEL_LOCATION,
            project=PROJECT_ID,
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(
                    attempts=5,
                    initial_delay=2.0,
                    max_delay=60.0,
                    exp_base=2.0,
                    http_status_codes=[408, 429, 500, 502, 503, 504]
                )
            )
        )


# =========================================================================
# 3. CENTRALIZED LAZY TOOLSET & DYNAMIC ONEMCP FACTORY
# =========================================================================
class LazyToolset(BaseToolset):
    """Helper to lazily load and resolve MCP tools at runtime."""
    def __init__(self, toolset_fn):
        super().__init__()
        self._toolset_fn = toolset_fn
        self._toolset = None

    async def get_tools(self, readonly_context=None):
        if self._toolset is None:
            self._toolset = self._toolset_fn()
        import inspect
        if inspect.iscoroutinefunction(self._toolset.get_tools):
            return await self._toolset.get_tools(readonly_context)
        return self._toolset.get_tools(readonly_context)

_toolset_cache = {}

def get_mcp_toolset(url_or_urn: str):
    """
    Returns an MCPToolset connected to Google's OneMCP server over HTTP+SSE with dynamic token refresh.
    If passed a full URN starting with 'projects/', delegates to AgentRegistry.
    Otherwise connects directly to the global OneMCP HTTP endpoint with automatic header injection.
    """
    if url_or_urn.startswith("projects/"):
        from google.adk.integrations.agent_registry import AgentRegistry
        registry = AgentRegistry(project_id=PROJECT_ID, location=GEMINI_LOCATION)
        return registry.get_mcp_toolset(url_or_urn)

    if url_or_urn in _toolset_cache:
        return _toolset_cache[url_or_urn]

    logger.info("[OneMCP] Initializing dynamic connection to %s for project %s", url_or_urn, PROJECT_ID)

    def dynamic_header_provider(context=None) -> dict:
        try:
            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            credentials.refresh(google.auth.transport.requests.Request())
            headers = {
                "Authorization": f"Bearer {credentials.token}"
            }
            if PROJECT_ID:
                headers["x-goog-user-project"] = str(PROJECT_ID)
            return headers
        except Exception as e:
            logger.error("❌ Failed to refresh OAuth token for OneMCP %s: %s", url_or_urn, e)
            return {}

    toolset = MCPToolset(
        connection_params=StreamableHTTPConnectionParams(url=url_or_urn),
        header_provider=dynamic_header_provider
    )
    _toolset_cache[url_or_urn] = toolset
    return toolset

# Universal, project-agnostic OneMCP endpoints
LOGGING_MCP_SERVER = os.environ.get("LOGGING_MCP_URL", "https://logging.googleapis.com/mcp")
MONITORING_MCP_SERVER = os.environ.get("MONITORING_MCP_URL", "https://monitoring.googleapis.com/mcp")
TRACE_MCP_SERVER = os.environ.get("TRACE_MCP_URL", "https://cloudtrace.googleapis.com/mcp")
ERROR_REPORTING_MCP_SERVER = os.environ.get("ERROR_REPORTING_MCP_URL", "https://clouderrorreporting.googleapis.com/mcp")
GKE_MCP_SERVER = os.environ.get("GKE_MCP_URL", "https://container.googleapis.com/mcp")
COMPUTE_MCP_SERVER = os.environ.get("COMPUTE_MCP_URL", "https://compute.googleapis.com/mcp")
GCS_MCP_SERVER = os.environ.get("GCS_MCP_URL", "https://storage.googleapis.com/mcp")
BQ_MCP_SERVER = os.environ.get("BQ_MCP_URL", "https://bigquery.googleapis.com/mcp")
