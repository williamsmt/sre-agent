import os
import sys
import time
import importlib
import inspect
import google.auth
import vertexai
from vertexai._genai import _agent_engines_utils
from vertexai._genai.types import AgentEngine, AgentEngineConfig, IdentityType
from google.cloud import resourcemanager_v3
from google.iam.v1 import iam_policy_pb2, policy_pb2
from google.adk.sessions.in_memory_session_service import InMemorySessionService

class AutoCreatingInMemorySessionService(InMemorySessionService):
    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config = None,
    ):
        session = await super().get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            config=config,
        )
        if not session:
            session = await self.create_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )
        return session

# =========================================================================
# SDK MONKEYPATCH: Fix Pydantic AgentCard Serialization in vertexai SDK
# =========================================================================
from google.protobuf import json_format
from pydantic import BaseModel
import json

original_message_to_json = json_format.MessageToJson

def patched_message_to_json(message, *args, **kwargs):
    if isinstance(message, BaseModel):
        return message.model_dump_json()
    elif isinstance(message, dict):
        return json.dumps(message)
    try:
        return original_message_to_json(message, *args, **kwargs)
    except AttributeError:
        return json.dumps(message)

json_format.MessageToJson = patched_message_to_json
# =========================================================================

# Load environment variables
from dotenv import load_dotenv
load_dotenv(".env")

PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", os.environ.get("GOOGLE_CLOUD_REGION", "us-central1"))
GEMINI_MODEL = (
    os.environ.get("GEMINI_MODEL")
    or os.environ.get("GOOGLE_GENAI_MODEL")
    or os.environ.get("MODEL_NAME")
    or os.environ.get("MODEL_ID")
    or "gemini-3.7-flash"
)

if not PROJECT_ID:
    _, PROJECT_ID = google.auth.default()

# Dynamically compute Project Number, Service Account, and Staging Bucket across any target project
try:
    proj_client = resourcemanager_v3.ProjectsClient()
    project_res = proj_client.get_project(name=f"projects/{PROJECT_ID}")
    PROJECT_NUMBER = project_res.name.split("/")[-1]
except Exception:
    PROJECT_NUMBER = os.environ.get("PROJECT_NUMBER") or os.environ.get("GOOGLE_CLOUD_PROJECT_NUMBER", "000000000000")

DEFAULT_SERVICE_ACCOUNT = os.environ.get("REASONING_ENGINE_SERVICE_ACCOUNT", f"{PROJECT_NUMBER}-compute@developer.gserviceaccount.com")
STAGING_BUCKET = os.environ.get("STAGING_BUCKET", f"gs://{PROJECT_ID}-telemetry")

print(f"Using Project: {PROJECT_ID} (Number: {PROJECT_NUMBER})")
print(f"Using Location: {LOCATION}")
print(f"Using Service Account: {DEFAULT_SERVICE_ACCOUNT}")
print(f"Using Staging Bucket: {STAGING_BUCKET}")

from vertexai import agent_engines
from vertexai.preview import reasoning_engines

def grant_iam_roles(sa_email: str = None, effective_identity: str = None):
    if not sa_email and not effective_identity:
        print("⚠️ No service account or effective identity found to configure IAM roles.")
        return
    print(f"🔐 Configuring IAM roles for SA: {sa_email}, Identity: {effective_identity}...")
    roles = [
        "roles/aiplatform.user",
        "roles/serviceusage.serviceUsageConsumer",
        "roles/browser",
        "roles/cloudapiregistry.viewer",
        "roles/agentregistry.viewer",
        "roles/logging.logWriter",
        "roles/monitoring.metricWriter",
        "roles/logging.viewer",
        "roles/monitoring.viewer",
        "roles/cloudtrace.viewer",
        "roles/cloudtrace.agent",
        "roles/errorreporting.viewer",
        "roles/mcp.toolUser",
        "roles/container.developer",
        "roles/bigquery.admin",
        "roles/storage.admin",
        "roles/storage.objectAdmin",
        "roles/iap.egressor",  # Grant IAP Egressor role for A2A routing through Agent Gateway
    ]
    
    proj_client = resourcemanager_v3.ProjectsClient()
    policy = proj_client.get_iam_policy(
        request={
            "resource": f"projects/{PROJECT_ID}"
        }
    )
    
    members = []
    if sa_email:
        members.append(f"serviceAccount:{sa_email}")
    if effective_identity:
        if "@" in effective_identity:
            member = f"serviceAccount:{effective_identity}"
        else:
            member = f"principal://{effective_identity}"
        if member not in members:
            members.append(member)
        
    for role in roles:
        binding = next((b for b in policy.bindings if b.role == role), None)
        if binding:
            for member in members:
                if member not in binding.members:
                    binding.members.append(member)
        else:
            policy.bindings.append(
                policy_pb2.Binding(role=role, members=members)
            )
            
    proj_client.set_iam_policy(
        request={
            "resource": f"projects/{PROJECT_ID}",
            "policy": policy
        }
    )
    print("  ✅ IAM roles configured successfully")

def deploy_agent(display_name: str, module_name: str, entrypoint_object: str, env_vars: dict = None):
    print(f"\n🚀 Deploying {display_name}...")
    vertexai.init(project=PROJECT_ID, location=LOCATION, staging_bucket=STAGING_BUCKET)
    
    # Dynamically import the agent/app instance
    sys.path.insert(0, os.getcwd())
    module = importlib.import_module(module_name)
    agent_instance = getattr(module, entrypoint_object)
    
    # Setup environment variables
    merged_env_vars = {
        "GOOGLE_CLOUD_REGION": LOCATION,
        "GCP_PROJECT_ID": PROJECT_ID,
        "GEMINI_MODEL": GEMINI_MODEL,
        "GOOGLE_GENAI_USE_VERTEXAI": "1",
        "GOOGLE_GENAI_LOCATION": "global",
        "GOOGLE_API_USE_CLIENT_CERTIFICATE": "false",
        "GOOGLE_API_USE_MTLS_ENDPOINT": "never",
        "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
        "OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED": "true",
        "OTEL_INSTRUMENTATION_A2A_SDK_ENABLED": "false",
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "SPAN_AND_EVENT",
        "OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH": STAGING_BUCKET,
        "OTEL_INSTRUMENTATION_GENAI_UPLOAD_FORMAT": "jsonl",
        "OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK": "upload",
        "OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_latest_experimental",
    }
    if env_vars:
        merged_env_vars.update(env_vars)
        
    # Wrap in AdkApp if not already wrapped (either AdkApp or A2aAgent) to avoid double-wrapping bugs
    from vertexai.preview.reasoning_engines import A2aAgent
    from google.adk.sessions.in_memory_session_service import InMemorySessionService
    if isinstance(agent_instance, (agent_engines.AdkApp, A2aAgent)):
        app = agent_instance
        if isinstance(app, agent_engines.AdkApp):
            app._tmpl_attrs["enable_tracing"] = True
            app._tmpl_attrs["session_service_builder"] = lambda **kwargs: InMemorySessionService()
    else:
        app = agent_engines.AdkApp(
            agent=agent_instance,
            enable_tracing=True,
            session_service_builder=lambda **kwargs: InMemorySessionService(),
        )
    
    # Check if agent already exists
    existing = list(reasoning_engines.ReasoningEngine.list())
    matching = [a for a in existing if a.display_name == display_name]
    if matching:
        print(f"Agent {display_name} already exists. Deleting it first to avoid update blocks...")
        try:
            matching[0].delete()
            print("Deletion request sent. Waiting 5 seconds for cleanup...")
            time.sleep(5)
        except Exception as e:
            print(f"Warning: Failed to delete agent: {e}")
            
    # Deploy using high-level agent_engines.create
    remote_app = agent_engines.create(
        agent_engine=app,
        display_name=display_name,
        gcs_dir_name=display_name,  # Isolate staging paths to prevent GCS pickle collisions!
        requirements=[
            "google-cloud-aiplatform[agent_engines]==1.156.0",
            "google-adk[a2a,agent-identity]==2.2.0",
            "a2a-sdk==0.3.26",
            "protobuf==6.33.6",
            "cloudpickle==3.1.2",
            "pydantic==2.13.4",
            "google-genai==2.8.0",
            "python-dotenv==1.2.2",
            "httpx==0.28.1",
            "google-auth==2.53.0",
            "opentelemetry-sdk==1.41.1",
            "opentelemetry-exporter-gcp-trace==1.12.0",
            "opentelemetry-instrumentation-google-genai",
            "opentelemetry-instrumentation-httpx",
            "opentelemetry-instrumentation-grpc",
            "kubernetes==36.0.2",
            "google-cloud-container==2.65.0",
            "google-cloud-privilegedaccessmanager==0.4.1",
            "a2ui-agent-sdk>=0.6.0",
            "requests>=2.31.0",
            "fastapi>=0.110.0",
            "uvicorn>=0.28.0",
            "mcp==1.27.2"
        ],
        extra_packages=["./app"],
        service_account=DEFAULT_SERVICE_ACCOUNT,
        env_vars=merged_env_vars
    )
    
    print(f"✅ Deployment completed. Agent URN: {remote_app.resource_name}")
    return remote_app

def main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "inv":
        inv_app = deploy_agent(display_name="rca-telemetry-expert", module_name="app.investigator_agent", entrypoint_object="agent_engine")
        print(f"\n🚀 Newly deployed Investigator Agent URN: {inv_app.resource_name}")
        try:
            import subprocess, shutil
            gcloud_bin = shutil.which("gcloud") or "/usr/local/google/home/madhavikarra/google-cloud-sdk/bin/gcloud"
            print("\n⚡ Automatically syncing fresh Investigator URN to live Cloud Run service 'novasre-control-room'...")
            subprocess.run([
                gcloud_bin, "run", "services", "update", "novasre-control-room",
                "--region", LOCATION,
                "--project", PROJECT_ID,
                f"--update-env-vars=INVESTIGATOR_AGENT_URN={inv_app.resource_name},GEMINI_MODEL={GEMINI_MODEL},GOOGLE_GENAI_LOCATION=global,GOOGLE_API_USE_CLIENT_CERTIFICATE=false,GOOGLE_API_USE_MTLS_ENDPOINT=never"
            ], check=True)
            print("✅ Cloud Run service updated successfully with new investigator URN!")
        except Exception as e:
            print(f"⚠️ Could not auto-update Cloud Run env var: {e}")
        return

    agents_to_deploy = [
        ("remediation-executor", "app.remediation_agent", "agent_engine"),
        ("outage-simulator", "app.outage_simulator_agent", "agent_engine"),
        ("rca-telemetry-expert", "app.investigator_agent", "agent_engine"),
    ]
    
    deployed_urns = {}
    print("\n⚡ Deploying agents sequentially to Vertex AI Reasoning Engines...")
    for display_name, module_name, entrypoint in agents_to_deploy:
        success = False
        for attempt in range(1, 4):
            try:
                print(f"Deploying {display_name} (Attempt {attempt}/3)...")
                remote_app = deploy_agent(display_name, module_name, entrypoint)
                deployed_urns[display_name] = remote_app.resource_name
                success = True
                break
            except Exception as e:
                print(f"⚠️ Attempt {attempt} failed for {display_name}: {e}")
                time.sleep(5)
        if not success:
            print(f"❌ Failed to deploy {display_name} after 3 attempts.")
            sys.exit(1)
            
    print(f"\n🚀 Newly deployed GKE Remediation Agent URN: {deployed_urns.get('remediation-executor')}")
    print(f"\n🚀 Newly deployed Outage Simulator URN: {deployed_urns.get('outage-simulator')}")
    print(f"\n🚀 Newly deployed Investigator Agent URN: {deployed_urns.get('rca-telemetry-expert')}")
    print("\n🎉 ALL 3 AGENTS DEPLOYED SUCCESSFULLY TO VERTEX AI REASONING ENGINES!")
    
    # Configure IAM roles sequentially
    grant_iam_roles(DEFAULT_SERVICE_ACCOUNT, None)

    # Automatically sync live Cloud Run environment variables to match these fresh URNs right now!
    try:
        import subprocess, shutil
        gcloud_bin = shutil.which("gcloud") or "/usr/local/google/home/madhavikarra/google-cloud-sdk/bin/gcloud"
        print("\n⚡ Automatically syncing fresh Reasoning Engine URNs to live Cloud Run service 'novasre-control-room'...")
        subprocess.run([
            gcloud_bin, "run", "services", "update", "novasre-control-room",
            "--region", LOCATION,
            "--project", PROJECT_ID,
            f"--update-env-vars=REMEDIATION_AGENT_URN={deployed_urns.get('remediation-executor')},OUTAGE_SIMULATOR_URN={deployed_urns.get('outage-simulator')},INVESTIGATOR_AGENT_URN={deployed_urns.get('rca-telemetry-expert')},GEMINI_MODEL={GEMINI_MODEL},GOOGLE_GENAI_LOCATION=global,GOOGLE_API_USE_CLIENT_CERTIFICATE=false,GOOGLE_API_USE_MTLS_ENDPOINT=never"
        ], check=True)
        print("✅ Cloud Run service 'novasre-control-room' updated successfully with new agent URNs!")
    except Exception as e:
        print(f"⚠️ Could not auto-update Cloud Run env vars (please check permissions or update manually): {e}")

if __name__ == "__main__":
    main()

