#!/usr/bin/env python3
"""
Enhanced Comprehensive Test Runner for Kubently
Captures full debugging data including tool calls, thinking steps, and multi-node interactions
from structured logs.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
import httpx
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Rich console for pretty output
console = Console()

# Try to import Gemini analyzer
try:
    from gemini_analyzer import GeminiAnalyzer
except ImportError:
    GeminiAnalyzer = None
    logger.warning("GeminiAnalyzer not available. Using heuristic analysis only.")


class ScenarioState(Enum):
    """States for scenario execution."""
    PENDING = "pending"
    SETTING_UP = "setting_up"
    RUNNING = "running"
    ANALYZING = "analyzing"
    CLEANING_UP = "cleaning_up"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TestScenario:
    """Represents a test scenario."""
    name: str
    path: Path
    expected_fix: str
    namespace: str
    query_template: str
    validation_checks: List[str]


@dataclass
class ToolCall:
    """Represents a single tool call."""
    timestamp: str
    tool_name: str
    parameters: Dict[str, Any]
    output: Optional[str] = None
    success: bool = True
    duration_ms: Optional[int] = None
    error: Optional[str] = None


@dataclass
class ThinkingStep:
    """Represents a thinking/reasoning step."""
    timestamp: str
    node: str  # "Diagnostician" or "Judge"
    content: str
    decision: Optional[str] = None  # For Judge: "COMPLETE" or "INCOMPLETE"
    confidence: Optional[float] = None


@dataclass
class EnhancedDebugTrace:
    """Enhanced debug trace with full data from logs."""
    timestamp: str
    query: str
    
    # Original fields (for compatibility)
    thinking_steps: List[Dict]
    tool_calls: List[Dict]
    responses: List[str]
    errors: List[str]
    duration_seconds: float
    token_usage: Dict[str, int]
    
    # Enhanced fields from logs
    structured_tool_calls: List[ToolCall] = field(default_factory=list)
    structured_thinking: List[ThinkingStep] = field(default_factory=list)
    diagnostician_rounds: List[Dict] = field(default_factory=list)
    judge_decisions: List[Dict] = field(default_factory=list)
    llm_prompts: List[Dict] = field(default_factory=list)
    total_rounds: int = 0
    
    def to_dict(self) -> Dict:
        """Convert to dictionary, handling dataclass fields."""
        result = {
            "timestamp": self.timestamp,
            "query": self.query,
            "thinking_steps": self.thinking_steps,
            "tool_calls": self.tool_calls,
            "responses": self.responses,
            "errors": self.errors,
            "duration_seconds": self.duration_seconds,
            "token_usage": self.token_usage,
            "structured_tool_calls": [asdict(tc) for tc in self.structured_tool_calls],
            "structured_thinking": [asdict(ts) for ts in self.structured_thinking],
            "diagnostician_rounds": self.diagnostician_rounds,
            "judge_decisions": self.judge_decisions,
            "llm_prompts": self.llm_prompts,
            "total_rounds": self.total_rounds
        }
        return result


@dataclass
class TestResult:
    """Complete test result with enhanced data."""
    scenario: TestScenario
    setup_success: bool
    debug_trace: Optional[EnhancedDebugTrace]
    analysis: Dict[str, Any]
    cleanup_success: bool
    overall_success: bool
    recommendations: List[str]
    log_snippets: Optional[List[str]] = None  # Raw log snippets for verification


class LogCapture:
    """Captures and parses structured logs from Kubently."""
    
    def __init__(self):
        self.buffer = []
        self.structured_events = []
        
    async def start_capture(self, namespace: str = "kubently"):
        """Start capturing logs from kubently pods."""
        # Get pod name
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", namespace, "-l", "app.kubernetes.io/component=api", 
             "-o", "jsonpath={.items[0].metadata.name}"],
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            logger.error(f"Failed to get pod name: {result.stderr}")
            return
            
        pod_name = result.stdout.strip()
        if not pod_name:
            logger.error("No kubently-api pod found")
            return
            
        # Start log streaming in background
        self.log_process = subprocess.Popen(
            ["kubectl", "logs", "-f", "-n", namespace, pod_name, "--tail=1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Start async reader
        asyncio.create_task(self._read_logs())
        
    async def _read_logs(self):
        """Read logs asynchronously."""
        while self.log_process and self.log_process.poll() is None:
            line = self.log_process.stdout.readline()
            if line:
                self.buffer.append(line)
                self._parse_structured_log(line)
            await asyncio.sleep(0.01)
    
    def _parse_structured_log(self, line: str):
        """Parse structured log events."""
        # Look for JSON structured logs
        if '"event":' in line:
            try:
                # Extract JSON from log line
                json_start = line.find('{')
                if json_start >= 0:
                    json_str = line[json_start:]
                    data = json.loads(json_str)
                    if "event" in data:
                        self.structured_events.append(data)
            except json.JSONDecodeError:
                pass  # Not a valid JSON log
                
    async def stop_capture(self):
        """Stop capturing logs."""
        if hasattr(self, 'log_process') and self.log_process:
            self.log_process.terminate()
            await asyncio.sleep(0.5)
            if self.log_process.poll() is None:
                self.log_process.kill()
    
    def get_events_for_thread(self, thread_id: str) -> List[Dict]:
        """Get all events for a specific thread/context."""
        return [e for e in self.structured_events 
                if e.get("thread_id") == thread_id or e.get("context_id") == thread_id]
    
    def clear(self):
        """Clear buffers."""
        self.buffer.clear()
        self.structured_events.clear()


class EnhancedKubentlyDebugCapture:
    """Enhanced capture that includes structured logs."""
    
    def __init__(self, api_url: str, api_key: str):
        """Initialize enhanced debug capture."""
        self.api_url = api_url
        self.api_key = api_key
        self.session_id = str(uuid.uuid4())
        self.log_capture = LogCapture()
        
        # HTTP client
        if not api_url.endswith('/a2a'):
            api_url = api_url.rstrip('/') + '/a2a'
        
        self.client = httpx.AsyncClient(
            base_url=api_url,
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json",
            },
            timeout=120.0,
        )
        
    async def execute_debug_with_enhanced_capture(
        self, 
        query: str,
        namespace: str,
        capture_thinking: bool = True
    ) -> EnhancedDebugTrace:
        """Execute debug query with enhanced data capture from logs."""
        
        # Start log capture
        await self.log_capture.start_capture()
        await asyncio.sleep(1)  # Let log capture initialize
        
        # Clear any previous events
        self.log_capture.clear()
        
        start_time = time.time()
        
        # Initialize enhanced trace
        trace = EnhancedDebugTrace(
            timestamp=datetime.now().isoformat(),
            query=query,
            thinking_steps=[],
            tool_calls=[],
            responses=[],
            errors=[],
            duration_seconds=0,
            token_usage={"input": 0, "output": 0, "total": 0}
        )
        
        try:
            # Create A2A request
            request = {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "message/send",
                "params": {
                    "message": {
                        "messageId": str(uuid.uuid4()),
                        "role": "user",
                        "parts": [{"text": query}]
                    }
                }
            }
            
            # Send request
            response = await self.client.post("/", json=request)
            
            if response.status_code != 200:
                logger.error(f"API returned {response.status_code}: {response.text[:200]}")
                trace.errors.append(f"HTTP {response.status_code}")
            else:
                data = response.json()
                
                # Extract basic response
                if "result" in data:
                    result = data["result"]
                    
                    # Get context/thread ID for log correlation
                    context_id = result.get("contextId", "")
                    
                    # Extract artifacts (final response)
                    if "artifacts" in result:
                        for artifact in result.get("artifacts", []):
                            if "parts" in artifact:
                                for part in artifact["parts"]:
                                    if "text" in part:
                                        trace.responses.append(part["text"])
                    
                    # Wait a bit for logs to catch up
                    await asyncio.sleep(2)
                    
                    # Now extract enhanced data from logs
                    if context_id:
                        events = self.log_capture.get_events_for_thread(context_id)
                        self._process_structured_events(trace, events)
                    
                    # Also capture some raw log snippets for verification
                    trace.log_snippets = self.log_capture.buffer[-100:]  # Last 100 lines
                    
        except Exception as e:
            logger.error(f"Debug execution error: {e}")
            trace.errors.append(str(e))
        
        finally:
            # Stop log capture
            await self.log_capture.stop_capture()
        
        # Finalize trace
        trace.duration_seconds = time.time() - start_time
        
        return trace
    
    def _process_structured_events(self, trace: EnhancedDebugTrace, events: List[Dict]):
        """Process structured log events into enhanced trace."""
        
        current_round = {}
        
        for event in events:
            event_type = event.get("event", "")
            
            if event_type == "tool_input":
                # Tool call started
                tool_call = ToolCall(
                    timestamp=event.get("timestamp", ""),
                    tool_name=event.get("tool_name", ""),
                    parameters=event.get("parameters", {})
                )
                trace.structured_tool_calls.append(tool_call)
                
                # Also add to legacy format
                trace.tool_calls.append({
                    "timestamp": tool_call.timestamp,
                    "tool": tool_call.tool_name,
                    "args": tool_call.parameters
                })
                
            elif event_type == "tool_output":
                # Tool call completed - update the last tool call
                if trace.structured_tool_calls:
                    last_tool = trace.structured_tool_calls[-1]
                    last_tool.output = event.get("output", "")
                    last_tool.success = event.get("success", True)
                    last_tool.error = event.get("error")
                    
                    # Update legacy format
                    if trace.tool_calls:
                        trace.tool_calls[-1]["result"] = last_tool.output
                        
            elif event_type == "llm_raw_tool_calls":
                # LLM decided to call tools
                step = event.get("step", 0)
                tool_names = event.get("tool_names", [])
                current_round["tools_called"] = tool_names
                current_round["step"] = step
                
            elif event_type == "llm_prompt":
                # Capture prompts sent to LLM
                trace.llm_prompts.append({
                    "timestamp": event.get("timestamp", ""),
                    "messages": event.get("messages", []),
                    "system_prompt": event.get("system_prompt", "")
                })
                
            elif event_type == "llm_final_response":
                # Final response from agent
                trace.total_rounds = event.get("total_steps", 1)
                
            elif "Diagnostician" in str(event):
                # Diagnostician output
                thinking = ThinkingStep(
                    timestamp=datetime.now().isoformat(),
                    node="Diagnostician",
                    content=str(event),
                    decision=None,
                    confidence=None
                )
                trace.structured_thinking.append(thinking)
                
                if current_round:
                    current_round["diagnostician_output"] = str(event)
                    
            elif "Judge" in str(event):
                # Judge decision
                decision = "COMPLETE" if "COMPLETE" in str(event) else "INCOMPLETE"
                thinking = ThinkingStep(
                    timestamp=datetime.now().isoformat(),
                    node="Judge",
                    content=str(event),
                    decision=decision,
                    confidence=None
                )
                trace.structured_thinking.append(thinking)
                
                trace.judge_decisions.append({
                    "timestamp": datetime.now().isoformat(),
                    "decision": decision,
                    "response": str(event)
                })
                
                if current_round:
                    current_round["judge_decision"] = decision
                    trace.diagnostician_rounds.append(current_round)
                    current_round = {}
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()


class EnhancedComprehensiveTestRunner:
    """Enhanced test runner with full data capture."""
    
    def __init__(self, api_url: str, api_key: str, use_gemini: bool = False):
        self.api_url = api_url
        self.api_key = api_key
        self.use_gemini = use_gemini
        self.debug_capture = EnhancedKubentlyDebugCapture(api_url, api_key)
        self.results = []
        
    def _load_scenarios(self) -> List[TestScenario]:
        """Load test scenarios from directory."""
        scenarios = []
        
        # Check scenarios directory
        scenarios_dir = Path("scenarios")
        if not scenarios_dir.exists():
            scenarios_dir = Path(__file__).parent / "scenarios"
        
        if scenarios_dir.exists():
            for scenario_file in sorted(scenarios_dir.glob("*.sh")):
                # Parse scenario file for metadata
                scenario = self._parse_scenario_file(scenario_file)
                if scenario:
                    scenarios.append(scenario)
        
        return scenarios
    
    def _parse_scenario_file(self, file_path: Path) -> Optional[TestScenario]:
        """Parse a scenario shell script to extract metadata."""
        try:
            content = file_path.read_text()
            
            # Extract namespace
            namespace_match = re.search(r'NAMESPACE="?([^"\s]+)"?', content)
            namespace = namespace_match.group(1) if namespace_match else f"test-scenario-{file_path.stem}"
            
            # Extract expected fix from comments
            expected_fix = "Unknown"
            for line in content.split('\n'):
                if 'Expected fix:' in line or 'expected fix:' in line:
                    expected_fix = line.split(':', 1)[1].strip()
                    break
            
            # Create query template based on scenario name
            name = file_path.stem
            if "imagepull" in name.lower():
                query_template = "In cluster kind, there's an issue with a pod in namespace {namespace}. The pod is showing ImagePullBackOff status. Please investigate and fix the issue."
            elif "crash" in name.lower():
                query_template = "In cluster kind, a pod in namespace {namespace} is in CrashLoopBackOff. Please diagnose the issue."
            elif "service" in name.lower():
                query_template = "In cluster kind, the service in namespace {namespace} doesn't seem to be working. Traffic isn't reaching the pods. Please investigate."
            elif "rbac" in name.lower():
                query_template = "In cluster kind, a pod in namespace {namespace} is encountering authorization issues. Please diagnose and make recommendations."
            else:
                query_template = "In cluster kind, there's an issue in namespace {namespace}. Please investigate and identify the root cause."
            
            return TestScenario(
                name=name,
                path=file_path,
                expected_fix=expected_fix,
                namespace=namespace,
                query_template=query_template,
                validation_checks=[]
            )
            
        except Exception as e:
            logger.error(f"Failed to parse scenario {file_path}: {e}")
            return None
    
    async def run_comprehensive_test(self, scenario: TestScenario) -> TestResult:
        """Run a comprehensive test with enhanced capture."""
        console.print(f"\n[bold blue]Testing Scenario: {scenario.name}[/bold blue]")
        
        # Setup scenario
        setup_success = await self._setup_scenario(scenario)
        
        if not setup_success:
            return TestResult(
                scenario=scenario,
                setup_success=False,
                debug_trace=None,
                analysis={"error": "Setup failed"},
                cleanup_success=False,
                overall_success=False,
                recommendations=["Fix scenario setup script"]
            )
        
        # Execute debug with enhanced capture
        query = scenario.query_template.format(namespace=scenario.namespace)
        console.print(f"[yellow]Query:[/yellow] {query}")
        
        debug_trace = await self.debug_capture.execute_debug_with_enhanced_capture(
            query=query,
            namespace=scenario.namespace
        )
        
        # Analyze results
        analysis = self._analyze_results(scenario, debug_trace)
        
        # Cleanup
        cleanup_success = await self._cleanup_scenario(scenario)
        
        # Determine overall success
        overall_success = (
            setup_success and 
            cleanup_success and 
            analysis.get("root_cause_analysis", {}).get("identified_correctly", False)
        )
        
        # Generate recommendations
        recommendations = self._generate_recommendations(scenario, debug_trace, analysis)
        
        result = TestResult(
            scenario=scenario,
            setup_success=setup_success,
            debug_trace=debug_trace,
            analysis=analysis,
            cleanup_success=cleanup_success,
            overall_success=overall_success,
            recommendations=recommendations,
            log_snippets=debug_trace.log_snippets if debug_trace else None
        )
        
        self.results.append(result)
        
        # Display summary
        self._display_result_summary(result)
        
        # Save enhanced result
        self._save_enhanced_result(result)
        
        return result
    
    async def _setup_scenario(self, scenario: TestScenario) -> bool:
        """Setup test scenario."""
        try:
            result = subprocess.run(
                ["bash", str(scenario.path), "setup"],
                capture_output=True,
                text=True,
                timeout=60
            )
            return result.returncode == 0
        except Exception as e:
            logger.error(f"Setup failed for {scenario.name}: {e}")
            return False
    
    async def _cleanup_scenario(self, scenario: TestScenario) -> bool:
        """Cleanup test scenario."""
        try:
            result = subprocess.run(
                ["bash", str(scenario.path), "cleanup"],
                capture_output=True,
                text=True,
                timeout=30
            )
            return result.returncode == 0
        except Exception as e:
            logger.error(f"Cleanup failed for {scenario.name}: {e}")
            return False
    
    def _analyze_results(
        self, 
        scenario: TestScenario, 
        trace: EnhancedDebugTrace
    ) -> Dict[str, Any]:
        """Analyze test results with enhanced data."""
        
        # Use Gemini if available
        if self.use_gemini and GeminiAnalyzer:
            analyzer = GeminiAnalyzer()
            if analyzer.initialized:
                # Convert to format expected by analyzer
                test_result = {
                    "scenario": asdict(scenario) if hasattr(scenario, '__dataclass_fields__') else scenario.__dict__,
                    "debug_trace": trace.to_dict() if trace else {}
                }
                return analyzer.analyze_test_result(test_result)
        
        # Enhanced heuristic analysis using structured data
        if not trace:
            return {"error": "No trace data"}
        
        response_text = ' '.join(trace.responses).lower()
        expected_fix = scenario.expected_fix.lower()
        
        # Check if root cause was found
        root_cause_found = any(
            keyword in response_text
            for keyword in expected_fix.split()
            if len(keyword) > 3
        )
        
        # Analyze efficiency using enhanced data
        efficiency_score = 10
        if trace.structured_tool_calls:
            # Penalize for too many tool calls
            efficiency_score = max(1, 10 - len(trace.structured_tool_calls) // 3)
        
        # Check for multi-round inefficiency
        if trace.total_rounds > 2:
            efficiency_score = max(1, efficiency_score - (trace.total_rounds - 2) * 2)
        
        # Analyze tool usage patterns
        tool_patterns = {}
        for tool_call in trace.structured_tool_calls:
            tool_patterns[tool_call.tool_name] = tool_patterns.get(tool_call.tool_name, 0) + 1
        
        return {
            "model": "enhanced_heuristic",
            "timestamp": datetime.now().isoformat(),
            "root_cause_analysis": {
                "identified_correctly": root_cause_found,
                "confidence": 0.7 if root_cause_found else 0.3,
                "explanation": f"Found root cause keywords in response" if root_cause_found else "Keywords not found"
            },
            "efficiency_metrics": {
                "tool_usage_score": efficiency_score,
                "response_time": trace.duration_seconds,
                "total_tool_calls": len(trace.structured_tool_calls),
                "total_rounds": trace.total_rounds,
                "tool_patterns": tool_patterns
            },
            "bottlenecks": self._identify_bottlenecks(trace),
            "improvements": self._suggest_improvements(trace),
            "missing_capabilities": [],
            "overall_quality": {
                "score": 8 if root_cause_found and efficiency_score > 5 else 4,
                "justification": "Enhanced analysis with full trace data"
            }
        }
    
    def _identify_bottlenecks(self, trace: EnhancedDebugTrace) -> List[str]:
        """Identify performance bottlenecks from enhanced trace."""
        bottlenecks = []
        
        if trace.total_rounds > 2:
            bottlenecks.append(f"Too many rounds ({trace.total_rounds}) - could complete in fewer iterations")
        
        # Check for repeated tool calls
        tool_counts = {}
        for tc in trace.structured_tool_calls:
            key = f"{tc.tool_name}:{json.dumps(tc.parameters, sort_keys=True)}"
            tool_counts[key] = tool_counts.get(key, 0) + 1
        
        for key, count in tool_counts.items():
            if count > 1:
                bottlenecks.append(f"Repeated tool call: {key.split(':')[0]} called {count} times")
        
        # Check for inefficient Judge decisions
        incomplete_count = sum(1 for d in trace.judge_decisions if d.get("decision") == "INCOMPLETE")
        if incomplete_count > 1:
            bottlenecks.append(f"Judge requested more data {incomplete_count} times when initial data might have been sufficient")
        
        return bottlenecks
    
    def _suggest_improvements(self, trace: EnhancedDebugTrace) -> List[str]:
        """Suggest improvements based on enhanced trace."""
        improvements = []
        
        if trace.total_rounds > 2:
            improvements.append("Diagnostician should gather more comprehensive data in first round")
        
        if not trace.structured_tool_calls:
            improvements.append("No tool calls detected - ensure agent is using kubectl commands")
        
        # Check if events were gathered
        has_events = any("events" in str(tc.parameters) for tc in trace.structured_tool_calls)
        if not has_events:
            improvements.append("Always check events as part of initial investigation")
        
        return improvements
    
    def _generate_recommendations(
        self,
        scenario: TestScenario,
        trace: Optional[EnhancedDebugTrace],
        analysis: Dict[str, Any]
    ) -> List[str]:
        """Generate recommendations based on analysis."""
        recommendations = []
        
        if trace:
            # Based on bottlenecks
            for bottleneck in analysis.get("bottlenecks", []):
                if "Too many rounds" in bottleneck:
                    recommendations.append("Optimize Judge prompt to recognize sufficient data earlier")
                elif "Repeated tool call" in bottleneck:
                    recommendations.append("Add caching or state tracking to avoid repeated tool calls")
            
            # Based on improvements
            recommendations.extend(analysis.get("improvements", []))
        
        return recommendations
    
    def _display_result_summary(self, result: TestResult):
        """Display test result summary."""
        status = "✅" if result.overall_success else "❌"
        
        console.print(f"\n{status} Scenario: {result.scenario.name}")
        
        if result.debug_trace:
            trace = result.debug_trace
            console.print(f"  Duration: {trace.duration_seconds:.2f}s")
            console.print(f"  Tool Calls: {len(trace.structured_tool_calls)}")
            console.print(f"  Rounds: {trace.total_rounds}")
            console.print(f"  Root Cause Found: {'Yes' if result.analysis.get('root_cause_analysis', {}).get('identified_correctly') else 'No'}")
            
            if trace.structured_tool_calls:
                console.print(f"  Tools Used: {', '.join(set(tc.tool_name for tc in trace.structured_tool_calls))}")
    
    def _save_enhanced_result(self, result: TestResult):
        """Save enhanced test result to file."""
        timestamp = datetime.now().strftime("%I%M%p.%m-%d-%Y")
        
        # Create results directory
        results_dir = Path("comprehensive-results-enhanced")
        results_dir.mkdir(exist_ok=True)
        
        # Save main result
        filename = results_dir / f"{result.scenario.name}.{timestamp}.json"
        
        # Convert to dict for JSON serialization
        result_dict = {
            "scenario": asdict(result.scenario) if hasattr(result.scenario, '__dataclass_fields__') else result.scenario.__dict__,
            "setup_success": result.setup_success,
            "debug_trace": result.debug_trace.to_dict() if result.debug_trace else None,
            "analysis": result.analysis,
            "cleanup_success": result.cleanup_success,
            "overall_success": result.overall_success,
            "recommendations": result.recommendations
        }
        
        with open(filename, "w") as f:
            json.dump(result_dict, f, indent=2, default=str)
        
        console.print(f"[green]Saved enhanced result to: {filename}[/green]")
        
        # Also save trace separately for detailed analysis
        if result.debug_trace:
            trace_dir = results_dir / "traces"
            trace_dir.mkdir(exist_ok=True)
            trace_file = trace_dir / f"{result.scenario.name}.{timestamp}_trace.json"
            
            with open(trace_file, "w") as f:
                json.dump(result.debug_trace.to_dict(), f, indent=2, default=str)
    
    async def run_all_scenarios(self):
        """Run all test scenarios."""
        scenarios = self._load_scenarios()
        
        if not scenarios:
            console.print("[red]No scenarios found![/red]")
            return
        
        console.print(f"[bold]Found {len(scenarios)} scenarios to test[/bold]")
        
        with Progress() as progress:
            task = progress.add_task("[cyan]Running tests...", total=len(scenarios))
            
            for scenario in scenarios:
                await self.run_comprehensive_test(scenario)
                progress.update(task, advance=1)
        
        # Display final summary
        self._display_final_summary()
    
    def _display_final_summary(self):
        """Display summary of all test results."""
        if not self.results:
            return
        
        successful = sum(1 for r in self.results if r.overall_success)
        total = len(self.results)
        
        table = Table(title="Test Results Summary")
        table.add_column("Scenario", style="cyan")
        table.add_column("Success", style="green")
        table.add_column("Duration", style="yellow")
        table.add_column("Tool Calls", style="blue")
        table.add_column("Rounds", style="magenta")
        table.add_column("Root Cause", style="white")
        
        for result in self.results:
            if result.debug_trace:
                table.add_row(
                    result.scenario.name,
                    "✅" if result.overall_success else "❌",
                    f"{result.debug_trace.duration_seconds:.1f}s",
                    str(len(result.debug_trace.structured_tool_calls)),
                    str(result.debug_trace.total_rounds),
                    "✅" if result.analysis.get("root_cause_analysis", {}).get("identified_correctly") else "❌"
                )
        
        console.print(table)
        console.print(f"\n[bold]Overall: {successful}/{total} successful ({successful/total*100:.1f}%)[/bold]")
    
    async def close(self):
        """Cleanup resources."""
        await self.debug_capture.close()


async def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Enhanced Comprehensive Kubernetes Testing with Full Data Capture"
    )
    parser.add_argument("--api-url", default="http://localhost:8080", help="Kubently API URL")
    parser.add_argument("--api-key", required=True, help="API key for authentication")
    parser.add_argument("--scenario", help="Run specific scenario")
    parser.add_argument("--use-gemini", action="store_true", help="Use Gemini for AI analysis")
    
    args = parser.parse_args()
    
    runner = EnhancedComprehensiveTestRunner(args.api_url, args.api_key, use_gemini=args.use_gemini)
    
    try:
        if args.scenario:
            # Run single scenario
            scenarios = runner._load_scenarios()
            scenario = next((s for s in scenarios if s.name == args.scenario), None)
            
            if scenario:
                await runner.run_comprehensive_test(scenario)
            else:
                console.print(f"[red]Scenario '{args.scenario}' not found[/red]")
        else:
            # Run all scenarios
            await runner.run_all_scenarios()
        
    finally:
        await runner.close()


if __name__ == "__main__":
    asyncio.run(main())