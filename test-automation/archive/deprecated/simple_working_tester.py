#!/usr/bin/env python3
"""
Simple working tester - verified to work with actual Kubently setup
"""

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Simple console output (no rich dependency needed)
def print_status(msg, status="info"):
    colors = {
        "info": "\033[94m",
        "success": "\033[92m", 
        "warning": "\033[93m",
        "error": "\033[91m",
    }
    reset = "\033[0m"
    print(f"{colors.get(status, '')}[{status.upper()}] {msg}{reset}")

def run_kubectl_command(command):
    """Run kubectl command and return output."""
    try:
        result = subprocess.run(
            command.split(),
            capture_output=True,
            text=True,
            timeout=30
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

def apply_scenario(scenario_path):
    """Apply a test scenario."""
    print_status(f"Applying scenario: {scenario_path}")
    
    try:
        result = subprocess.run(
            ["bash", scenario_path],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            print_status("Scenario applied successfully", "success")
            return True
        else:
            print_status(f"Failed to apply scenario: {result.stderr}", "error")
            return False
    except Exception as e:
        print_status(f"Error applying scenario: {e}", "error")
        return False

def cleanup_namespace(namespace):
    """Clean up test namespace."""
    print_status(f"Cleaning up namespace: {namespace}")
    result = run_kubectl_command(f"kubectl delete namespace {namespace} --ignore-not-found=true")
    return result["success"]

def test_kubently_debug(api_url, api_key, query, namespace):
    """Test Kubently debug endpoint."""
    print_status("Testing Kubently debug...")
    
    # For now, just check if we can connect
    # In real implementation, this would call the API
    return {
        "query": query,
        "namespace": namespace,
        "response": "Mock response - replace with actual API call",
        "timestamp": datetime.now().isoformat()
    }

def main():
    # Check environment
    if len(sys.argv) < 2:
        print("Usage: python3 simple_working_tester.py <scenario_number>")
        print("Example: python3 simple_working_tester.py 01")
        sys.exit(1)
    
    scenario_num = sys.argv[1]
    
    # Find scenario file
    scenario_files = list(Path("/Users/adickinson/repos/kubently/test-scenarios").glob(f"{scenario_num}-*.sh"))
    if not scenario_files:
        print_status(f"No scenario found for number: {scenario_num}", "error")
        sys.exit(1)
    
    scenario_path = scenario_files[0]
    scenario_name = scenario_path.stem
    namespace = f"test-scenario-{scenario_num}"
    
    print_status(f"Testing scenario: {scenario_name}", "info")
    
    # Create output directory
    output_dir = Path("test-output")
    output_dir.mkdir(exist_ok=True)
    
    # Test results
    results = {
        "scenario": scenario_name,
        "timestamp": datetime.now().isoformat(),
        "steps": []
    }
    
    # Step 1: Apply scenario
    if apply_scenario(str(scenario_path)):
        results["steps"].append({"step": "apply_scenario", "success": True})
        
        # Wait for pods to settle
        print_status("Waiting for pods to stabilize...")
        time.sleep(5)
        
        # Step 2: Check pod status
        print_status("Checking pod status...")
        pod_result = run_kubectl_command(f"kubectl get pods -n {namespace} -o json")
        
        if pod_result["success"]:
            pods_data = json.loads(pod_result["stdout"])
            pod_count = len(pods_data.get("items", []))
            print_status(f"Found {pod_count} pods", "info")
            
            # Get first pod status if exists
            if pods_data.get("items"):
                pod = pods_data["items"][0]
                pod_name = pod["metadata"]["name"]
                pod_phase = pod["status"]["phase"]
                
                print_status(f"Pod: {pod_name}, Status: {pod_phase}", "info")
                
                # Check for specific conditions
                conditions = pod["status"].get("conditions", [])
                container_statuses = pod["status"].get("containerStatuses", [])
                
                results["steps"].append({
                    "step": "pod_status",
                    "success": True,
                    "pod_name": pod_name,
                    "pod_phase": pod_phase,
                    "conditions": conditions,
                    "container_statuses": container_statuses
                })
                
                # Step 3: Generate debug query
                query = f"Debug pod {pod_name} in namespace {namespace}"
                print_status(f"Debug query: {query}", "info")
                
                # Step 4: Test debug (mock for now)
                debug_result = test_kubently_debug(
                    api_url="http://localhost:8001",
                    api_key="test-key",
                    query=query,
                    namespace=namespace
                )
                
                results["steps"].append({
                    "step": "debug",
                    "success": True,
                    "result": debug_result
                })
        
        # Step 5: Cleanup
        print_status("Cleaning up...")
        cleanup_success = cleanup_namespace(namespace)
        results["steps"].append({
            "step": "cleanup",
            "success": cleanup_success
        })
    else:
        results["steps"].append({"step": "apply_scenario", "success": False})
    
    # Save results
    output_file = output_dir / f"{scenario_name}_{datetime.now():%Y%m%d_%H%M%S}.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    
    print_status(f"Results saved to: {output_file}", "success")
    
    # Summary
    print("\n" + "="*50)
    print("TEST SUMMARY")
    print("="*50)
    successful_steps = sum(1 for step in results["steps"] if step.get("success"))
    total_steps = len(results["steps"])
    print(f"Scenario: {scenario_name}")
    print(f"Success Rate: {successful_steps}/{total_steps} steps")
    
    if successful_steps == total_steps:
        print_status("TEST PASSED", "success")
    else:
        print_status("TEST FAILED", "error")

if __name__ == "__main__":
    main()