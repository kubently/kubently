#!/usr/bin/env python3
"""
Test runner that actually calls Kubently API for debugging
"""

import json
import subprocess
import sys
import time
import os
import uuid
from datetime import datetime
from pathlib import Path

# Try to import httpx, fall back to urllib if not available
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    import urllib.request
    import urllib.error
    HAS_HTTPX = False

def print_status(msg, status="info"):
    """Print colored status messages."""
    colors = {
        "info": "\033[94m",
        "success": "\033[92m", 
        "warning": "\033[93m",
        "error": "\033[91m",
    }
    reset = "\033[0m"
    print(f"{colors.get(status, '')}[{status.upper()}] {msg}{reset}")

def call_kubently_api(api_url, api_key, query, namespace):
    """Call Kubently API via A2A protocol."""
    print_status(f"Calling Kubently API: {api_url}", "info")
    
    # Prepare A2A request
    request_data = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "user",
                "parts": [{"text": f"[namespace: {namespace}] {query}"}]
            },
            "contextId": str(uuid.uuid4()),
        }
    }
    
    if HAS_HTTPX:
        # Use httpx if available
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    api_url,
                    json=request_data,
                    headers={
                        "X-API-Key": api_key,
                        "Content-Type": "application/json",
                    }
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            print_status(f"API call failed: {e}", "error")
            return {"error": str(e)}
    else:
        # Fallback to urllib
        try:
            req = urllib.request.Request(
                api_url,
                data=json.dumps(request_data).encode('utf-8'),
                headers={
                    "X-API-Key": api_key,
                    "Content-Type": "application/json",
                }
            )
            
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as e:
            print_status(f"API call failed: {e}", "error")
            return {"error": str(e)}

def extract_response_text(api_response):
    """Extract text from A2A API response."""
    if "error" in api_response:
        return f"Error: {api_response['error']}"
    
    if "result" in api_response:
        result = api_response["result"]
        
        # Try to extract text from artifacts
        if isinstance(result, dict) and "artifacts" in result:
            texts = []
            for artifact in result.get("artifacts", []):
                if "parts" in artifact:
                    for part in artifact["parts"]:
                        if "text" in part:
                            texts.append(part["text"])
            if texts:
                return "\n".join(texts)
        
        # Try status message
        if isinstance(result, dict) and "status" in result:
            status = result["status"]
            if "message" in status and "parts" in status["message"]:
                texts = []
                for part in status["message"]["parts"]:
                    if "text" in part:
                        texts.append(part["text"])
                if texts:
                    return "\n".join(texts)
        
        # Return raw result if can't extract
        return str(result)
    
    return "No response text found"

def apply_scenario_clean(scenario_path):
    """Apply scenario without watch commands."""
    print_status(f"Applying scenario: {scenario_path.name}")
    
    # Read and filter script
    with open(scenario_path, 'r') as f:
        lines = f.readlines()
    
    # Remove watch commands
    filtered = []
    for line in lines:
        if 'kubectl' in line and '-w' in line:
            continue
        filtered.append(line)
    
    # Write temp script
    temp_script = Path("/tmp/test_scenario.sh")
    with open(temp_script, 'w') as f:
        f.writelines(filtered)
    
    os.chmod(temp_script, 0o755)
    
    try:
        result = subprocess.run(
            ["bash", str(temp_script)],
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode == 0, result.stdout
    except:
        return False, "Failed to apply"
    finally:
        if temp_script.exists():
            temp_script.unlink()

def get_pod_status(namespace):
    """Get pod status in namespace."""
    try:
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", namespace, "-o", "json"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if data.get("items"):
                pod = data["items"][0]
                pod_name = pod["metadata"]["name"]
                
                # Check container status
                for status in pod["status"].get("containerStatuses", []):
                    state = status.get("state", {})
                    if "waiting" in state:
                        return {
                            "pod_name": pod_name,
                            "issue": state["waiting"].get("reason", "Unknown"),
                            "message": state["waiting"].get("message", "")
                        }
                
                return {
                    "pod_name": pod_name,
                    "phase": pod["status"].get("phase", "Unknown")
                }
        
        return None
    except:
        return None

def cleanup_namespace(namespace):
    """Clean up namespace."""
    try:
        subprocess.run(
            ["kubectl", "delete", "namespace", namespace, "--ignore-not-found=true"],
            capture_output=True,
            text=True,
            timeout=30
        )
        return True
    except:
        return False

def main():
    # Get configuration
    api_url = os.getenv("KUBENTLY_API_URL", "http://localhost:8001")
    api_key = os.getenv("KUBENTLY_API_KEY", "")
    
    if not api_key:
        print_status("KUBENTLY_API_KEY not set", "error")
        print("Please set: export KUBENTLY_API_KEY=your-key")
        sys.exit(1)
    
    if len(sys.argv) < 2:
        print("Usage: python3 test_with_api.py <scenario_number>")
        sys.exit(1)
    
    scenario_num = sys.argv[1]
    
    # Find scenario
    scenario_dir = Path("/Users/adickinson/repos/kubently/test-scenarios")
    scenario_files = list(scenario_dir.glob(f"{scenario_num}-*.sh"))
    
    if not scenario_files:
        print_status(f"No scenario found: {scenario_num}", "error")
        sys.exit(1)
    
    scenario_path = scenario_files[0]
    scenario_name = scenario_path.stem
    namespace = f"test-scenario-{scenario_num}"
    
    print("="*60)
    print(f"TESTING: {scenario_name}")
    print(f"API: {api_url}")
    print("="*60)
    
    results = {
        "scenario": scenario_name,
        "timestamp": datetime.now().isoformat(),
        "api_url": api_url,
        "namespace": namespace,
        "steps": []
    }
    
    # Step 1: Apply scenario
    success, output = apply_scenario_clean(scenario_path)
    if not success:
        print_status("Failed to apply scenario", "error")
        sys.exit(1)
    
    print_status("Scenario applied", "success")
    results["steps"].append({"name": "apply", "success": True})
    
    # Step 2: Wait and check status
    print_status("Waiting for pod issues to manifest...", "info")
    time.sleep(8)
    
    pod_status = get_pod_status(namespace)
    if pod_status:
        print_status(f"Pod: {pod_status.get('pod_name')}", "info")
        
        if "issue" in pod_status:
            print_status(f"Issue detected: {pod_status['issue']}", "warning")
            results["steps"].append({
                "name": "detect_issue",
                "success": True,
                "issue": pod_status
            })
            
            # Step 3: Call Kubently API
            query = f"Debug the {pod_status['issue']} issue with pod {pod_status['pod_name']} in namespace {namespace}. Identify the root cause and provide a solution."
            
            print_status("Calling Kubently API for debugging...", "info")
            api_response = call_kubently_api(
                api_url,
                api_key,
                query,
                namespace
            )
            
            response_text = extract_response_text(api_response)
            
            # Display response (truncated)
            print("\n" + "-"*40)
            print("KUBENTLY RESPONSE:")
            print("-"*40)
            if len(response_text) > 500:
                print(response_text[:500] + "...")
            else:
                print(response_text)
            print("-"*40 + "\n")
            
            results["steps"].append({
                "name": "kubently_debug",
                "success": "error" not in api_response,
                "query": query,
                "response": response_text[:1000]  # Truncate for storage
            })
    
    # Step 4: Cleanup
    print_status("Cleaning up...", "info")
    cleanup_success = cleanup_namespace(namespace)
    results["steps"].append({
        "name": "cleanup",
        "success": cleanup_success
    })
    
    # Save results
    output_dir = Path("api-test-results")
    output_dir.mkdir(exist_ok=True)
    
    output_file = output_dir / f"{scenario_name}_{datetime.now():%Y%m%d_%H%M%S}.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    
    print("="*60)
    print_status(f"Test complete! Results: {output_file}", "success")
    
    # Summary
    successful = sum(1 for s in results["steps"] if s.get("success"))
    total = len(results["steps"])
    print(f"Success rate: {successful}/{total} steps")
    
    # Check if Kubently identified the issue correctly
    for step in results["steps"]:
        if step.get("name") == "kubently_debug":
            response = step.get("response", "").lower()
            
            # Check for expected keywords based on scenario
            expected_keywords = {
                "01": ["busyboxx", "typo", "busybox"],
                "03": ["crash", "command", "exit"],
                "06": ["memory", "oom", "limit"],
            }
            
            keywords = expected_keywords.get(scenario_num, [])
            found = sum(1 for kw in keywords if kw in response)
            
            if found > 0:
                print_status(f"Kubently correctly identified the issue! ({found}/{len(keywords)} keywords found)", "success")
            else:
                print_status("Kubently may not have fully identified the issue", "warning")

if __name__ == "__main__":
    main()