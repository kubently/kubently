#!/usr/bin/env python3
"""
Gemini-powered analysis for Kubently test results
Actually calls Gemini API to analyze debugging sessions
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List

# Try to import Google Gemini
try:
    import google.generativeai as genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False
    print("Warning: google-generativeai not installed. Run: pip install google-generativeai")


def generate_markdown_report(analysis: Dict) -> str:
    """Generate a markdown report from analysis results."""
    md_lines = []
    
    # Header
    md_lines.append("# Kubently Test Analysis Report")
    md_lines.append(f"\n**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    md_lines.append(f"**Analysis Model**: {analysis.get('model', 'Unknown')}")
    md_lines.append("")
    
    # Summary for batch analysis
    if "total_scenarios" in analysis:
        md_lines.append("## Summary")
        md_lines.append("")
        md_lines.append(f"- **Total Scenarios**: {analysis.get('total_scenarios', 0)}")
        md_lines.append(f"- **Successful**: {analysis.get('successful_scenarios', 0)}")
        md_lines.append(f"- **Success Rate**: {analysis.get('success_rate', 'N/A')}")
        md_lines.append(f"- **Average Duration**: {analysis.get('average_duration', 'N/A')}")
        md_lines.append("")
        
        # Quality Assessment
        if "quality_assessment" in analysis:
            qa = analysis["quality_assessment"]
            md_lines.append("## Quality Assessment")
            md_lines.append("")
            md_lines.append(f"- **Overall Score**: {qa.get('overall_score', 'N/A')}/10")
            md_lines.append(f"- **Accuracy**: {qa.get('accuracy', 'N/A')}/10")
            md_lines.append(f"- **Completeness**: {qa.get('completeness', 'N/A')}/10")
            md_lines.append("")
        
        # Aggregate Insights
        if "aggregate_insights" in analysis:
            insights = analysis["aggregate_insights"]
            md_lines.append("## Insights")
            md_lines.append("")
            
            if "patterns" in insights:
                md_lines.append("### Patterns")
                for pattern in insights["patterns"]:
                    md_lines.append(f"- {pattern}")
                md_lines.append("")
            
            if "recommendations" in insights:
                md_lines.append("### Recommendations")
                for rec in insights["recommendations"]:
                    md_lines.append(f"- {rec}")
                md_lines.append("")
        
        # Individual Results
        if "individual_results" in analysis:
            md_lines.append("## Individual Test Results")
            md_lines.append("")
            
            for result in analysis["individual_results"]:
                success = "✅" if result.get("success", False) else "❌"
                md_lines.append(f"### {success} {result.get('scenario', 'Unknown')}")
                md_lines.append(f"**File**: {result.get('file', 'Unknown')}")
                
                if "analysis" in result:
                    result_analysis = result["analysis"]
                    if "root_cause_analysis" in result_analysis:
                        rca = result_analysis["root_cause_analysis"]
                        md_lines.append(f"- **Root Cause Found**: {'Yes' if rca.get('identified_correctly', False) else 'No'}")
                        md_lines.append(f"- **Confidence**: {rca.get('confidence', 0) * 100:.0f}%")
                    
                    if "efficiency_analysis" in result_analysis:
                        eff = result_analysis["efficiency_analysis"]
                        md_lines.append(f"- **Efficiency Score**: {eff.get('score', 'N/A')}/10")
                
                md_lines.append("")
    
    # Single result analysis
    else:
        if "root_cause_analysis" in analysis:
            md_lines.append("## Root Cause Analysis")
            rca = analysis["root_cause_analysis"]
            md_lines.append(f"- **Identified Correctly**: {'Yes' if rca.get('identified_correctly', False) else 'No'}")
            md_lines.append(f"- **Confidence**: {rca.get('confidence', 0) * 100:.0f}%")
            md_lines.append(f"- **Explanation**: {rca.get('explanation', 'N/A')}")
            md_lines.append("")
        
        if "system_prompt_enhancements" in analysis:
            md_lines.append("## System Prompt Enhancements")
            md_lines.append("")
            for enhancement in analysis["system_prompt_enhancements"]:
                md_lines.append(f"### {enhancement.get('enhancement_type', 'General').title()} Enhancement")
                md_lines.append(f"**Placement**: {enhancement.get('placement', 'N/A')}")
                md_lines.append(f"**Rationale**: {enhancement.get('rationale', 'N/A')}")
                md_lines.append("")
                md_lines.append("**Suggested Text**:")
                md_lines.append("```")
                md_lines.append(enhancement.get('specific_text', 'N/A'))
                md_lines.append("```")
                md_lines.append("")
        
        if "prompt_improvements" in analysis:
            md_lines.append("## Prompt Improvements")
            md_lines.append("")
            for improvement in analysis["prompt_improvements"]:
                priority = improvement.get('priority', 'medium')
                if priority == 'critical':
                    md_lines.append(f"#### CRITICAL: {improvement.get('current_issue', 'N/A')}")
                else:
                    md_lines.append(f"#### {improvement.get('current_issue', 'N/A')} (Priority: {priority})")
                md_lines.append(f"**Expected Benefit**: {improvement.get('expected_benefit', 'N/A')}")
                md_lines.append("")
                md_lines.append("**Suggested Improvement**:")
                md_lines.append("```")
                md_lines.append(improvement.get('suggested_improvement', 'N/A'))
                md_lines.append("```")
                md_lines.append("")
        
        if "tool_implementations" in analysis:
            md_lines.append("## Recommended Tool Implementations")
            md_lines.append("")
            for tool in analysis["tool_implementations"]:
                priority = tool.get('priority', 'medium')
                md_lines.append(f"### {tool.get('tool_name', 'Unknown')} (Priority: {priority})")
                md_lines.append(f"**Description**: {tool.get('description', 'N/A')}")
                md_lines.append("")
                md_lines.append("**Function Signature**:")
                md_lines.append("```python")
                md_lines.append(tool.get('function_signature', 'N/A'))
                md_lines.append("```")
                md_lines.append("")
                md_lines.append(f"**Implementation Notes**: {tool.get('implementation_notes', 'N/A')}")
                md_lines.append("")
        
        if "architecture_improvements" in analysis:
            md_lines.append("## Architecture Improvements")
            md_lines.append("")
            for arch in analysis["architecture_improvements"]:
                md_lines.append(f"### {arch.get('improvement_type', 'General').replace('-', ' ').title()}")
                md_lines.append(f"**Description**: {arch.get('description', 'N/A')}")
                md_lines.append(f"**Complexity**: {arch.get('complexity', 'N/A')}")
                md_lines.append("")
                md_lines.append("**Implementation Approach**:")
                md_lines.append(arch.get('implementation_approach', 'N/A'))
                md_lines.append("")
                md_lines.append(f"**Expected Benefits**: {arch.get('expected_benefits', 'N/A')}")
                md_lines.append("")
        
        if "recommendations" in analysis:
            md_lines.append("## Recommendations")
            for rec in analysis["recommendations"]:
                md_lines.append(f"- {rec}")
            md_lines.append("")
    
    return '\n'.join(md_lines)


class GeminiAnalyzer:
    """Analyzes Kubently test results using Google Gemini."""
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize Gemini analyzer."""
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY")
        self.initialized = False
        
        if not self.api_key:
            print("Warning: GOOGLE_API_KEY not set. Gemini analysis unavailable.")
            return
            
        if not HAS_GEMINI:
            print("Error: google-generativeai package not installed")
            return
        
        try:
            genai.configure(api_key=self.api_key)
            # Use Gemini 2.5 Pro for deeper analysis with better reasoning
            self.model = genai.GenerativeModel('gemini-2.5-pro')
            # Use Flash for faster RCA-only analysis
            self.rca_model = genai.GenerativeModel('gemini-1.5-flash')
            self.initialized = True
            print("✓ Gemini analyzer initialized")
        except Exception as e:
            print(f"Error initializing Gemini: {e}")
    
    def analyze_rca_only(self, test_result: Dict) -> Dict[str, Any]:
        """
        Lightweight RCA-only analysis - just determines if root cause was found.
        Much faster than full analysis.

        Args:
            test_result: The test result JSON data

        Returns:
            Minimal analysis with just RCA determination
        """
        if not self.initialized:
            raise RuntimeError("Gemini analyzer not initialized. Please set GOOGLE_API_KEY environment variable.")

        # Extract key information
        scenario = test_result.get("scenario", {})
        debug_trace = test_result.get("debug_trace", {})

        # Build focused prompt for RCA determination
        prompt = f"""You are analyzing whether a Kubernetes debugging agent correctly identified the root cause of an issue.

SCENARIO: {scenario.get('name', 'Unknown')}
EXPECTED ROOT CAUSE: {scenario.get('expected_fix', 'Not specified')}

AGENT'S RESPONSE:
{json.dumps(debug_trace.get('responses', []), indent=2)}

TOOL CALLS MADE (summary):
{', '.join(set(tc.get('tool', 'unknown') for tc in debug_trace.get('tool_calls', [])))}

Based on the agent's response, did it correctly identify the root cause of the issue?
Consider:
1. Does the response identify the actual problem (not just symptoms)?
2. Does it match or relate to the expected root cause?
3. Is the diagnosis accurate and actionable?

Respond with ONLY a JSON object in this exact format:
{{
    "root_cause_analysis": {{
        "identified_correctly": true/false,
        "confidence": 0.0-1.0,
        "explanation": "Brief explanation (1-2 sentences)"
    }}
}}
"""

        try:
            # Use the faster Flash model for RCA-only
            response = self.rca_model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.1,  # Low temperature for consistent evaluation
                    "top_p": 0.95,
                    "max_output_tokens": 500,  # Small response needed
                    "response_mime_type": "application/json"
                }
            )

            response_text = response.text.strip()

            # Parse JSON response
            try:
                analysis = json.loads(response_text)
                analysis["model"] = "gemini-1.5-flash"
                analysis["analysis_type"] = "rca_only"
                analysis["timestamp"] = datetime.now().isoformat()
                return analysis

            except json.JSONDecodeError:
                # Try to extract JSON from the response
                import re
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    analysis = json.loads(json_match.group())
                    analysis["model"] = "gemini-1.5-flash"
                    analysis["analysis_type"] = "rca_only"
                    analysis["timestamp"] = datetime.now().isoformat()
                    return analysis
                else:
                    # Return error structure
                    return {
                        "model": "gemini-1.5-flash",
                        "analysis_type": "rca_only",
                        "timestamp": datetime.now().isoformat(),
                        "error": "Failed to parse response",
                        "raw_response": response_text,
                        "root_cause_analysis": {
                            "identified_correctly": False,
                            "confidence": 0.0,
                            "explanation": f"Analysis failed: Could not parse response"
                        }
                    }

        except Exception as e:
            print(f"Error in RCA analysis: {e}")
            return {
                "model": "gemini-1.5-flash",
                "analysis_type": "rca_only",
                "timestamp": datetime.now().isoformat(),
                "error": str(e),
                "root_cause_analysis": {
                    "identified_correctly": False,
                    "confidence": 0.0,
                    "explanation": f"Analysis error: {str(e)}"
                }
            }

    def analyze_test_result(self, test_result: Dict) -> Dict[str, Any]:
        """
        Analyze a single test result using Gemini.
        
        Args:
            test_result: The test result JSON data
            
        Returns:
            Analysis results from Gemini
        """
        if not self.initialized:
            raise RuntimeError("Gemini analyzer not initialized. Please set GOOGLE_API_KEY environment variable.")
        
        # Extract key information
        scenario = test_result.get("scenario", {})
        debug_trace = test_result.get("debug_trace", {})
        
        # Load the current system prompt
        system_prompt_path = Path(__file__).parent.parent / "prompts" / "system.prompt.yaml"
        current_system_prompt = ""
        if system_prompt_path.exists():
            import yaml
            with open(system_prompt_path) as f:
                prompt_data = yaml.safe_load(f)
                current_system_prompt = prompt_data.get("content", "")
        
        # Prepare prompt for Gemini
        prompt = f"""
        Analyze this Kubernetes debugging test session with CRITICAL FOCUS on improving the system prompt to make the agent more effective.
        
        CURRENT SYSTEM PROMPT:
        ```yaml
        {current_system_prompt}
        ```
        
        SCENARIO INFORMATION:
        - Name: {scenario.get('name', 'Unknown')}
        - Expected Fix: {scenario.get('expected_fix', 'Not specified')}
        - Query Sent: {debug_trace.get('query', 'No query')}
        
        KUBENTLY'S RESPONSE:
        {json.dumps(debug_trace.get('responses', []), indent=2)}
        
        PERFORMANCE METRICS:
        - Duration: {debug_trace.get('duration_seconds', 0):.2f} seconds
        - Tokens Used: {debug_trace.get('token_usage', {}).get('total', 0)}
        
        TOOL CALLS EXECUTED:
        {json.dumps(debug_trace.get('tool_calls', []), indent=2)}
        
        Please analyze and provide a JSON response with the following structure:
        {{
            "root_cause_analysis": {{
                "identified_correctly": true/false,
                "confidence": 0.0-1.0,
                "explanation": "Detailed explanation of whether the agent found the root cause",
                "missing_insights": ["List of insights the agent missed"]
            }},
            "efficiency_analysis": {{
                "score": 1-10,
                "response_time_assessment": "fast/acceptable/slow",
                "tool_usage_analysis": "Analyze the efficiency and appropriateness of the tool calls made",
                "optimization_opportunities": ["List of ways to optimize"]
            }},
            "quality_assessment": {{
                "overall_score": 1-10,
                "accuracy": 1-10,
                "completeness": 1-10,
                "clarity": 1-10,
                "actionability": 1-10,
                "explanation": "Why these scores were given"
            }},
            "bottlenecks": [
                {{
                    "type": "performance/logic/tool_usage",
                    "description": "Description of bottleneck",
                    "impact": "high/medium/low",
                    "solution": "How to fix it"
                }}
            ],
            "prompt_improvements": [
                {{
                    "current_issue": "What specific behavior or pattern in the agent's response indicates a system prompt deficiency",
                    "suggested_improvement": "EXACT system prompt text to add/modify (be very specific - provide the actual prompt text)",
                    "expected_benefit": "Precisely how this will improve the agent's debugging capabilities",
                    "priority": "critical/high/medium/low"
                }}
            ],
            "system_prompt_enhancements": [
                {{
                    "enhancement_type": "instruction/behavior/knowledge/reasoning",
                    "specific_text": "The EXACT text to add to the system prompt",
                    "placement": "Where in the system prompt this should go (beginning/tools section/reasoning section/end)",
                    "rationale": "Why this enhancement is needed based on the observed behavior"
                }}
            ],
            "missing_capabilities": [
                {{
                    "capability": "What's missing",
                    "use_case": "When it would help",
                    "priority": "high/medium/low"
                }}
            ],
            "tool_implementations": [
                {{
                    "tool_name": "Name of the new tool",
                    "function_signature": "def tool_name(param1: type, param2: type) -> ReturnType",
                    "description": "What this tool does and when to use it",
                    "implementation_notes": "Key implementation considerations",
                    "priority": "critical/high/medium/low"
                }}
            ],
            "architecture_improvements": [
                {{
                    "improvement_type": "multi-agent/workflow/preprocessing/context-management",
                    "description": "Detailed description of the architectural improvement",
                    "implementation_approach": "How to implement this improvement",
                    "expected_benefits": "What problems this solves",
                    "complexity": "low/medium/high"
                }}
            ],
            "recommendations": [
                "Specific, actionable recommendations for improving the agent, with PRIMARY FOCUS on system prompt enhancements"
            ]
        }}
        
        CRITICAL: Focus your analysis on THREE KEY AREAS:
        
        1. SYSTEM PROMPT IMPROVEMENTS - Based on the current system prompt above, identify:
           - Missing instructions that would have helped the agent debug this specific scenario
           - Behavioral patterns that need to be added or modified
           - Tool usage guidance that's missing from the current prompt
           - Decision tree improvements for better problem-solving
           
        2. ADDITIONAL TOOL IMPLEMENTATIONS - Identify new tools that would help:
           - What specific kubectl commands or operations are missing?
           - What analysis capabilities would improve debugging?
           - What information gathering tools would help?
           - Provide specific tool signatures and descriptions
           
        3. AGENT/NODE ARCHITECTURE - Consider architectural improvements:
           - Would a multi-agent approach help (e.g., specialist agents for different issues)?
           - Would a node-based workflow improve decision making?
           - What preprocessing or analysis steps could be automated?
           - How could the agent better maintain context across debugging steps?
        
        Be EXTREMELY specific with ALL suggestions:
        - For prompt changes: provide the exact text and where to insert it
        - For new tools: provide the function signature and description
        - For architecture: provide concrete implementation suggestions

        IMPORTANT: Return valid JSON with properly escaped strings. Use \\n for newlines within string values.
        """
        
        try:
            # Call Gemini
            response = self.model.generate_content(prompt)
            
            # Extract JSON from response
            response_text = response.text
            
            # Try to parse JSON from the response
            # Gemini might wrap it in markdown code blocks
            json_text = response_text

            if "```json" in response_text:
                # Extract JSON from markdown code block
                json_start = response_text.find("```json") + 7
                # Skip any whitespace/newlines after ```json
                while json_start < len(response_text) and response_text[json_start] in ['\n', '\r', ' ']:
                    json_start += 1
                json_end = response_text.find("```", json_start)
                if json_end > json_start:
                    json_text = response_text[json_start:json_end].strip()
            elif "```" in response_text and "{" in response_text:
                # Handle code blocks without json specifier
                json_start = response_text.find("```") + 3
                while json_start < len(response_text) and response_text[json_start] in ['\n', '\r', ' ']:
                    json_start += 1
                json_end = response_text.find("```", json_start)
                if json_end > json_start:
                    json_text = response_text[json_start:json_end].strip()
            elif "{" in response_text:
                # Find the JSON object without code blocks
                json_start = response_text.find("{")
                json_end = response_text.rfind("}") + 1
                if json_end > json_start:
                    json_text = response_text[json_start:json_end].strip()

            analysis = json.loads(json_text)
            analysis["model"] = "gemini-2.5-pro"
            analysis["timestamp"] = datetime.now().isoformat()
            
            # Ensure required fields are present with defaults if missing
            if "root_cause_analysis" not in analysis:
                # Try to determine from the response
                response_text_lower = ' '.join(debug_trace.get('responses', [])).lower()
                expected_fix_lower = scenario.get('expected_fix', '').lower()
                
                # Simple heuristic: if the expected fix keywords are in the response
                identified_correctly = False
                confidence = 0.0
                
                if expected_fix_lower and response_text_lower:
                    # Check for key phrases from expected fix in the response
                    fix_keywords = [kw.strip() for kw in expected_fix_lower.split() if len(kw.strip()) > 3]
                    matching_keywords = sum(1 for kw in fix_keywords if kw in response_text_lower)
                    if fix_keywords:
                        confidence = matching_keywords / len(fix_keywords)
                        identified_correctly = confidence > 0.7
                
                analysis["root_cause_analysis"] = {
                    "identified_correctly": identified_correctly,
                    "confidence": confidence,
                    "explanation": "Gemini analysis did not provide root cause assessment. Using heuristic based on response content.",
                    "missing_insights": []
                }
            
            # Ensure other expected fields have defaults
            if "efficiency_analysis" not in analysis:
                analysis["efficiency_analysis"] = {
                    "score": 5,
                    "response_time_assessment": "acceptable",
                    "tool_usage_analysis": "Not analyzed",
                    "optimization_opportunities": []
                }
            
            if "quality_assessment" not in analysis:
                analysis["quality_assessment"] = {
                    "overall_score": 5,
                    "accuracy": 5,
                    "completeness": 5,
                    "clarity": 5,
                    "actionability": 5,
                    "explanation": "Default assessment"
                }
            
            if "recommendations" not in analysis:
                analysis["recommendations"] = []
            
            return analysis
            
        except json.JSONDecodeError as e:
            # Don't log the first error - silently try the regex approach first
            # Try with a more aggressive regex approach for JSON with unescaped newlines
            try:
                import re
                # This regex finds the outermost JSON object
                json_pattern = r'\{(?:[^{}]|(?:\{[^{}]*\})|(?:\[[^\[\]]*\]))*\}'
                matches = re.findall(json_pattern, response_text, re.DOTALL)

                if matches:
                    # Try the largest match first (likely the complete JSON)
                    for match in sorted(matches, key=len, reverse=True):
                        try:
                            analysis = json.loads(match)
                            analysis["model"] = "gemini-2.5-pro"
                            analysis["timestamp"] = datetime.now().isoformat()
                            
                            # Ensure required fields are present in fallback case too
                            if "root_cause_analysis" not in analysis:
                                response_text_lower = ' '.join(debug_trace.get('responses', [])).lower()
                                expected_fix_lower = scenario.get('expected_fix', '').lower()
                                
                                identified_correctly = False
                                confidence = 0.0
                                
                                if expected_fix_lower and response_text_lower:
                                    fix_keywords = [kw.strip() for kw in expected_fix_lower.split() if len(kw.strip()) > 3]
                                    matching_keywords = sum(1 for kw in fix_keywords if kw in response_text_lower)
                                    if fix_keywords:
                                        confidence = matching_keywords / len(fix_keywords)
                                        identified_correctly = confidence > 0.7
                                
                                analysis["root_cause_analysis"] = {
                                    "identified_correctly": identified_correctly,
                                    "confidence": confidence,
                                    "explanation": "Gemini analysis did not provide root cause assessment. Using heuristic based on response content.",
                                    "missing_insights": []
                                }
                            
                            if "efficiency_analysis" not in analysis:
                                analysis["efficiency_analysis"] = {
                                    "score": 5,
                                    "response_time_assessment": "acceptable",
                                    "tool_usage_analysis": "Not analyzed",
                                    "optimization_opportunities": []
                                }
                            
                            if "quality_assessment" not in analysis:
                                analysis["quality_assessment"] = {
                                    "overall_score": 5,
                                    "accuracy": 5,
                                    "completeness": 5,
                                    "clarity": 5,
                                    "actionability": 5,
                                    "explanation": "Default assessment"
                                }
                            
                            if "recommendations" not in analysis:
                                analysis["recommendations"] = []
                            
                            # Silently succeed without logging
                            return analysis
                        except json.JSONDecodeError:
                            continue
            except Exception as regex_error:
                pass  # Continue to the error reporting below

            # Only show error if both approaches failed
            print(f"Warning: Had to use fallback JSON extraction due to formatting issues")
            if 'json_text' in locals() and len(json_text) > 0:
                print(f"  JSON appears to contain unescaped newlines in strings")
            else:
                print(f"  Could not extract valid JSON from response")

            # Return the raw response if JSON parsing fails with required fields
            return {
                "model": "gemini-2.5-pro",
                "timestamp": datetime.now().isoformat(),
                "raw_response": response_text,
                "error": f"Failed to parse structured response: {str(e)}",
                "root_cause_analysis": {
                    "identified_correctly": False,
                    "confidence": 0.0,
                    "explanation": f"JSON parsing failed: {str(e)}",
                    "missing_insights": []
                },
                "efficiency_analysis": {
                    "score": 0,
                    "response_time_assessment": "unknown",
                    "tool_usage_analysis": "Analysis failed",
                    "optimization_opportunities": []
                },
                "quality_assessment": {
                    "overall_score": 0,
                    "accuracy": 0,
                    "completeness": 0,
                    "clarity": 0,
                    "actionability": 0,
                    "explanation": "Analysis failed"
                },
                "recommendations": []
            }
        except Exception as e:
            print(f"Error calling Gemini: {e}")
            return {
                "error": str(e),
                "model": "gemini-2.5-pro",
                "timestamp": datetime.now().isoformat(),
                "root_cause_analysis": {
                    "identified_correctly": False,
                    "confidence": 0.0,
                    "explanation": f"Analysis error: {str(e)}",
                    "missing_insights": []
                },
                "efficiency_analysis": {
                    "score": 0,
                    "response_time_assessment": "unknown",
                    "tool_usage_analysis": "Analysis failed",
                    "optimization_opportunities": []
                },
                "quality_assessment": {
                    "overall_score": 0,
                    "accuracy": 0,
                    "completeness": 0,
                    "clarity": 0,
                    "actionability": 0,
                    "explanation": "Analysis failed"
                },
                "recommendations": []
            }
    
    async def analyze_trace(self, debug_trace: Any, scenario: Any, rca_only: bool = True) -> Dict:
        """
        Analyze a debug trace and scenario using Gemini.
        This is an async wrapper around analyze_test_result for compatibility.

        Args:
            debug_trace: The debug trace data (dict or dataclass)
            scenario: The test scenario information (dict or dataclass)
            rca_only: If True, only analyze RCA (faster). If False, do full analysis.

        Returns:
            Analysis results from Gemini
        """
        # Convert dataclasses to dictionaries if needed
        from dataclasses import asdict, is_dataclass
        
        if is_dataclass(debug_trace):
            debug_trace = asdict(debug_trace)
        
        if is_dataclass(scenario):
            # Convert scenario dataclass to dict, handling Path objects
            scenario_dict = {}
            for field_name in scenario.__dataclass_fields__:
                value = getattr(scenario, field_name)
                if hasattr(value, '__fspath__'):  # Check if it's a Path object
                    scenario_dict[field_name] = str(value)
                else:
                    scenario_dict[field_name] = value
            scenario = scenario_dict
        
        # Combine trace and scenario into test_result format
        test_result = {
            "debug_trace": debug_trace,
            "scenario": scenario
        }

        # Call the appropriate analysis method based on rca_only flag
        if rca_only:
            return self.analyze_rca_only(test_result)
        else:
            return self.analyze_test_result(test_result)
    
    
    def analyze_multiple_results(self, result_files: List[Path]) -> Dict:
        """
        Analyze multiple test results and provide aggregate insights.
        """
        if not self.initialized:
            raise RuntimeError("Gemini analyzer not initialized. Please set GOOGLE_API_KEY environment variable.")
        
        all_results = []
        failed_analyses = []
        
        print(f"Analyzing {len(result_files)} test results...")
        
        for i, file_path in enumerate(result_files):
            print(f"  Processing {i+1}/{len(result_files)}: {file_path.name}", end='', flush=True)
            
            try:
                with open(file_path) as f:
                    test_data = json.load(f)
                
                # Add a small delay to avoid rate limiting
                if i > 0:
                    time.sleep(0.5)
                
                analysis = self.analyze_test_result(test_data)
                
                if "error" in analysis:
                    print(f" [FAILED: {analysis['error']}]")
                    failed_analyses.append({
                        "file": file_path.name,
                        "error": analysis["error"]
                    })
                else:
                    print(" [OK]")
                    all_results.append({
                        "file": file_path.name,
                        "scenario": test_data.get("scenario", {}).get("name"),
                        "success": test_data.get("debug_session", {}).get("success", False),
                        "analysis": analysis
                    })
            except Exception as e:
                print(f" [ERROR: {str(e)}]")
                failed_analyses.append({
                    "file": file_path.name,
                    "error": str(e)
                })
        
        # Calculate basic metrics without making another API call
        total_scenarios = len(result_files)
        successful_analyses = len(all_results)
        
        # Extract quality scores from individual analyses
        quality_scores = []
        for result in all_results:
            if "quality_assessment" in result["analysis"]:
                score = result["analysis"]["quality_assessment"].get("overall_score", 0)
                quality_scores.append(score)
        
        avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 0
        
        # Build final result
        final_result = {
            "model": "gemini-2.0-flash",
            "timestamp": datetime.now().isoformat(),
            "total_scenarios": total_scenarios,
            "successful_scenarios": successful_analyses,
            "failed_scenarios": len(failed_analyses),
            "success_rate": f"{(successful_analyses/total_scenarios*100):.1f}%" if total_scenarios > 0 else "0%",
            "average_duration": "N/A",  # Would need to calculate from individual results
            "quality_assessment": {
                "overall_score": round(avg_quality, 1),
                "accuracy": round((successful_analyses/total_scenarios) * 10, 1) if total_scenarios > 0 else 0,
                "completeness": round((successful_analyses/total_scenarios) * 10, 1) if total_scenarios > 0 else 0
            },
            "individual_results": all_results
        }
        
        if failed_analyses:
            final_result["failed_analyses"] = failed_analyses
        
        # If we have results, try to get aggregate insights (but handle failure gracefully)
        if all_results and len(all_results) <= 10:  # Limit to avoid token limits
            try:
                # Create a summary for aggregate analysis
                summary_data = [{
                    "scenario": r["scenario"],
                    "success": r["success"],
                    "quality_score": r["analysis"].get("quality_assessment", {}).get("overall_score", 0)
                } for r in all_results]
                
                aggregate_prompt = f"""
                Based on these {len(all_results)} test results summary:
                {json.dumps(summary_data, indent=2)}
                
                Provide brief JSON insights:
                {{
                    "patterns": ["2-3 key patterns observed"],
                    "recommendations": ["2-3 actionable recommendations"]
                }}
                """
                
                response = self.model.generate_content(aggregate_prompt)
                insights = json.loads(response.text)
                final_result["aggregate_insights"] = insights
            except Exception as e:
                print(f"Note: Could not generate aggregate insights: {e}")
        
        return final_result


def aggregate_existing_results(result_files: List[Path]) -> Dict:
    """Aggregate existing analysis results from test files."""
    
    all_results = []
    failed_to_load = []
    
    for file_path in result_files:
        try:
            with open(file_path) as f:
                test_data = json.load(f)
                
            scenario = test_data.get("scenario", {})
            analysis = test_data.get("analysis", {})
            debug_trace = test_data.get("debug_trace", {})
            
            # Get success indicators
            root_cause = analysis.get("root_cause_analysis", {})
            identified_correctly = root_cause.get("identified_correctly", False)
            overall_success = test_data.get("overall_success", False)
            
            # Get quality scores
            quality = analysis.get("quality_assessment", {})
            
            all_results.append({
                "file": file_path.name,
                "scenario": scenario.get("name", "Unknown"),
                "identified_correctly": identified_correctly,
                "overall_success": overall_success,
                "duration": debug_trace.get("duration_seconds", 0),
                "expected_fix": scenario.get("expected_fix", "Unknown"),
                "quality_score": quality.get("overall_score", 0),
                "accuracy": quality.get("accuracy", 0),
                "completeness": quality.get("completeness", 0),
                "clarity": quality.get("clarity", 0),
                "actionability": quality.get("actionability", 0),
                "confidence": root_cause.get("confidence", 0),
                "explanation": root_cause.get("explanation", ""),
                "recommendations": analysis.get("recommendations", [])
            })
        except Exception as e:
            failed_to_load.append({
                "file": file_path.name,
                "error": str(e)
            })
    
    # Calculate summary statistics
    total = len(all_results)
    successful = sum(1 for r in all_results if r["identified_correctly"])
    avg_duration = sum(r["duration"] for r in all_results) / total if total > 0 else 0
    avg_quality = sum(r["quality_score"] for r in all_results) / total if total > 0 else 0
    avg_accuracy = sum(r["accuracy"] for r in all_results) / total if total > 0 else 0
    avg_completeness = sum(r["completeness"] for r in all_results) / total if total > 0 else 0
    
    # Identify patterns
    common_recommendations = {}
    for result in all_results:
        for rec in result["recommendations"]:
            # Extract the main recommendation (before the colon)
            main_rec = rec.split(":")[0].strip("*").strip()
            common_recommendations[main_rec] = common_recommendations.get(main_rec, 0) + 1
    
    # Sort recommendations by frequency
    top_recommendations = sorted(common_recommendations.items(), key=lambda x: x[1], reverse=True)[:5]
    
    return {
        "model": "gemini-2.5-pro (aggregated)",
        "timestamp": datetime.now().isoformat(),
        "total_scenarios": total,
        "successful_scenarios": successful,
        "failed_scenarios": total - successful,
        "success_rate": f"{(successful/total*100):.1f}%" if total > 0 else "0%",
        "average_duration": f"{avg_duration:.2f}s",
        "quality_assessment": {
            "overall_score": round(avg_quality, 1),
            "accuracy": round(avg_accuracy, 1),
            "completeness": round(avg_completeness, 1)
        },
        "aggregate_insights": {
            "patterns": [
                f"{successful}/{total} scenarios correctly identified root cause",
                f"Average confidence level: {sum(r['confidence'] for r in all_results)/total*100:.0f}%",
                f"Average quality score: {avg_quality:.1f}/10"
            ],
            "recommendations": [rec[0] for rec in top_recommendations]
        },
        "individual_results": all_results,
        "failed_to_load": failed_to_load
    }


def main():
    """Main entry point for analysis."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Analyze Kubently test results with Gemini")
    parser.add_argument(
        "--result-dir",
        help="Directory containing test results to analyze"
    )
    parser.add_argument(
        "--result-file",
        help="Path to specific test result JSON file to analyze"
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Analyze the most recent test result"
    )
    parser.add_argument(
        "--scenario",
        help="Analyze latest result for specific scenario"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Analyze all test results in comprehensive-results/"
    )
    parser.add_argument(
        "--output",
        help="Output file for analysis results"
    )
    parser.add_argument(
        "--api-key",
        help="Google API key (or set GOOGLE_API_KEY env var)"
    )
    
    args = parser.parse_args()
    
    # Initialize analyzer
    analyzer = GeminiAnalyzer(api_key=args.api_key)
    
    if not analyzer.initialized:
        print("Error: Gemini analyzer not initialized. Please set GOOGLE_API_KEY environment variable.")
        sys.exit(1)
    
    # Determine what to analyze
    if args.result_dir:
        result_dir = Path(args.result_dir)
    else:
        result_dir = Path("comprehensive-results")
    
    if args.result_file:
        # Analyze specific file
        result_file = Path(args.result_file)
        if not result_file.exists():
            print(f"Error: File not found: {result_file}")
            sys.exit(1)
        
        print(f"Analyzing: {result_file}")
        with open(result_file) as f:
            test_data = json.load(f)
        
        analysis = analyzer.analyze_test_result(test_data)
        
    elif args.latest:
        # Analyze most recent result
        result_files = sorted(result_dir.glob("*.json"), key=lambda x: x.stat().st_mtime)
        if not result_files:
            print("No test results found")
            sys.exit(1)
        
        latest_file = result_files[-1]
        print(f"Analyzing latest: {latest_file}")
        
        with open(latest_file) as f:
            test_data = json.load(f)
        
        analysis = analyzer.analyze_test_result(test_data)
        
    elif args.scenario:
        # Analyze latest result for specific scenario
        pattern = f"{args.scenario}*.json"
        result_files = sorted(result_dir.glob(pattern), key=lambda x: x.stat().st_mtime)
        
        if not result_files:
            print(f"No results found for scenario: {args.scenario}")
            sys.exit(1)
        
        latest_file = result_files[-1]
        print(f"Analyzing latest for {args.scenario}: {latest_file}")
        
        with open(latest_file) as f:
            test_data = json.load(f)
        
        analysis = analyzer.analyze_test_result(test_data)
        
    elif args.all:
        # Analyze all results
        result_files = list(result_dir.glob("*.json"))
        if not result_files:
            print("No test results found")
            sys.exit(1)
        
        print(f"Analyzing {len(result_files)} test results...")
        analysis = analyzer.analyze_multiple_results(result_files)
        
    elif args.result_dir:
        # Analyze all results in the specified directory
        result_files = []
        for json_file in result_dir.glob("*.json"):
            # Skip summary and trace files
            if "summary" not in str(json_file) and "/traces/" not in str(json_file) and "analysis" not in str(json_file):
                result_files.append(json_file)
        
        if not result_files:
            print(f"No test results found in {result_dir}")
            sys.exit(1)
        
        print(f"Aggregating {len(result_files)} test results from {result_dir}...")
        # Use report generator instead of re-analyzing with Gemini
        analysis = aggregate_existing_results(result_files)
        
    else:
        print("Please specify what to analyze: --result-dir, --latest, --scenario, --result-file, or --all")
        sys.exit(1)
    
    # Output results
    if args.output:
        output_file = Path(args.output)
        
        # Check if markdown output is requested
        if output_file.suffix == '.md':
            # Generate markdown report
            markdown_content = generate_markdown_report(analysis)
            with open(output_file, "w") as f:
                f.write(markdown_content)
            
            # Also save JSON report
            json_file = output_file.with_suffix('.json')
            with open(json_file, "w") as f:
                json.dump(analysis, f, indent=2)
            
            print(f"Analysis saved to: {output_file}")
            print(f"JSON report saved to: {json_file}")
        else:
            # Save as JSON
            with open(output_file, "w") as f:
                json.dump(analysis, f, indent=2)
            print(f"Analysis saved to: {output_file}")
    else:
        # Pretty print to console
        print("\n" + "="*60)
        print("GEMINI ANALYSIS RESULTS")
        print("="*60)
        print(json.dumps(analysis, indent=2))
    
    # Print summary if available
    if "quality_assessment" in analysis:
        qa = analysis["quality_assessment"]
        print("\n" + "="*60)
        print(f"Overall Quality Score: {qa.get('overall_score', 'N/A')}/10")
        print(f"Accuracy: {qa.get('accuracy', 'N/A')}/10")
        print(f"Completeness: {qa.get('completeness', 'N/A')}/10")
        print("="*60)


if __name__ == "__main__":
    main()