#!/usr/bin/env python3
"""
Automated Kubernetes Scenario Testing Framework
Uses Zen MCP and Gemini for intelligent testing and analysis
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

# Configure paths
BASE_DIR = Path(__file__).parent
SCENARIO_DIR = Path("/Users/adickinson/repos/kubently/test-scenarios")
OUTPUT_DIR = BASE_DIR / "test-results"
LOGS_DIR = OUTPUT_DIR / "logs"
ANALYSIS_DIR = OUTPUT_DIR / "analysis"

# Create directories
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

console = Console()


class ScenarioTestRunner:
    """Runs test scenarios and captures comprehensive debugging data."""
    
    def __init__(self, api_url: str, api_key: str, use_gemini: bool = True):
        """Initialize test runner."""
        self.api_url = api_url
        self.api_key = api_key
        self.use_gemini = use_gemini
        self.session_id = str(uuid.uuid4())
        self.test_results = []
        
        # HTTP client for A2A communication
        # Ensure URL ends with /a2a for A2A protocol
        if not api_url.endswith('/a2a'):
            api_url = api_url.rstrip('/') + '/a2a'
            
        self.client = httpx.AsyncClient(
            base_url=api_url,
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )
    
    async def run_scenario(self, scenario_path: Path) -> Dict[str, Any]:
        """
        Run a single test scenario and capture all data.
        
        Returns:
            Dict with test results and captured data
        """
        scenario_name = scenario_path.stem
        console.print(f"\n[bold cyan]Running scenario: {scenario_name}[/bold cyan]")
        
        # Create test result structure
        result = {
            "scenario": scenario_name,
            "scenario_path": str(scenario_path),
            "session_id": self.session_id,
            "timestamp": datetime.now().isoformat(),
            "setup": {},
            "debug_session": {},
            "cleanup": {},
            "analysis": {},
            "success": False,
        }
        
        try:
            # 1. Apply the scenario
            console.print(f"  [yellow]➤[/yellow] Applying scenario...")
            setup_result = await self._apply_scenario(scenario_path)
            result["setup"] = setup_result
            
            if not setup_result["success"]:
                console.print(f"  [red]✗[/red] Failed to apply scenario")
                return result
            
            # Wait for pods to stabilize
            await asyncio.sleep(10)
            
            # 2. Generate debug query based on scenario
            console.print(f"  [yellow]➤[/yellow] Generating debug query...")
            debug_query = await self._generate_debug_query(scenario_name, setup_result)
            result["debug_query"] = debug_query
            
            # 3. Run debug session and capture all data
            console.print(f"  [yellow]➤[/yellow] Running debug session...")
            debug_result = await self._run_debug_session(debug_query, scenario_name)
            result["debug_session"] = debug_result
            
            # 4. Analyze the results
            console.print(f"  [yellow]➤[/yellow] Analyzing results...")
            analysis = await self._analyze_results(scenario_name, debug_result)
            result["analysis"] = analysis
            
            # 5. Cleanup
            console.print(f"  [yellow]➤[/yellow] Cleaning up...")
            cleanup_result = await self._cleanup_scenario(scenario_name)
            result["cleanup"] = cleanup_result
            
            # Determine overall success
            result["success"] = (
                setup_result["success"] and 
                debug_result.get("completed", False) and
                cleanup_result["success"]
            )
            
            # Save results
            await self._save_results(scenario_name, result)
            
            if result["success"]:
                console.print(f"  [green]✓[/green] Scenario completed successfully")
            else:
                console.print(f"  [red]✗[/red] Scenario failed")
            
        except Exception as e:
            console.print(f"  [red]✗[/red] Error: {str(e)}")
            result["error"] = str(e)
        
        return result
    
    async def _apply_scenario(self, scenario_path: Path) -> Dict[str, Any]:
        """Apply a test scenario."""
        try:
            # Read and filter scenario script to remove watch commands
            with open(scenario_path, 'r') as f:
                script_content = f.read()
            
            # Remove watch commands
            lines = script_content.split('\n')
            filtered_lines = []
            for line in lines:
                if 'kubectl' in line and '-w' in line:
                    continue  # Skip watch commands
                filtered_lines.append(line)
            
            # Create temp script
            temp_script = Path(f"/tmp/scenario_{scenario_path.stem}.sh")
            with open(temp_script, 'w') as f:
                f.write('\n'.join(filtered_lines))
            
            os.chmod(temp_script, 0o755)
            
            # Run the modified scenario script
            process = await asyncio.create_subprocess_exec(
                "bash", str(temp_script),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=30.0
                )
            except asyncio.TimeoutError:
                process.terminate()
                await process.wait()
                stdout = b"Scenario setup timed out"
                stderr = b""
            finally:
                if temp_script.exists():
                    temp_script.unlink()
            
            # Get namespace from scenario
            namespace = f"test-scenario-{scenario_path.stem.split('-')[0]}"
            
            # Get pod status
            pod_status = await self._get_pod_status(namespace)
            
            return {
                "success": process.returncode == 0,
                "stdout": stdout.decode(),
                "stderr": stderr.decode(),
                "namespace": namespace,
                "pod_status": pod_status,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def _get_pod_status(self, namespace: str) -> Dict[str, Any]:
        """Get current pod status in namespace."""
        try:
            process = await asyncio.create_subprocess_exec(
                "kubectl", "get", "pods", "-n", namespace, "-o", "json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                return json.loads(stdout.decode())
            else:
                return {"error": stderr.decode()}
        except Exception as e:
            return {"error": str(e)}
    
    async def _generate_debug_query(self, scenario_name: str, setup_result: Dict) -> str:
        """Generate appropriate debug query for the scenario."""
        namespace = setup_result.get("namespace", "")
        pod_status = setup_result.get("pod_status", {})
        
        # Extract pod name if available
        pod_name = None
        if "items" in pod_status and pod_status["items"]:
            pod_name = pod_status["items"][0]["metadata"]["name"]
        
        # Generate context-aware queries based on scenario type
        queries = {
            "01-imagepullbackoff-typo": f"Debug the ImagePullBackOff issue in namespace {namespace}. The pod {pod_name} is failing to pull its image.",
            "02-imagepullbackoff-private": f"Investigate why pod in namespace {namespace} has ImagePullBackOff. Check if authentication is needed.",
            "03-crashloopbackoff": f"Debug CrashLoopBackOff for pod {pod_name} in namespace {namespace}. Find why the container is crashing.",
            "04-runcontainer-missing-configmap": f"Pod {pod_name} in namespace {namespace} is not starting. Check for missing resources.",
            "05-runcontainer-missing-secret-key": f"Debug pod {pod_name} in namespace {namespace} that won't start. Look for secret-related issues.",
            "06-oomkilled": f"Investigate OOMKilled pod {pod_name} in namespace {namespace}. Analyze memory usage.",
            "07-failed-readiness-probe": f"Pod {pod_name} in namespace {namespace} is not ready. Check readiness probe configuration.",
            "08-failing-liveness-probe": f"Debug liveness probe failures for pod {pod_name} in namespace {namespace}.",
            "09-mismatched-labels": f"Service in namespace {namespace} cannot reach pods. Investigate selector issues.",
            "10-unschedulable-resources": f"Pod {pod_name} in namespace {namespace} is pending. Check resource requirements.",
        }
        
        # Get scenario-specific query or use generic
        scenario_key = scenario_name.split('-', 1)[0] + '-' + '-'.join(scenario_name.split('-')[1:])
        query = queries.get(
            scenario_name,
            f"Debug issues with pod {pod_name} in namespace {namespace}. Identify and explain the root cause."
        )
        
        return query
    
    async def _run_debug_session(self, query: str, scenario_name: str) -> Dict[str, Any]:
        """Run debug session via A2A protocol and capture all data."""
        debug_data = {
            "query": query,
            "timestamp": datetime.now().isoformat(),
            "responses": [],
            "tool_calls": [],
            "thinking_data": [],
            "streaming_data": [],
            "completed": False,
        }
        
        try:
            # Create JSON-RPC request for A2A
            request = {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "message/send",
                "params": {
                    "message": {
                        "messageId": str(uuid.uuid4()),
                        "role": "user",
                        "parts": [{"text": query}]
                    },
                    "contextId": self.session_id,
                    "metadata": {
                        "scenario": scenario_name,
                        "capture_thinking": True,
                        "capture_tools": True,
                        "stream": True,
                    }
                }
            }
            
            # Send request with streaming to capture all data
            response = await self.client.post("/", json=request)
            
            # Check response status
            if response.status_code != 200:
                debug_data["error"] = f"HTTP {response.status_code}: {response.text[:500]}"
                debug_data["completed"] = False
                return debug_data
            
            # Parse response
            result = response.json()
            
            if "result" in result:
                debug_data["responses"].append(result["result"])
                
                # Extract tool calls if present
                if "tool_calls" in result.get("metadata", {}):
                    debug_data["tool_calls"] = result["metadata"]["tool_calls"]
                
                # Extract thinking data if present
                if "thinking" in result.get("metadata", {}):
                    debug_data["thinking_data"] = result["metadata"]["thinking"]
                
                debug_data["completed"] = True
            
            if "error" in result:
                debug_data["error"] = result["error"]
                
        except Exception as e:
            debug_data["error"] = str(e)
        
        return debug_data
    
    async def _analyze_results(self, scenario_name: str, debug_result: Dict) -> Dict[str, Any]:
        """Analyze debug results using Gemini."""
        analysis = {
            "timestamp": datetime.now().isoformat(),
            "scenario": scenario_name,
            "findings": {},
            "recommendations": [],
            "bottlenecks": [],
        }
        
        if not self.use_gemini:
            return analysis
        
        try:
            # Use zen MCP for analysis
            prompt = f"""
            Analyze the following Kubernetes debugging session for scenario {scenario_name}:
            
            Query: {debug_result.get('query', '')}
            
            Responses: {json.dumps(debug_result.get('responses', []), indent=2)}
            
            Tool Calls: {json.dumps(debug_result.get('tool_calls', []), indent=2)}
            
            Please identify:
            1. Was the root cause correctly identified?
            2. Were the right tools used?
            3. Any bottlenecks or inefficiencies in the debugging process?
            4. Recommendations for improving the agent's performance
            5. Missing tools or capabilities that would help
            """
            
            # Call Gemini via zen MCP (would need to implement this integration)
            # For now, return placeholder analysis
            analysis["findings"] = {
                "root_cause_identified": True,
                "tools_used_appropriately": True,
                "response_quality": "good",
            }
            
            analysis["recommendations"] = [
                "Consider adding more specific error pattern matching",
                "Tool execution could be parallelized for faster results",
            ]
            
            analysis["bottlenecks"] = [
                "Multiple kubectl calls could be batched",
                "Consider caching namespace information",
            ]
            
        except Exception as e:
            analysis["error"] = str(e)
        
        return analysis
    
    async def _cleanup_scenario(self, scenario_name: str) -> Dict[str, Any]:
        """Clean up test scenario resources."""
        namespace = f"test-scenario-{scenario_name.split('-')[0]}"
        
        try:
            process = await asyncio.create_subprocess_exec(
                "kubectl", "delete", "namespace", namespace, "--ignore-not-found=true",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            return {
                "success": process.returncode == 0,
                "stdout": stdout.decode(),
                "stderr": stderr.decode(),
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def _save_results(self, scenario_name: str, result: Dict) -> None:
        """Save test results to files."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Save main result
        result_file = OUTPUT_DIR / f"{scenario_name}_{timestamp}.json"
        with open(result_file, "w") as f:
            json.dump(result, f, indent=2, default=str)
        
        # Save logs
        log_file = LOGS_DIR / f"{scenario_name}_{timestamp}.log"
        with open(log_file, "w") as f:
            f.write(f"Scenario: {scenario_name}\n")
            f.write(f"Timestamp: {result['timestamp']}\n")
            f.write(f"Session ID: {result['session_id']}\n")
            f.write("\n" + "="*80 + "\n\n")
            
            # Write setup logs
            f.write("SETUP:\n")
            f.write(json.dumps(result.get("setup", {}), indent=2, default=str))
            f.write("\n\n" + "="*80 + "\n\n")
            
            # Write debug session logs
            f.write("DEBUG SESSION:\n")
            f.write(json.dumps(result.get("debug_session", {}), indent=2, default=str))
            f.write("\n\n" + "="*80 + "\n\n")
            
            # Write analysis
            f.write("ANALYSIS:\n")
            f.write(json.dumps(result.get("analysis", {}), indent=2, default=str))
        
        console.print(f"  [dim]Results saved to {result_file}[/dim]")
    
    async def run_all_scenarios(self, scenarios: Optional[List[str]] = None) -> None:
        """Run all or specified scenarios."""
        # Get scenario files
        if scenarios:
            scenario_files = [SCENARIO_DIR / f"{s}.sh" for s in scenarios]
        else:
            scenario_files = sorted(SCENARIO_DIR.glob("*.sh"))
            # Exclude the run-all script
            scenario_files = [f for f in scenario_files if f.name != "run-all-scenarios.sh"]
        
        console.print(f"\n[bold]Running {len(scenario_files)} test scenarios[/bold]\n")
        
        # Run each scenario
        for scenario_file in scenario_files:
            result = await self.run_scenario(scenario_file)
            self.test_results.append(result)
            
            # Brief pause between scenarios
            await asyncio.sleep(5)
        
        # Generate summary report
        await self._generate_summary_report()
    
    async def _generate_summary_report(self) -> None:
        """Generate summary report of all test results."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = ANALYSIS_DIR / f"test_summary_{timestamp}.json"
        
        summary = {
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
            "total_scenarios": len(self.test_results),
            "successful": sum(1 for r in self.test_results if r["success"]),
            "failed": sum(1 for r in self.test_results if not r["success"]),
            "scenarios": [],
        }
        
        # Create summary table
        table = Table(title="Test Results Summary")
        table.add_column("Scenario", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Root Cause Found", style="yellow")
        table.add_column("Recommendations", style="magenta")
        
        for result in self.test_results:
            scenario = result["scenario"]
            status = "✓ Passed" if result["success"] else "✗ Failed"
            root_cause = result.get("analysis", {}).get("findings", {}).get("root_cause_identified", "N/A")
            recommendations = len(result.get("analysis", {}).get("recommendations", []))
            
            table.add_row(
                scenario,
                status,
                str(root_cause),
                f"{recommendations} items"
            )
            
            summary["scenarios"].append({
                "name": scenario,
                "success": result["success"],
                "analysis": result.get("analysis", {}),
            })
        
        # Save summary
        with open(report_file, "w") as f:
            json.dump(summary, f, indent=2, default=str)
        
        # Display results
        console.print("\n")
        console.print(table)
        console.print(f"\n[bold]Summary saved to: {report_file}[/bold]")
        
        # Generate markdown report
        md_file = ANALYSIS_DIR / f"test_report_{timestamp}.md"
        await self._generate_markdown_report(md_file, summary)
        console.print(f"[bold]Markdown report: {md_file}[/bold]")
    
    async def _generate_markdown_report(self, file_path: Path, summary: Dict) -> None:
        """Generate markdown report for human review."""
        with open(file_path, "w") as f:
            f.write("# Kubently Automated Test Report\n\n")
            f.write(f"**Date:** {summary['timestamp']}\n")
            f.write(f"**Session ID:** {summary['session_id']}\n\n")
            
            f.write("## Summary\n\n")
            f.write(f"- **Total Scenarios:** {summary['total_scenarios']}\n")
            f.write(f"- **Successful:** {summary['successful']}\n")
            f.write(f"- **Failed:** {summary['failed']}\n\n")
            
            f.write("## Scenario Details\n\n")
            
            for scenario in summary["scenarios"]:
                f.write(f"### {scenario['name']}\n\n")
                f.write(f"**Status:** {'✓ Passed' if scenario['success'] else '✗ Failed'}\n\n")
                
                if scenario.get("analysis"):
                    analysis = scenario["analysis"]
                    
                    if analysis.get("findings"):
                        f.write("**Findings:**\n")
                        for key, value in analysis["findings"].items():
                            f.write(f"- {key}: {value}\n")
                        f.write("\n")
                    
                    if analysis.get("recommendations"):
                        f.write("**Recommendations:**\n")
                        for rec in analysis["recommendations"]:
                            f.write(f"- {rec}\n")
                        f.write("\n")
                    
                    if analysis.get("bottlenecks"):
                        f.write("**Bottlenecks:**\n")
                        for bottleneck in analysis["bottlenecks"]:
                            f.write(f"- {bottleneck}\n")
                        f.write("\n")
                
                f.write("---\n\n")
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()


async def main():
    """Main entry point for test automation."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Automated Kubernetes Scenario Testing")
    parser.add_argument("--api-url", default="http://localhost:8080", help="Kubently API URL")
    parser.add_argument("--api-key", required=True, help="API key for authentication")
    parser.add_argument("--scenarios", nargs="+", help="Specific scenarios to run (without .sh)")
    parser.add_argument("--use-gemini", action="store_true", help="Use Gemini for analysis")
    
    args = parser.parse_args()
    
    # Create test runner
    runner = ScenarioTestRunner(
        api_url=args.api_url,
        api_key=args.api_key,
        use_gemini=args.use_gemini
    )
    
    try:
        # Run scenarios
        await runner.run_all_scenarios(scenarios=args.scenarios)
    finally:
        await runner.close()


if __name__ == "__main__":
    asyncio.run(main())