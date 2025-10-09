#!/usr/bin/env python3
"""
Comprehensive Kubernetes Test Runner with Full Data Capture
Integrates with Google Gemini for intelligent AI-powered analysis
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table
from rich.syntax import Syntax

# Import GeminiAnalyzer for analysis
try:
    from gemini_analyzer import GeminiAnalyzer
except ImportError:
    GeminiAnalyzer = None

# Setup paths
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "comprehensive-results"
LOGS_DIR = OUTPUT_DIR / "logs"
TRACES_DIR = OUTPUT_DIR / "traces"
ANALYSIS_DIR = OUTPUT_DIR / "analysis"

# Create directories
for dir_path in [OUTPUT_DIR, LOGS_DIR, TRACES_DIR, ANALYSIS_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOGS_DIR / f"test_runner_{datetime.now():%Y%m%d_%H%M%S}.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

console = Console()


@dataclass
class TestScenario:
    """Test scenario configuration."""
    name: str
    path: Path
    expected_fix: str
    namespace: str
    query_template: str
    validation_checks: List[str]


@dataclass
class DebugTrace:
    """Captures complete debug trace."""
    timestamp: str
    query: str
    thinking_steps: List[Dict]
    tool_calls: List[Dict]
    responses: List[str]
    errors: List[str]
    duration_seconds: float
    token_usage: Dict[str, int]


@dataclass
class TestResult:
    """Complete test result."""
    scenario: TestScenario
    setup_success: bool
    debug_trace: Optional[DebugTrace]
    analysis: Dict[str, Any]
    cleanup_success: bool
    overall_success: bool
    recommendations: List[str]


class KubentlyDebugCapture:
    """Captures all data from Kubently debug sessions."""
    
    def __init__(self, api_url: str, api_key: str):
        """Initialize debug capture."""
        self.api_url = api_url
        self.api_key = api_key
        self.session_id = str(uuid.uuid4())
        
        # HTTP client with extended timeout for streaming
        # Ensure URL ends with /a2a for A2A protocol
        if not api_url.endswith('/a2a'):
            api_url = api_url.rstrip('/') + '/a2a'
        
        self.client = httpx.AsyncClient(
            base_url=api_url,
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json",
            },
            timeout=120.0,  # Extended timeout for complex scenarios
        )
        
        # Data capture storage
        self.current_trace = None
        self.thinking_buffer = []
        self.tool_buffer = []
        self.response_buffer = []
    
    async def execute_debug_with_capture(
        self, 
        query: str,
        namespace: str,
        capture_thinking: bool = True
    ) -> DebugTrace:
        """
        Execute debug query with comprehensive data capture.
        """
        start_time = time.time()
        
        # Initialize trace
        self.current_trace = DebugTrace(
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
            # Create enhanced A2A request with capture flags
            request = {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "message/send",
                "params": {
                    "message": {
                        "messageId": str(uuid.uuid4()),
                        "role": "user",
                        "parts": [{"text": query}]  # Send query directly, namespace is in the query
                    }
                    # Note: contextId and metadata may not be supported by current A2A implementation
                }
            }
            
            # Send request and capture streaming response
            response = await self._send_with_streaming_capture(request)
            
            # Process captured data
            if response:
                await self._process_response(response)
            
        except Exception as e:
            logger.error(f"Debug execution error: {e}")
            self.current_trace.errors.append(str(e))
        
        # Finalize trace
        self.current_trace.duration_seconds = time.time() - start_time
        
        return self.current_trace
    
    async def _send_with_streaming_capture(self, request: Dict) -> Dict:
        """Send request and capture streaming data."""
        try:
            # Try regular POST first (A2A typically doesn't use SSE)
            response = await self.client.post("/", json=request)
            
            # Check for successful response
            if response.status_code != 200:
                logger.error(f"API returned {response.status_code}: {response.text[:200]}")
                return {
                    "error": f"HTTP {response.status_code}",
                    "message": response.text[:500]
                }
            
            return response.json()
                
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error: {e}")
            return {
                "error": f"HTTP {e.response.status_code}",
                "message": str(e)
            }
        except Exception as e:
            logger.error(f"Request failed: {e}")
            return {
                "error": "Request failed",
                "message": str(e)
            }
    
    async def _handle_stream_event(self, event: Dict) -> None:
        """Handle individual streaming event."""
        event_type = event.get("type", "")
        
        if event_type == "thinking":
            # Capture thinking/reasoning steps
            self.current_trace.thinking_steps.append({
                "timestamp": datetime.now().isoformat(),
                "content": event.get("content", ""),
                "confidence": event.get("confidence", 0),
            })
            
        elif event_type == "tool_call":
            # Capture tool execution
            self.current_trace.tool_calls.append({
                "timestamp": datetime.now().isoformat(),
                "tool": event.get("tool", ""),
                "args": event.get("args", {}),
                "result": event.get("result", ""),
                "duration_ms": event.get("duration_ms", 0),
            })
            
        elif event_type == "response":
            # Capture response chunks
            self.current_trace.responses.append(event.get("content", ""))
            
        elif event_type == "token_usage":
            # Capture token usage
            self.current_trace.token_usage.update(event.get("usage", {}))
    
    async def _process_response(self, response: Dict) -> None:
        """Process the complete response."""
        if "result" in response:
            result = response["result"]
            
            # Extract artifacts (final response)
            if "artifacts" in result:
                for artifact in result.get("artifacts", []):
                    if "parts" in artifact:
                        for part in artifact["parts"]:
                            if "text" in part:
                                self.current_trace.responses.append(part["text"])
            
            # Extract metadata - try multiple possible locations
            metadata = result.get("metadata", {})
            
            # Also check for tool_calls and thinking at the root level
            if "tool_calls" in result:
                for call in result["tool_calls"]:
                    self.current_trace.tool_calls.append(call)
            elif "tool_calls" in metadata:
                for call in metadata["tool_calls"]:
                    self.current_trace.tool_calls.append(call)
            
            # Check for thinking/reasoning steps
            if "thinking" in result:
                for step in result["thinking"]:
                    self.current_trace.thinking_steps.append(step)
            elif "thinking" in metadata:
                for step in metadata["thinking"]:
                    self.current_trace.thinking_steps.append(step)
            elif "reasoning" in result:
                for step in result["reasoning"]:
                    self.current_trace.thinking_steps.append(step)
            
            # Extract token usage from various possible locations
            if "token_usage" in result:
                self.current_trace.token_usage.update(result["token_usage"])
            elif "token_usage" in metadata:
                self.current_trace.token_usage.update(metadata["token_usage"])
            elif "usage" in result:
                self.current_trace.token_usage.update(result["usage"])
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()


class GeminiIntegration:
    """Direct integration with Gemini for AI-powered analysis."""
    
    def __init__(self):
        """Initialize Gemini integration."""
        self.google_api_key = os.getenv("GOOGLE_API_KEY")
        self.system_prompt = self._load_system_prompt()
        self.has_gemini = False
        self.model = None
        
        if self.google_api_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=self.google_api_key)
                self.model = genai.GenerativeModel('gemini-2.0-flash')
                self.has_gemini = True
                logger.info("Gemini integration initialized successfully")
            except ImportError:
                logger.warning("google-generativeai not installed. Run: pip install google-generativeai")
            except Exception as e:
                logger.error(f"Failed to initialize Gemini: {e}")
    
    def _load_system_prompt(self) -> str:
        """Load the current system prompt from file."""
        prompt_paths = [
            "../prompts/system.prompt.yaml",
            "prompts/system.prompt.yaml",
            "/etc/kubently/prompts/system.prompt.yaml"
        ]
        
        for path in prompt_paths:
            try:
                full_path = Path(path).resolve()
                if full_path.exists():
                    import yaml
                    with open(full_path, 'r') as f:
                        data = yaml.safe_load(f)
                        return data.get('content', 'System prompt not found')
            except Exception as e:
                logger.debug(f"Could not load prompt from {path}: {e}")
                continue
        
        # Fallback to default
        return "You are a multi-cluster Kubernetes debugging agent. Operate strictly read-only; never mutate cluster state."
        
    async def analyze_trace_with_gemini(
        self,
        scenario: TestScenario,
        trace: DebugTrace
    ) -> Dict[str, Any]:
        """
        Use Gemini API to analyze the debug trace.
        """
        if not self.has_gemini:
            return self._basic_analysis(scenario, trace)
        
        # Prepare comprehensive analysis request
        analysis_prompt = f"""
        Analyze this Kubernetes debugging session for the scenario: {scenario.name}
        
        CURRENT SYSTEM PROMPT:
        {self.system_prompt}
        
        Expected Fix: {scenario.expected_fix}
        
        Debug Query: {trace.query}
        
        Thinking Steps ({len(trace.thinking_steps)} steps):
        {json.dumps(trace.thinking_steps, indent=2)}
        
        Tool Calls ({len(trace.tool_calls)} calls):
        {json.dumps(trace.tool_calls, indent=2)}
        
        Final Response:
        {' '.join(trace.responses)}
        
        Token Usage: {trace.token_usage}
        Duration: {trace.duration_seconds:.2f} seconds
        
        Please analyze:
        1. Was the root cause correctly identified? Compare with expected fix.
        2. Rate the efficiency of tool usage (1-10 scale)
        3. Identify specific bottlenecks in the debugging process
        4. Suggest concrete improvements to the SYSTEM PROMPT shown above
        5. What tools or capabilities are missing?
        6. Rate overall solution quality (1-10) with justification
        7. Provide specific SYSTEM PROMPT improvements to address the agent's behavior
        """
        
        # Actually call Gemini if available
        if self.has_gemini and self.model:
            try:
                prompt = analysis_prompt + """\n\nProvide a JSON response with these fields:
- root_cause_analysis: Did the agent identify the correct issue?
- efficiency_metrics: How efficient was the investigation?
- bottlenecks: What slowed down the investigation?
- improvements: What SYSTEM PROMPT changes would improve the agent's behavior?
- missing_capabilities: What tools or knowledge is the agent missing?
- overall_quality: Score and assessment
- prompt_suggestions: Specific SYSTEM PROMPT improvements to make the agent more effective

Focus on how to improve the SYSTEM PROMPT that configures the agent's behavior, NOT the user's query."""
                
                response = self.model.generate_content(prompt)
                response_text = response.text
                
                # Parse JSON from response
                if "```json" in response_text:
                    json_start = response_text.find("```json") + 7
                    json_end = response_text.find("```", json_start)
                    json_text = response_text[json_start:json_end]
                else:
                    json_text = response_text
                
                result = json.loads(json_text)
                result["model"] = "gemini-2.0-flash"
                result["timestamp"] = datetime.now().isoformat()
                return result
                
            except Exception as e:
                logger.error(f"Gemini analysis failed: {e}")
                # Fall back to heuristic analysis
                return self._basic_analysis(scenario, trace)
        
        # Fallback to heuristic analysis - should not reach here
        return self._basic_analysis(scenario, trace)
    
    def _basic_analysis(self, scenario: TestScenario, trace: DebugTrace) -> Dict:
        """Fallback basic analysis without Gemini."""
        return {
            "model": "basic_analyzer",
            "timestamp": datetime.now().isoformat(),
            "root_cause_analysis": {
                "identified_correctly": self._check_root_cause_match(trace, scenario.expected_fix),
                "confidence": 0.5,
            },
            "efficiency_metrics": {
                "tool_usage_score": self._calculate_tool_efficiency(trace),
                "response_time": trace.duration_seconds,
            },
            "bottlenecks": self._identify_bottlenecks(trace),
        }
    
    def _check_root_cause_match(self, trace: DebugTrace, expected_fix: str) -> bool:
        """Check if root cause matches expected fix."""
        response_text = ' '.join(trace.responses).lower()
        fix_keywords = expected_fix.lower().split()
        matches = sum(1 for keyword in fix_keywords if keyword in response_text)
        return matches >= len(fix_keywords) * 0.6
    
    def _calculate_tool_efficiency(self, trace: DebugTrace) -> float:
        """Calculate tool usage efficiency score."""
        if not trace.tool_calls:
            return 0.0
        
        # Factors: unique tools, duplicate calls, total calls
        unique_tools = len(set(call["tool"] for call in trace.tool_calls))
        total_calls = len(trace.tool_calls)
        
        # Check for duplicate calls
        call_signatures = [f"{c['tool']}:{json.dumps(c.get('args', {}))}" for c in trace.tool_calls]
        unique_calls = len(set(call_signatures))
        duplicate_ratio = 1 - (unique_calls / total_calls) if total_calls > 0 else 0
        
        # Calculate score (10 = perfect efficiency)
        score = 10.0
        score -= duplicate_ratio * 3  # Penalize duplicates
        score -= max(0, (total_calls - 10) * 0.2)  # Penalize excessive calls
        
        return max(1.0, min(10.0, score))
    
    def _calculate_token_efficiency(self, trace: DebugTrace) -> float:
        """Calculate token usage efficiency."""
        total_tokens = trace.token_usage.get("total", 0)
        if total_tokens == 0:
            return 0.0
        
        # Rough efficiency: good if < 2000 tokens for simple scenarios
        if total_tokens < 2000:
            return 10.0
        elif total_tokens < 5000:
            return 7.0
        elif total_tokens < 10000:
            return 5.0
        else:
            return 3.0
    
    def _identify_bottlenecks(self, trace: DebugTrace) -> List[str]:
        """Identify performance bottlenecks."""
        bottlenecks = []
        
        # Check tool call patterns
        kubectl_calls = [c for c in trace.tool_calls if "kubectl" in c.get("tool", "").lower()]
        
        if len(kubectl_calls) > 10:
            bottlenecks.append(f"Excessive kubectl calls ({len(kubectl_calls)})")
        
        # Check for repeated calls
        call_counts = {}
        for call in trace.tool_calls:
            key = f"{call['tool']}:{call.get('args', {}).get('command', '')}"
            call_counts[key] = call_counts.get(key, 0) + 1
        
        for call_key, count in call_counts.items():
            if count > 2:
                bottlenecks.append(f"Repeated call: {call_key} ({count} times)")
        
        # Check thinking steps
        if len(trace.thinking_steps) > 20:
            bottlenecks.append(f"Excessive thinking steps ({len(trace.thinking_steps)})")
        
        # Check response time
        if trace.duration_seconds > 30:
            bottlenecks.append(f"Slow response time ({trace.duration_seconds:.1f}s)")
        
        return bottlenecks
    
    def _generate_improvements(self, trace: DebugTrace, scenario: TestScenario) -> List[str]:
        """Generate SYSTEM PROMPT improvement recommendations."""
        improvements = []
        
        response_text = " ".join(trace.responses).lower() if trace.responses else ""
        
        # Based on tool usage patterns
        if len(trace.tool_calls) == 0:
            improvements.append("System prompt should include: 'Always begin by using kubectl to list and describe resources in the namespace'")
        elif len(trace.tool_calls) > 15:
            improvements.append("System prompt should include: 'Be efficient - avoid redundant kubectl calls by caching results'")
        
        # Based on investigation approach
        if "could you" in response_text or "please provide" in response_text:
            improvements.append("System prompt should include: 'Never ask the user for information - investigate autonomously'")
        
        # Based on problem identification
        if not any("root cause" in r.lower() for r in trace.responses):
            improvements.append("System prompt should include: 'Always explicitly state the root cause of the issue'")
        
        if not any("recommend" in r.lower() or "suggestion" in r.lower() for r in trace.responses):
            improvements.append("System prompt should include: 'Always provide clear, actionable recommendations'")
        
        # Scenario-specific system prompt improvements
        if ("permission" in scenario.name.lower() or "rbac" in scenario.name.lower()) and len(trace.tool_calls) < 3:
            improvements.append("System prompt should include RBAC investigation steps: check ServiceAccount, Roles, and RoleBindings")
        
        return improvements
    
    def _identify_missing_tools(self, trace: DebugTrace, scenario: TestScenario) -> List[str]:
        """Identify missing tools or capabilities."""
        missing = []
        
        tools_used = set(call["tool"] for call in trace.tool_calls)
        
        # Scenario-specific tool recommendations
        if "configmap" in scenario.name.lower() and "configmap" not in str(tools_used):
            missing.append("ConfigMap-specific inspection tool")
        
        if "oom" in scenario.name.lower() and "metrics" not in str(tools_used):
            missing.append("Memory metrics collection tool")
        
        if "network" in scenario.name.lower() and "netpol" not in str(tools_used):
            missing.append("Network policy analyzer tool")
        
        return missing
    
    def _rate_overall_quality(self, trace: DebugTrace, scenario: TestScenario) -> Dict:
        """Rate overall solution quality."""
        score = 5.0  # Base score
        
        # Positive factors
        if self._check_root_cause_match(trace, scenario.expected_fix):
            score += 2.0
        
        if trace.duration_seconds < 20:
            score += 1.0
        
        if len(trace.errors) == 0:
            score += 1.0
        
        if any("kubectl apply" in str(c) or "kubectl patch" in str(c) for c in trace.tool_calls):
            score += 1.0  # Provided actionable fix
        
        # Negative factors
        if len(trace.errors) > 0:
            score -= 2.0
        
        if trace.duration_seconds > 60:
            score -= 1.0
        
        score = max(1.0, min(10.0, score))
        
        return {
            "score": score,
            "justification": self._justify_quality_score(score)
        }
    
    def _justify_quality_score(self, score: float) -> str:
        """Provide justification for quality score."""
        if score >= 8:
            return "Excellent - accurate diagnosis with efficient solution"
        elif score >= 6:
            return "Good - correct analysis with room for optimization"
        elif score >= 4:
            return "Fair - partial success with notable issues"
        else:
            return "Poor - significant problems in analysis or solution"
    
    def _generate_prompt_suggestions(self, trace: DebugTrace, scenario: TestScenario) -> List[str]:
        """Generate SYSTEM prompt improvement suggestions based on agent behavior."""
        suggestions = []
        
        # Analyze agent's response patterns
        response_text = " ".join(trace.responses).lower() if trace.responses else ""
        
        # Check if agent is asking questions instead of investigating
        if "could you please provide" in response_text or "what is the name" in response_text:
            suggestions.append("System prompt should instruct agent to proactively investigate all pods in the namespace rather than asking for specific pod names")
        
        # Check if agent mentions it can't fix issues
        if "won't be able to fix" in response_text or "read-only mode" in response_text:
            suggestions.append("System prompt should clarify that 'diagnose and make recommendations' is the goal, not to apologize for read-only limitations")
        
        # Check if agent failed to use kubectl tools
        if len(trace.tool_calls) == 0 and "kubectl" not in response_text:
            suggestions.append("System prompt should emphasize using kubectl commands immediately to investigate issues")
        
        # Check for efficient tool usage
        if len(trace.tool_calls) > 15:
            suggestions.append("System prompt should emphasize efficient tool usage and avoiding redundant calls")
        
        # Check for systematic investigation
        if len(trace.tool_calls) < 3:
            suggestions.append("System prompt should outline a systematic investigation approach: 1) List pods, 2) Describe problematic pods, 3) Check logs/events")
        
        # Check for namespace awareness
        if trace.tool_calls and not any(scenario.namespace in str(call) for call in trace.tool_calls):
            suggestions.append("System prompt should emphasize always using the provided namespace in kubectl commands")
        
        return suggestions


class ComprehensiveTestRunner:
    """Main test runner with full integration."""
    
    def __init__(self, api_url: str, api_key: str, use_gemini: bool = False):
        """Initialize test runner."""
        self.api_url = api_url
        self.api_key = api_key
        self.use_gemini = use_gemini
        self.debug_capture = KubentlyDebugCapture(api_url, api_key)
        self.gemini_analyzer = GeminiIntegration() if use_gemini else None
        self.results: List[TestResult] = []
        self.system_prompt = self._load_system_prompt()
        
    async def run_comprehensive_test(self, scenario: TestScenario) -> TestResult:
        """Run comprehensive test for a scenario."""
        console.print(Panel(
            f"[bold cyan]Testing Scenario: {scenario.name}[/bold cyan]\n"
            f"Expected Fix: {scenario.expected_fix}",
            title="Test Execution"
        ))
        
        # Setup phase
        with console.status("Setting up scenario..."):
            setup_success = await self._setup_scenario(scenario)
            
        if not setup_success:
            console.print("[red]✗ Setup failed[/red]")
            return TestResult(
                scenario=scenario,
                setup_success=False,
                debug_trace=None,
                analysis={},
                cleanup_success=False,
                overall_success=False,
                recommendations=["Fix scenario setup script"]
            )
        
        console.print("[green]✓ Setup complete[/green]")
        await asyncio.sleep(5)  # Wait for resources to stabilize
        
        # Debug phase with full capture
        with console.status("Running debug session with full data capture..."):
            debug_trace = await self.debug_capture.execute_debug_with_capture(
                query=scenario.query_template.format(namespace=scenario.namespace),
                namespace=scenario.namespace,
                capture_thinking=True
            )
        
        console.print(f"[green]✓ Debug complete[/green] ({debug_trace.duration_seconds:.2f}s)")
        
        # Analysis phase
        with console.status("Analyzing with Gemini..." if self.use_gemini else "Analyzing..."):
            if self.gemini_analyzer:
                analysis = await self.gemini_analyzer.analyze_trace_with_gemini(scenario, debug_trace)
            else:
                # Use basic heuristic analysis
                analysis = self._basic_heuristic_analysis(scenario, debug_trace)
        
        console.print("[green]✓ Analysis complete[/green]")
        
        # Cleanup phase
        with console.status("Cleaning up..."):
            cleanup_success = await self._cleanup_scenario(scenario)
        
        # Create result
        result = TestResult(
            scenario=scenario,
            setup_success=setup_success,
            debug_trace=debug_trace,
            analysis=analysis,
            cleanup_success=cleanup_success,
            overall_success=setup_success and cleanup_success and analysis.get("root_cause_analysis", {}).get("identified_correctly", False),
            recommendations=analysis.get("improvements", []) + analysis.get("prompt_suggestions", [])
        )
        
        # Display results
        self._display_test_result(result)
        
        # Save results
        await self._save_test_result(result)
        
        return result
    
    async def _setup_scenario(self, scenario: TestScenario) -> bool:
        """Setup test scenario."""
        try:
            # Read scenario file and remove watch commands
            with open(scenario.path, 'r') as f:
                script_content = f.read()
            
            # Create temp script
            temp_script = Path("/tmp") / f"scenario_{scenario.name}.sh"
            with open(temp_script, 'w') as f:
                f.write(script_content)
            
            # Make executable
            os.chmod(temp_script, 0o755)
            
            # Run the modified script with timeout
            process = await asyncio.create_subprocess_exec(
                "bash", str(temp_script),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=30.0  # 30 second timeout
                )
                
                if process.returncode != 0:
                    logger.error(f"Setup failed: {stderr.decode()}")
                    return False
                
                return True
                
            except asyncio.TimeoutError:
                logger.warning("Scenario setup timed out, but continuing")
                process.terminate()
                await process.wait()
                return True  # Continue anyway, namespace likely created
                
        except Exception as e:
            logger.error(f"Setup error: {e}")
            return False
        finally:
            # Clean up temp script
            if temp_script.exists():
                temp_script.unlink()
    
    async def _cleanup_scenario(self, scenario: TestScenario) -> bool:
        """Cleanup test scenario."""
        try:
            process = await asyncio.create_subprocess_exec(
                "kubectl", "delete", "namespace", scenario.namespace, "--ignore-not-found=true",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            await process.communicate()
            return process.returncode == 0
            
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
            return False
    
    def _display_test_result(self, result: TestResult) -> None:
        """Display test result summary."""
        table = Table(title=f"Results: {result.scenario.name}")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="yellow")
        
        # Basic metrics
        table.add_row("Setup", "✓" if result.setup_success else "✗")
        table.add_row("Cleanup", "✓" if result.cleanup_success else "✗")
        
        if result.debug_trace:
            table.add_row("Duration", f"{result.debug_trace.duration_seconds:.2f}s")
            table.add_row("Tool Calls", str(len(result.debug_trace.tool_calls)))
            table.add_row("Thinking Steps", str(len(result.debug_trace.thinking_steps)))
            table.add_row("Tokens Used", str(result.debug_trace.token_usage.get("total", 0)))
        
        if result.analysis:
            root_cause = result.analysis.get("root_cause_analysis", {})
            table.add_row("Root Cause Found", "✓" if root_cause.get("identified_correctly") else "✗")
            
            quality = result.analysis.get("overall_quality", {})
            table.add_row("Quality Score", f"{quality.get('score', 0):.1f}/10")
        
        console.print("\n")
        console.print(table)
        
        # Show bottlenecks
        bottlenecks = result.analysis.get("bottlenecks", [])
        if bottlenecks:
            console.print("\n[yellow]Bottlenecks Found:[/yellow]")
            for b in bottlenecks[:5]:  # Show top 5
                console.print(f"  • {b}")
        
        # Show recommendations
        if result.recommendations:
            console.print("\n[green]Recommendations:[/green]")
            for r in result.recommendations[:5]:  # Show top 5
                console.print(f"  • {r}")
    
    async def _save_test_result(self, result: TestResult) -> None:
        """Save test result to files."""
        # Create more human-readable timestamp
        now = datetime.now()
        date_str = now.strftime("%m-%d-%Y")  # MM-DD-YYYY format
        time_str = now.strftime("%H%M%S")  # For uniqueness
        human_time = now.strftime("%I%M%p").lower().lstrip("0")  # 2:45pm format
        
        # Create human-readable filename: scenario_name.1245pm.09-08-2025.json
        # This makes it easier to identify
        result_file = OUTPUT_DIR / f"{result.scenario.name}.{human_time}.{date_str}.json"
        with open(result_file, "w") as f:
            # Convert to dict for JSON serialization
            result_dict = {
                "scenario": asdict(result.scenario),
                "setup_success": result.setup_success,
                "debug_trace": asdict(result.debug_trace) if result.debug_trace else None,
                "analysis": result.analysis,
                "cleanup_success": result.cleanup_success,
                "overall_success": result.overall_success,
                "recommendations": result.recommendations,
            }
            json.dump(result_dict, f, indent=2, default=str)
        
        # Save trace separately for detailed analysis
        if result.debug_trace:
            trace_file = TRACES_DIR / f"{result.scenario.name}.{human_time}.{date_str}_trace.json"
            with open(trace_file, "w") as f:
                json.dump(asdict(result.debug_trace), f, indent=2, default=str)
        
        console.print(f"\n[dim]Results saved to {result_file}[/dim]")
    
    async def run_all_scenarios(self) -> None:
        """Run all configured scenarios."""
        scenarios = self._load_scenarios()
        
        console.print(f"\n[bold]Running {len(scenarios)} test scenarios[/bold]\n")
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console,
        ) as progress:
            
            task = progress.add_task("Testing scenarios...", total=len(scenarios))
            
            for scenario in scenarios:
                result = await self.run_comprehensive_test(scenario)
                self.results.append(result)
                progress.update(task, advance=1)
                
                # Brief pause between scenarios
                await asyncio.sleep(3)
        
        # Generate final report
        await self._generate_final_report()
    
    def _load_scenarios(self) -> List[TestScenario]:
        """Load test scenarios dynamically from the scenarios directory."""
        scenarios = []
        
        # Define scenario configurations with expected fixes and query templates
        scenario_configs = {
            "01-imagepullbackoff-typo": {
                "fix": "Correct the image name from 'busyboxx:latest' to 'busybox:latest'",
                "query": "In cluster kind, there's an issue with a pod in namespace {namespace}. The pod is showing ImagePullBackOff status. Please investigate and fix the issue."
            },
            "02-imagepullbackoff-private": {
                "fix": "Add image pull secret for private registry authentication",
                "query": "In cluster kind, a pod in namespace {namespace} is stuck in ImagePullBackOff state. Please diagnose and resolve the issue."
            },
            "03-crashloopbackoff": {
                "fix": "Fix the invalid command or add proper error handling",
                "query": "In cluster kind, there's a pod in namespace {namespace} that keeps restarting. Please investigate why it's failing and provide a solution."
            },
            "04-runcontainer-missing-configmap": {
                "fix": "Create the missing ConfigMap or fix the reference",
                "query": "In cluster kind, a pod in namespace {namespace} is failing to start. Please investigate what's preventing it from running."
            },
            "05-runcontainer-missing-secret-key": {
                "fix": "Add the missing key to the Secret or fix the key reference",
                "query": "In cluster kind, there's a pod in namespace {namespace} that won't start. Please diagnose and make recommendations."
            },
            "06-oomkilled": {
                "fix": "Increase memory limits or optimize application memory usage",
                "query": "In cluster kind, a pod in namespace {namespace} was terminated unexpectedly. Please investigate what happened and provide a fix."
            },
            "07-failed-readiness-probe": {
                "fix": "Fix the readiness probe configuration or application endpoint",
                "query": "In cluster kind, there's a pod in namespace {namespace} that's running but not ready. Please investigate and resolve."
            },
            "08-failing-liveness-probe": {
                "fix": "Fix the liveness probe configuration or application health endpoint",
                "query": "In cluster kind, a pod in namespace {namespace} keeps getting restarted. Please find out why and fix it."
            },
            "09-mismatched-labels": {
                "fix": "Align pod labels with selector requirements",
                "query": "In cluster kind, there's a deployment in namespace {namespace} but the expected pods aren't showing up correctly. Please investigate."
            },
            "10-unschedulable-resources": {
                "fix": "Adjust resource requests to fit available node capacity",
                "query": "In cluster kind, a pod in namespace {namespace} is stuck in Pending state. Please diagnose why it can't be scheduled."
            },
            "11-unschedulable-taint": {
                "fix": "Add appropriate tolerations or remove node taints",
                "query": "In cluster kind, there's a pod in namespace {namespace} that remains in Pending status. Please investigate the scheduling issue."
            },
            "12-pvc-unbound": {
                "fix": "Create matching PV or fix PVC specifications",
                "query": "In cluster kind, there's a storage-related issue in namespace {namespace}. The application can't access its storage. Please fix it."
            },
            "13-service-selector-mismatch": {
                "fix": "Fix the Service selector to match pod labels",
                "query": "In cluster kind, the service in namespace {namespace} doesn't seem to be working. Traffic isn't reaching the pods. Please investigate."
            },
            "14-service-port-mismatch": {
                "fix": "Align Service port with container port",
                "query": "In cluster kind, we can't connect to the service in namespace {namespace}. Please debug the connectivity issue."
            },
            "15-network-policy-deny-ingress": {
                "fix": "Add appropriate NetworkPolicy ingress rules",
                "query": "In cluster kind, we're unable to reach the application in namespace {namespace} from outside. Please investigate the connectivity problem."
            },
            "16-network-policy-deny-egress": {
                "fix": "Add appropriate NetworkPolicy egress rules",
                "query": "In cluster kind, the application in namespace {namespace} can't connect to external services. Please diagnose and make recommendations."
            },
            "17-cross-namespace-block": {
                "fix": "Configure NetworkPolicy for cross-namespace communication",
                "query": "In cluster kind, services in namespace {namespace} can't communicate with other namespaces. Please investigate the issue."
            },
            "18-missing-serviceaccount": {
                "fix": "Create the missing ServiceAccount or fix the reference",
                "query": "In cluster kind, a pod in namespace {namespace} is failing to start. Please identify and fix the problem."
            },
            "19-rbac-forbidden-role": {
                "fix": "Grant appropriate RBAC permissions via Role/RoleBinding",
                "query": "In cluster kind, the application in namespace {namespace} is getting permission errors. Please investigate and resolve."
            },
            "20-rbac-forbidden-clusterrole": {
                "fix": "Grant appropriate RBAC permissions via ClusterRole/ClusterRoleBinding",
                "query": "In cluster kind, a pod in namespace {namespace} is encountering authorization issues. Please diagnose and make recommendations."
            }
        }
        
        # Check scenarios directory - try both possible locations
        scenarios_dir = Path("scenarios")
        if not scenarios_dir.exists():
            scenarios_dir = Path(__file__).parent / "scenarios"
        
        # Load all .sh files from scenarios directory
        if scenarios_dir.exists():
            for scenario_file in sorted(scenarios_dir.glob("*.sh")):
                name = scenario_file.stem  # Remove .sh extension
                
                # Skip run-all-scenarios
                if name == "run-all-scenarios":
                    continue
                
                # Get config or use defaults
                config = scenario_configs.get(name, {
                    "fix": f"Fix the issue in {name}",
                    "query": "In cluster kind, debug the issue in namespace {namespace}. Identify the root cause and provide the fix."
                })
                
                scenarios.append(TestScenario(
                    name=name,
                    path=scenario_file,
                    expected_fix=config["fix"],
                    namespace=f"test-scenario-{name.split('-')[0]}",
                    query_template=config["query"],
                    validation_checks=[]
                ))
        
        return scenarios
    
    async def _generate_final_report(self) -> None:
        """Generate comprehensive final report."""
        # Use same naming convention as test results
        now = datetime.now()
        date_str = now.strftime("%m-%d-%Y")  # MM-DD-YYYY format
        human_time = now.strftime("%I%M%p").lower().lstrip("0")
        
        # Create summary
        summary = {
            "timestamp": datetime.now().isoformat(),
            "total_scenarios": len(self.results),
            "successful": sum(1 for r in self.results if r.overall_success),
            "failed": sum(1 for r in self.results if not r.overall_success),
            "average_duration": sum(r.debug_trace.duration_seconds for r in self.results if r.debug_trace) / len(self.results),
            "total_tool_calls": sum(len(r.debug_trace.tool_calls) for r in self.results if r.debug_trace),
            "common_bottlenecks": self._find_common_bottlenecks(),
            "top_recommendations": self._aggregate_recommendations(),
        }
        
        # Save summary
        summary_file = ANALYSIS_DIR / f"final_report.{human_time}.{date_str}.json"
        with open(summary_file, "w") as f:
            json.dump(summary, f, indent=2, default=str)
        
        # Generate markdown report
        md_file = ANALYSIS_DIR / f"final_report.{human_time}.{date_str}.md"
        self._generate_markdown_report(md_file, summary)
        
        # Display summary
        console.print("\n" + "="*60)
        console.print(Panel(
            f"[bold green]Test Suite Complete[/bold green]\n\n"
            f"Total Scenarios: {summary['total_scenarios']}\n"
            f"Successful: {summary['successful']}\n"
            f"Failed: {summary['failed']}\n"
            f"Average Duration: {summary['average_duration']:.2f}s\n\n"
            f"Report saved to: {summary_file}",
            title="Final Summary"
        ))
    
    def _find_common_bottlenecks(self) -> List[str]:
        """Find common bottlenecks across all tests."""
        bottleneck_counts = {}
        
        for result in self.results:
            if result.analysis:
                for bottleneck in result.analysis.get("bottlenecks", []):
                    # Generalize bottleneck patterns
                    pattern = bottleneck.split("(")[0].strip()
                    bottleneck_counts[pattern] = bottleneck_counts.get(pattern, 0) + 1
        
        # Return top bottlenecks
        sorted_bottlenecks = sorted(bottleneck_counts.items(), key=lambda x: x[1], reverse=True)
        return [f"{pattern} ({count} occurrences)" for pattern, count in sorted_bottlenecks[:5]]
    
    def _aggregate_recommendations(self) -> List[str]:
        """Aggregate and prioritize recommendations."""
        rec_counts = {}
        
        for result in self.results:
            for rec in result.recommendations:
                rec_counts[rec] = rec_counts.get(rec, 0) + 1
        
        # Return top recommendations
        sorted_recs = sorted(rec_counts.items(), key=lambda x: x[1], reverse=True)
        return [f"{rec} (suggested {count} times)" for rec, count in sorted_recs[:10]]
    
    def _generate_markdown_report(self, file_path: Path, summary: Dict) -> None:
        """Generate detailed markdown report."""
        with open(file_path, "w") as f:
            f.write("# Kubently Comprehensive Test Report\n\n")
            f.write(f"**Generated:** {summary['timestamp']}\n\n")
            
            f.write("## Executive Summary\n\n")
            f.write(f"- **Total Scenarios Tested:** {summary['total_scenarios']}\n")
            f.write(f"- **Successful:** {summary['successful']}\n")
            f.write(f"- **Failed:** {summary['failed']}\n")
            f.write(f"- **Success Rate:** {(summary['successful']/summary['total_scenarios']*100):.1f}%\n")
            f.write(f"- **Average Debug Duration:** {summary['average_duration']:.2f} seconds\n")
            f.write(f"- **Total Tool Calls:** {summary['total_tool_calls']}\n\n")
            
            f.write("## Common Bottlenecks\n\n")
            for bottleneck in summary['common_bottlenecks']:
                f.write(f"- {bottleneck}\n")
            f.write("\n")
            
            f.write("## Top Recommendations\n\n")
            for rec in summary['top_recommendations']:
                f.write(f"1. {rec}\n")
            f.write("\n")
            
            f.write("## Detailed Scenario Results\n\n")
            for result in self.results:
                f.write(f"### {result.scenario.name}\n\n")
                f.write(f"- **Success:** {'✓' if result.overall_success else '✗'}\n")
                
                if result.debug_trace:
                    f.write(f"- **Duration:** {result.debug_trace.duration_seconds:.2f}s\n")
                    f.write(f"- **Tool Calls:** {len(result.debug_trace.tool_calls)}\n")
                    f.write(f"- **Tokens:** {result.debug_trace.token_usage.get('total', 0)}\n")
                
                if result.analysis:
                    quality = result.analysis.get("overall_quality", {})
                    f.write(f"- **Quality Score:** {quality.get('score', 0):.1f}/10\n")
                    f.write(f"- **Quality Justification:** {quality.get('justification', 'N/A')}\n")
                
                f.write("\n")
    
    def _basic_heuristic_analysis(self, scenario: TestScenario, trace: DebugTrace) -> Dict:
        """Basic heuristic analysis when Gemini is not available."""
        response_text = ' '.join(trace.responses).lower()
        expected_fix = scenario.expected_fix.lower()
        
        # Generate system prompt suggestions based on agent behavior
        prompt_suggestions = self._analyze_system_prompt_gaps(trace, scenario)
        
        # Check if root cause was found
        root_cause_found = any(
            keyword in response_text
            for keyword in expected_fix.split()
            if len(keyword) > 3
        )
        
        return {
            "model": "heuristic",
            "timestamp": datetime.now().isoformat(),
            "root_cause_analysis": {
                "identified_correctly": root_cause_found,
                "confidence": 0.5,
                "explanation": "Basic keyword matching analysis"
            },
            "efficiency_metrics": {
                "tool_usage_score": max(1, 10 - len(trace.tool_calls) // 2),
                "response_time": trace.duration_seconds,
            },
            "bottlenecks": [],
            "improvements": [],
            "missing_capabilities": [],
            "overall_quality": {
                "score": 7 if root_cause_found else 3,
                "justification": "Heuristic scoring based on root cause detection"
            },
            "prompt_suggestions": prompt_suggestions
        }
    
    def _analyze_system_prompt_gaps(self, trace: DebugTrace, scenario: TestScenario) -> List[str]:
        """Analyze gaps in system prompt based on agent behavior."""
        suggestions = []
        response_text = " ".join(trace.responses).lower() if trace.responses else ""
        
        # Check if agent asked for clarification instead of investigating
        if "could you" in response_text or "please provide" in response_text or "which namespace" in response_text:
            suggestions.append("Add to system prompt: 'When a namespace is provided (e.g., test-scenario-XX), use it directly without asking for confirmation'")
        
        # Check if agent failed to use tools
        if len(trace.tool_calls) == 0:
            suggestions.append("Add to system prompt: 'For debugging requests, immediately use kubectl commands to investigate - do not ask for permission or more information'")
        
        # Check if agent mentioned read-only limitations unnecessarily
        if "read-only" in response_text or "can't fix" in response_text or "won't be able" in response_text:
            suggestions.append("Add to system prompt: 'Focus on diagnosis and recommendations without mentioning read-only limitations'")
        
        # Check for specific scenario gaps
        if "test-scenario" in scenario.namespace and scenario.namespace not in str(trace.tool_calls):
            suggestions.append(f"Add to system prompt: 'When namespace is explicitly mentioned in the query (like {scenario.namespace}), use it in all kubectl commands'")
        
        return suggestions
    
    def _load_system_prompt(self) -> str:
        """Load the current system prompt from file."""
        prompt_paths = [
            "../prompts/system.prompt.yaml",
            "prompts/system.prompt.yaml",
            "/etc/kubently/prompts/system.prompt.yaml"
        ]
        
        for path in prompt_paths:
            try:
                full_path = Path(path).resolve()
                if full_path.exists():
                    import yaml
                    with open(full_path, 'r') as f:
                        data = yaml.safe_load(f)
                        return data.get('content', 'System prompt not found')
            except Exception as e:
                logger.debug(f"Could not load prompt from {path}: {e}")
                continue
        
        # Fallback to default
        return "You are a multi-cluster Kubernetes debugging agent. Operate strictly read-only; never mutate cluster state."
    
    async def close(self):
        """Cleanup resources."""
        await self.debug_capture.close()


async def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Comprehensive Kubernetes Testing with Full Data Capture"
    )
    parser.add_argument("--api-url", default="http://localhost:8080", help="Kubently API URL")
    parser.add_argument("--api-key", required=True, help="API key for authentication")
    parser.add_argument("--scenario", help="Run specific scenario")
    parser.add_argument("--use-gemini", action="store_true", help="Use Gemini for AI analysis")
    parser.add_argument("--no-gemini", dest="use_gemini", action="store_false", help="Disable Gemini analysis (default)")
    parser.add_argument("--analyze-previous", help="Analyze a previous test result file instead of running new test")
    parser.add_argument("--auto-analyze", action="store_true", help="Automatically analyze results after running tests")
    
    args = parser.parse_args()
    
    # If analyzing previous results, use the separate analyzer
    if args.analyze_previous:
        if not GeminiAnalyzer:
            console.print("[red]Error: GeminiAnalyzer not available. Check if gemini_analyzer.py exists[/red]")
            sys.exit(1)
            
        analyzer = GeminiAnalyzer()
        if not analyzer.initialized:
            console.print("[red]Error: Gemini not initialized. Check GOOGLE_API_KEY[/red]")
            sys.exit(1)
        
        result_file = Path(args.analyze_previous)
        if not result_file.exists():
            # Try in comprehensive-results directory
            result_file = Path("comprehensive-results") / args.analyze_previous
            if not result_file.exists():
                console.print(f"[red]Error: File not found: {args.analyze_previous}[/red]")
                sys.exit(1)
        
        console.print(f"[blue]Analyzing previous result: {result_file}[/blue]")
        with open(result_file) as f:
            test_data = json.load(f)
        
        analysis = analyzer.analyze_test_result(test_data)
        
        # Display results
        console.print("\n[bold]Gemini Analysis Results:[/bold]")
        console.print(json.dumps(analysis, indent=2))
        
        # Save analysis in the analysis folder
        analysis_dir = Path("comprehensive-results/analysis")
        analysis_dir.mkdir(parents=True, exist_ok=True)
        
        analysis_file = analysis_dir / f"{result_file.stem}_gemini_analysis.json"
        with open(analysis_file, "w") as f:
            json.dump(analysis, f, indent=2)
        console.print(f"\n[green]Analysis saved to: {analysis_file}[/green]")
        
        return
    
    runner = ComprehensiveTestRunner(args.api_url, args.api_key, use_gemini=args.use_gemini)
    
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
        
        # Auto-analyze if requested
        if args.auto_analyze and runner.results:
            console.print("\n" + "="*60)
            console.print("[bold cyan]Starting automatic analysis of results...[/bold cyan]")
            console.print("="*60 + "\n")
            
            # Analyze each result
            for result in runner.results:
                if result.debug_trace:
                    console.print(f"\n[yellow]Analyzing {result.scenario.name}...[/yellow]")
                    
                    # Use the analyzer to provide insights if available
                    if GeminiAnalyzer:
                        analyzer = GeminiAnalyzer()
                        analysis = await analyzer.analyze_trace(result.debug_trace, result.scenario)
                    else:
                        console.print("[yellow]  GeminiAnalyzer not available, skipping analysis[/yellow]")
                        analysis = None
                    
                    # Display key findings
                    if analysis:
                        console.print(f"  Root Cause Found: {'✓' if analysis.get('root_cause_analysis', {}).get('identified_correctly') else '✗'}")
                        console.print(f"  Quality Score: {analysis.get('overall_quality', {}).get('score', 'N/A')}/10")
                        
                        # Show top recommendations
                        if analysis.get('improvements'):
                            console.print("  [cyan]Recommendations:[/cyan]")
                            for imp in analysis.get('improvements', [])[:3]:
                                console.print(f"    • {imp}")
            
    finally:
        await runner.close()


if __name__ == "__main__":
    asyncio.run(main())