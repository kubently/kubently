#!/usr/bin/env python3
"""
Unified Test Runner for Kubently
Captures full debugging data and provides actionable analysis
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

# Import GeminiAnalyzer
from analyzer import GeminiAnalyzer

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if "--debug" in sys.argv else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# A2A Protocol imports
try:
    from a2a.client import A2AClient
    from a2a.types import Message, TextPart
    HAS_A2A_CLIENT = True
except ImportError:
    HAS_A2A_CLIENT = False
    logger.warning("Official A2A SDK not installed. Install with: pip install a2a")

# Rich console for pretty output
console = Console()


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
class DebugTrace:
    """Debug trace with full data from logs."""
    timestamp: str
    query: str
    
    # Core data
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
    debug_trace: Optional[DebugTrace]
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


class KubentlyDebugCapture:
    """Captures debug data from Kubently API using official A2A client."""
    
    def __init__(self, api_url: str, api_key: str):
        """Initialize debug capture."""
        self.api_url = api_url
        self.api_key = api_key
        self.session_id = str(uuid.uuid4())
        self.log_capture = LogCapture()
        
        # For now, always use httpx fallback as the official A2A client has a complex API
        # TODO: Implement proper A2AClient usage once we understand the card/consumer pattern
        self.use_a2a_client = False
        
        if False and HAS_A2A_CLIENT:
            # Ensure URL ends with /a2a for A2A protocol
            if not api_url.endswith('/a2a'):
                api_url = api_url.rstrip('/') + '/a2a'
            
            # TODO: Proper A2AClient initialization requires AgentCard and more setup
            self.use_a2a_client = True
        else:
            # Fallback to httpx for backward compatibility
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
            self.use_a2a_client = False
        
    async def execute_debug(
        self, 
        query: str,
        namespace: str,
        capture_thinking: bool = True
    ) -> DebugTrace:
        """Execute debug query with enhanced data capture from logs."""
        
        # Start log capture
        # NOTE: Temporarily disabled due to hanging issues
        # await self.log_capture.start_capture()
        # await asyncio.sleep(1)  # Let log capture initialize
        
        # Clear any previous events
        self.log_capture.clear()
        
        start_time = time.time()
        
        # Initialize trace
        trace = DebugTrace(
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
            context_id = None
            final_response = ""
            
            if self.use_a2a_client:
                # Use official A2A client
                message = Message(
                    role="user",
                    parts=[TextPart(text=query)]
                )
                
                # Send message and process streaming response
                async for event in self.a2a_client.send_message_streaming(message):
                    if hasattr(event, 'context_id') and not context_id:
                        context_id = event.context_id
                    
                    # Handle different event types
                    if hasattr(event, 'message') and event.message:
                        # Extract text from message parts
                        for part in event.message.parts:
                            if hasattr(part, 'text') and part.text:
                                final_response = part.text
                    
                    elif hasattr(event, 'artifact') and event.artifact:
                        # Extract text from artifact parts
                        for part in event.artifact.parts:
                            if hasattr(part, 'text') and part.text:
                                final_response = part.text
                    
                    # Check if task is completed
                    if hasattr(event, 'status') and event.status:
                        if event.status.state == "completed":
                            break
            else:
                # Fallback to manual SSE parsing
                request = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "message/stream",
                    "params": {
                        "message": {
                            "messageId": str(uuid.uuid4()),
                            "role": "user",
                            "parts": [{"partId": "p1", "text": query}]
                        }
                    }
                }
                
                async with self.client.stream("POST", "/", json=request) as response:
                    if response.status_code != 200:
                        logger.error(f"API returned {response.status_code}")
                        trace.errors.append(f"HTTP {response.status_code}")
                    else:
                        # Process SSE stream
                        async for line in response.aiter_lines():
                            if line.startswith("data: "):
                                try:
                                    data = json.loads(line[6:])
                                    
                                    # Debug: Log what we're receiving
                                    if "result" in data and data["result"].get("kind"):
                                        logger.debug(f"Received event kind: {data['result'].get('kind')}")
                                    
                                    if "result" in data:
                                        result = data["result"]
                                        
                                        # Get context ID from first response
                                        if not context_id and "contextId" in result:
                                            context_id = result["contextId"]
                                        
                                        # Check for text in status updates
                                        if result.get("kind") == "status-update":
                                            message = result.get("status", {}).get("message", {})
                                            if message and "parts" in message:
                                                for part in message["parts"]:
                                                    if part.get("kind") == "text":
                                                        text = part.get("text", "")
                                                        # Check if this is a tool call message
                                                        if "🔧 Tool Call:" in text:
                                                            # Parse tool call from the message
                                                            import re
                                                            tool_match = re.search(r'🔧 Tool Call: (\w+)\((.*?)\)', text, re.DOTALL)
                                                            if tool_match:
                                                                tool_name = tool_match.group(1)
                                                                args_str = tool_match.group(2)
                                                                try:
                                                                    args = json.loads(args_str) if args_str.strip() else {}
                                                                except:
                                                                    args = {"raw": args_str}
                                                                
                                                                # Extract result if present
                                                                result_text = None
                                                                if "✅ Result:" in text:
                                                                    result_match = re.search(r'✅ Result: (.+)', text, re.DOTALL)
                                                                    if result_match:
                                                                        result_text = result_match.group(1).strip()
                                                                
                                                                tool_call = ToolCall(
                                                                    timestamp=datetime.now().isoformat(),
                                                                    tool_name=tool_name,
                                                                    parameters=args,
                                                                    output=result_text,
                                                                    success=bool(result_text and "❌ Error:" not in text)
                                                                )
                                                                trace.structured_tool_calls.append(tool_call)
                                                                trace.tool_calls.append({
                                                                    "timestamp": tool_call.timestamp,
                                                                    "tool": tool_call.tool_name,
                                                                    "args": tool_call.parameters,
                                                                    "result": tool_call.output
                                                                })
                                                        else:
                                                            final_response = text
                                        
                                        # Check for artifact updates
                                        elif result.get("kind") == "artifact-update":
                                            artifact = result.get("artifact", {})
                                            if "parts" in artifact:
                                                for part in artifact["parts"]:
                                                    if part.get("kind") == "text":
                                                        final_response = part.get("text", "")
                                                    # Note: Tool calls are not exposed in the A2A protocol
                                                    # They happen internally but aren't sent as SSE events
                                        
                                        # Check if completed
                                        if result.get("kind") == "status-update" and result.get("final"):
                                            if result.get("status", {}).get("state") == "completed":
                                                break
                                except json.JSONDecodeError:
                                    logger.warning(f"Failed to parse SSE line: {line}")
            
            # Store the final response
            if final_response:
                trace.responses.append(final_response)
            
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
            # NOTE: Temporarily disabled due to hanging issues
            # await self.log_capture.stop_capture()
            pass
        
        # Finalize trace
        trace.duration_seconds = time.time() - start_time
        
        return trace
    
    def _process_structured_events(self, trace: DebugTrace, events: List[Dict]):
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
    
    async def close(self):
        """Close the client connection."""
        if self.use_a2a_client:
            if hasattr(self.a2a_client, 'close'):
                await self.a2a_client.close()
        else:
            await self.client.aclose()


class TestRunner:
    """Unified test runner with full data capture and analysis."""
    
    def __init__(self, api_url: str, api_key: str, results_dir: Optional[str] = None,
                 skip_analysis: bool = False, full_analysis: bool = False):
        self.api_url = api_url
        self.api_key = api_key
        self.debug_capture = KubentlyDebugCapture(api_url, api_key)
        self.results = []
        self.skip_analysis = skip_analysis
        self.full_analysis = full_analysis  # If True, do full analysis. If False, RCA-only.

        # Only initialize analyzer if not skipping analysis
        if not skip_analysis:
            self.analyzer = GeminiAnalyzer()
        else:
            self.analyzer = None
        
        # Create timestamped results directory
        if results_dir:
            self.results_dir = Path(results_dir)
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.results_dir = Path(f"test-results-{timestamp}")
        
        self.results_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Results will be saved to: {self.results_dir}")
        
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
            
            # Extract namespace - first try NAMESPACE variable, then kubectl create namespace command
            namespace_match = re.search(r'NAMESPACE="?([^"\s]+)"?', content)
            if not namespace_match:
                # Try to find namespace from kubectl create namespace command
                namespace_match = re.search(r'create\s+namespace\s+([^\s]+)', content)
            
            if namespace_match:
                namespace = namespace_match.group(1)
            else:
                # Use a generic namespace name that doesn't reveal the issue
                scenario_num = re.search(r'^(\d+)-', file_path.stem)
                if scenario_num:
                    namespace = f"test-ns-{scenario_num.group(1)}"
                else:
                    namespace = f"test-ns-{file_path.stem[:8]}"  # Use first 8 chars of filename
            
            # Extract expected fix from comments
            expected_fix = "Unknown"
            for line in content.split('\n'):
                if 'Expected fix:' in line or 'expected fix:' in line or 'THE FIX:' in line:
                    expected_fix = line.split(':', 1)[1].strip()
            
            # Create query template based on scenario name or hint
            name = file_path.stem
            if "imagepull" in name.lower():
                query_template = "In cluster kind, there's an issue with a pod in namespace {namespace}. The pod is showing ImagePullBackOff status. Please investigate and fix the issue."
            elif "crash" in name.lower():
                query_template = "In cluster kind, a pod in namespace {namespace} is in CrashLoopBackOff. Please diagnose the issue."
            elif "service" in name.lower():
                query_template = "In cluster kind, the service in namespace {namespace} doesn't seem to be working. Traffic isn't reaching the pods. Please investigate."
            elif "rbac" in name.lower():
                query_template = "In cluster kind, a pod in namespace {namespace} is encountering authorization issues. Please diagnose and make recommendations."
            elif "cross-namespace" in name.lower() or "namespace-a" in content:
                # For cross-namespace scenarios, use a generic query that doesn't reveal the issue
                query_template = "In cluster kind, there are connectivity issues between namespaces. Please investigate and identify the root cause."
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
    
    async def run_test(self, scenario: TestScenario) -> TestResult:
        """Run a test with full data capture."""
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
        
        debug_trace = await self.debug_capture.execute_debug(
            query=query,
            namespace=scenario.namespace
        )
        
        # Analyze results (if not skipping)
        if self.skip_analysis:
            analysis = {"skipped": True, "reason": "Analysis disabled"}
        else:
            analysis = await self._analyze_results(scenario, debug_trace)
        
        # Cleanup
        cleanup_success = await self._cleanup_scenario(scenario)
        
        # Determine overall success
        if self.skip_analysis:
            # Without analysis, success is based on setup and cleanup only
            overall_success = setup_success and cleanup_success
        else:
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
        
        # Save result
        self._save_result(result)
        
        return result
    
    async def _setup_scenario(self, scenario: TestScenario) -> bool:
        """Setup test scenario."""
        try:
            console.print(f"[cyan]Setting up scenario: {scenario.name}[/cyan]")
            result = subprocess.run(
                ["bash", str(scenario.path), "setup"],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode != 0:
                console.print(f"[red]Setup failed with return code: {result.returncode}[/red]")
                console.print(f"[red]STDOUT:[/red] {result.stdout}")
                console.print(f"[red]STDERR:[/red] {result.stderr}")
            else:
                console.print(f"[green]Setup completed successfully[/green]")
                if result.stdout:
                    console.print(f"[dim]Setup output: {result.stdout[:200]}...[/dim]")
            
            return result.returncode == 0
        except Exception as e:
            logger.error(f"Setup failed for {scenario.name}: {e}")
            console.print(f"[red]Setup exception: {e}[/red]")
            return False
    
    async def _cleanup_scenario(self, scenario: TestScenario) -> bool:
        """Cleanup test scenario."""
        try:
            result = subprocess.run(
                ["bash", str(scenario.path), "cleanup"],
                capture_output=True,
                text=True,
                timeout=60  # Increased timeout for namespace deletion
            )
            return result.returncode == 0
        except Exception as e:
            logger.error(f"Cleanup failed for {scenario.name}: {e}")
            return False
    
    async def _analyze_results(
        self, 
        scenario: TestScenario, 
        trace: DebugTrace
    ) -> Dict[str, Any]:
        """Analyze test results using Gemini."""
        
        if not trace:
            return {"error": "No trace data"}
        
        try:
            # Use GeminiAnalyzer with RCA-only by default (unless full_analysis is True)
            analysis = await self.analyzer.analyze_trace(trace, scenario, rca_only=(not self.full_analysis))
            return analysis
        except Exception as e:
            logger.error(f"Gemini analysis failed: {e}")
            # If Gemini fails, raise the error (no fallback)
            raise
    
    
    def _generate_recommendations(
        self,
        scenario: TestScenario,
        trace: Optional[DebugTrace],
        analysis: Dict[str, Any]
    ) -> List[str]:
        """Generate recommendations based on Gemini analysis."""
        if self.skip_analysis:
            return ["Analysis was skipped - run with analysis enabled for recommendations"]
        # Use recommendations from Gemini analysis
        return analysis.get("recommendations", [])
    
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
    
    def _save_result(self, result: TestResult):
        """Save test result to file."""
        # Save main result
        filename = self.results_dir / f"{result.scenario.name}.json"
        
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
        
        # Also save trace separately for detailed analysis
        if result.debug_trace:
            trace_dir = self.results_dir / "traces"
            trace_dir.mkdir(exist_ok=True)
            trace_file = trace_dir / f"{result.scenario.name}_trace.json"
            
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
                await self.run_test(scenario)
                progress.update(task, advance=1)
        
        # Display final summary
        self._display_final_summary()
        
        # Save summary
        self._save_summary()
    
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
    
    def _save_summary(self):
        """Save test summary."""
        summary = {
            "timestamp": datetime.now().isoformat(),
            "results_dir": str(self.results_dir),
            "total_scenarios": len(self.results),
            "successful": sum(1 for r in self.results if r.overall_success),
            "success_rate": sum(1 for r in self.results if r.overall_success) / max(len(self.results), 1) * 100,
            "scenarios": [
                {
                    "name": r.scenario.name,
                    "success": r.overall_success,
                    "duration": r.debug_trace.duration_seconds if r.debug_trace else 0,
                    "tool_calls": len(r.debug_trace.structured_tool_calls) if r.debug_trace else 0,
                    "rounds": r.debug_trace.total_rounds if r.debug_trace else 0
                }
                for r in self.results
            ]
        }
        
        with open(self.results_dir / "summary.json", "w") as f:
            json.dump(summary, f, indent=2)
    
    async def close(self):
        """Cleanup resources."""
        await self.debug_capture.close()


async def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Unified Kubernetes Testing with Full Data Capture"
    )
    parser.add_argument("--api-url", default="http://localhost:8080", help="Kubently API URL")
    parser.add_argument("--api-key", required=True, help="API key for authentication")
    parser.add_argument("--scenario", help="Run specific scenario")
    parser.add_argument("--results-dir", help="Directory to save results (default: timestamped)")
    parser.add_argument("--skip-analysis", action="store_true", help="Skip Gemini analysis")
    parser.add_argument("--full-analysis", action="store_true",
                        help="Run full improvement analysis (default: RCA-only)")

    args = parser.parse_args()

    runner = TestRunner(args.api_url, args.api_key, args.results_dir,
                        skip_analysis=args.skip_analysis,
                        full_analysis=args.full_analysis)
    
    try:
        if args.scenario:
            # Run single scenario
            scenarios = runner._load_scenarios()
            scenario = next((s for s in scenarios if s.name == args.scenario), None)
            
            if scenario:
                await runner.run_test(scenario)
            else:
                console.print(f"[red]Scenario '{args.scenario}' not found[/red]")
        else:
            # Run all scenarios
            await runner.run_all_scenarios()
        
        console.print(f"\n[green]Results saved to: {runner.results_dir}[/green]")
        
    finally:
        await runner.close()


if __name__ == "__main__":
    asyncio.run(main())