import asyncio
import sys
import os
import argparse
import vertexai
from vertexai.preview import reasoning_engines
from dotenv import load_dotenv

load_dotenv(".env")

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "sre-agent-1780845375")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

# URNs of the deployed agents
INVESTIGATOR_URN = f"projects/787608666447/locations/{LOCATION}/reasoningEngines/1070949064465448960"
REMEDIATION_URN = f"projects/787608666447/locations/{LOCATION}/reasoningEngines/2971468107215798272"

def main():
    parser = argparse.ArgumentParser(description="End-to-End Test Runner for 2-Agent SRE Platform")
    parser.add_argument("--pod", type=str, required=True, 
                        help="The GKE pod name to target (e.g. frontend-7d9888cf5d-abcde)")
    parser.add_argument("--namespace", type=str, default="default", 
                        help="The GKE namespace of the pod (default: 'default')")
    parser.add_argument("--alert-type", type=str, choices=["oom", "latency", "fake"], default="oom",
                        help="The type of alert simulation to run: 'oom', 'latency', or 'fake' (for failure path)")
    args = parser.parse_args()

    print("=" * 80)
    print("STARTING END-TO-END SRE PLATFORM VALIDATION")
    print("=" * 80)
    print(f"Target Project:   {PROJECT_ID}")
    print(f"Target Region:    {LOCATION}")
    print(f"Target Pod:       {args.pod} (Namespace: {args.namespace})")
    print(f"Simulation Type:  {args.alert_type.upper()}")
    print("-" * 80)

    # Initialize Vertex AI
    vertexai.init(project=PROJECT_ID, location=LOCATION)

    # 1. Formulate the alert based on simulation type
    if args.alert_type == "oom":
        alert_msg = f"GKE Pod {args.pod} in namespace {args.namespace} is crashing with OOMKilled"
    elif args.alert_type == "latency":
        alert_msg = f"GKE Pod {args.pod} in namespace {args.namespace} is experiencing elevated HTTP 5xx latency anomalies"
    else:
        alert_msg = f"GKE Pod {args.pod} in namespace {args.namespace} is reporting a bad disk state"

    print(f"🚨 Ingesting Simulated Alert:\n   -> '{alert_msg}'\n")

    # 2. Connect to the Investigator Agent in the cloud
    print(f"Connecting to SRE Investigator Agent URN:\n   -> {INVESTIGATOR_URN}...")
    try:
        investigator = reasoning_engines.ReasoningEngine(INVESTIGATOR_URN)
    except Exception as e:
        print(f"❌ Failed to connect to Investigator Agent: {e}")
        sys.exit(1)

    # 3. Query the Investigator Agent (this triggers the E2E A2A flow!)
    print("\n⚡ Querying Investigator Agent. Running live telemetry analysis and A2A remediation...")
    print("   (This will take 30-90 seconds as it queries logs/metrics and delegates via Agent Gateway)\n")
    
    try:
        response = investigator.query(
            message=alert_msg,
            user_id="sre-test-operator",
            session_id=f"test-session-{args.alert_type}"
        )
        
        print("=" * 80)
        print("🏆 PLATFORM EXECUTION RESPONSE RECEIVER")
        print("=" * 80)
        print(response)
        print("=" * 80)
        print("\n✅ End-to-End run completed. Please verify the pod status in your GKE cluster!")
        
    except Exception as e:
        print(f"\n❌ Execution failed with error: {e}")
        print("Check the cloud logs for details on where the connection failed.")

if __name__ == "__main__":
    main()
