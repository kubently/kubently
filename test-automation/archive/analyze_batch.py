#!/usr/bin/env python3
"""
Batch analysis of test results from September 9, 2025
"""

import json
import asyncio
from pathlib import Path
from typing import Dict, List, Any
from gemini_analyzer import GeminiAnalyzer
from dataclasses import dataclass
import sys

@dataclass
class AggregatedFindings:
    """Aggregated analysis findings."""
    total_tests: int = 0
    successful_root_cause: int = 0
    common_issues: Dict[str, int] = None
    prompt_gaps: Dict[str, int] = None
    performance_issues: List[str] = None
    recommendations: List[str] = None

async def analyze_batch():
    """Analyze all test results from Sep 9."""
    
    # Get all Sep 9 test files
    test_files = sorted(Path("comprehensive-results").glob("*.09-09-2025.json"))
    print(f"Found {len(test_files)} test results to analyze\n")
    
    # Initialize analyzer
    analyzer = GeminiAnalyzer()
    
    # Store all analyses
    all_analyses = []
    findings = AggregatedFindings(
        common_issues={},
        prompt_gaps={},
        performance_issues=[],
        recommendations=[]
    )
    findings.total_tests = len(test_files)
    
    # Analyze each test
    for test_file in test_files:
        print(f"Analyzing {test_file.name}...")
        
        try:
            with open(test_file) as f:
                test_data = json.load(f)
            
            # Get scenario and trace
            scenario = test_data.get("scenario", {})
            debug_trace = test_data.get("debug_trace", {})
            
            # Analyze
            if analyzer.initialized:
                analysis = await analyzer.analyze_trace(debug_trace, scenario)
            else:
                # Use heuristic analysis if Gemini not available
                analysis = analyzer._heuristic_analysis(test_data)
            
            all_analyses.append({
                "file": test_file.name,
                "scenario": scenario.get("name"),
                "analysis": analysis
            })
            
            # Track successful root cause identification
            if analysis.get("root_cause_analysis", {}).get("identified_correctly"):
                findings.successful_root_cause += 1
            
            # Collect common issues
            responses = " ".join(debug_trace.get("responses", [])).lower()
            
            # Check for common problematic patterns
            if "could you" in responses or "please provide" in responses:
                findings.common_issues["asks_for_clarification"] = \
                    findings.common_issues.get("asks_for_clarification", 0) + 1
                    
            if "which namespace" in responses or "what namespace" in responses:
                findings.common_issues["namespace_confusion"] = \
                    findings.common_issues.get("namespace_confusion", 0) + 1
                    
            if len(debug_trace.get("tool_calls", [])) == 0:
                findings.common_issues["no_tool_usage"] = \
                    findings.common_issues.get("no_tool_usage", 0) + 1
                    
            if "read-only" in responses or "can't fix" in responses:
                findings.common_issues["mentions_readonly_limits"] = \
                    findings.common_issues.get("mentions_readonly_limits", 0) + 1
                    
            if debug_trace.get("duration_seconds", 0) > 10:
                findings.performance_issues.append(f"{scenario.get('name')}: {debug_trace.get('duration_seconds', 0):.1f}s")
            
            # Check if namespace was used correctly
            expected_ns = scenario.get("namespace", "")
            tool_calls_str = str(debug_trace.get("tool_calls", []))
            if expected_ns and expected_ns.startswith("test-scenario") and expected_ns not in tool_calls_str:
                findings.prompt_gaps["namespace_not_used"] = \
                    findings.prompt_gaps.get("namespace_not_used", 0) + 1
                    
        except Exception as e:
            print(f"  Error analyzing {test_file.name}: {e}")
    
    return findings, all_analyses

def generate_prompt_recommendations(findings: AggregatedFindings) -> Dict[str, List[str]]:
    """Generate specific prompt recommendations based on findings."""
    
    recommendations = {
        "diagnostician_prompt": [],
        "judge_prompt": [],
        "system_wide": []
    }
    
    # Calculate percentages
    clarification_rate = findings.common_issues.get("asks_for_clarification", 0) / findings.total_tests * 100
    namespace_confusion_rate = findings.common_issues.get("namespace_confusion", 0) / findings.total_tests * 100
    no_tools_rate = findings.common_issues.get("no_tool_usage", 0) / findings.total_tests * 100
    readonly_mentions_rate = findings.common_issues.get("mentions_readonly_limits", 0) / findings.total_tests * 100
    namespace_usage_rate = findings.prompt_gaps.get("namespace_not_used", 0) / findings.total_tests * 100
    
    # Diagnostician-specific recommendations
    if clarification_rate > 20:
        recommendations["diagnostician_prompt"].append(
            "Add: 'When debugging Kubernetes issues, immediately investigate using kubectl commands. "
            "Do not ask for clarification about namespaces or resource names if they are provided in the query.'"
        )
    
    if namespace_confusion_rate > 10:
        recommendations["diagnostician_prompt"].append(
            "Add: 'When a namespace is mentioned in the query (e.g., test-scenario-XX), "
            "use it directly in all kubectl commands without asking for confirmation.'"
        )
        
    if no_tools_rate > 15:
        recommendations["diagnostician_prompt"].append(
            "Add: 'Always use kubectl commands to gather diagnostic information. "
            "Do not provide theoretical analysis without first examining the actual cluster state.'"
        )
    
    if namespace_usage_rate > 20:
        recommendations["diagnostician_prompt"].append(
            "Add: 'Extract and use any namespace mentioned in the query. "
            "Format: kubectl [command] -n [namespace] for all commands when a namespace is specified.'"
        )
    
    # Judge-specific recommendations
    if readonly_mentions_rate > 10:
        recommendations["judge_prompt"].append(
            "Add: 'Focus on identifying root causes and providing actionable recommendations. "
            "Do not mention read-only limitations unless specifically asked about fixing issues.'"
        )
    
    recommendations["judge_prompt"].append(
        "Add: 'Synthesize findings from the Diagnostician into a clear, concise root cause analysis. "
        "Structure your response as: 1) Root Cause, 2) Evidence, 3) Recommended Fix (if applicable).'"
    )
    
    # System-wide recommendations
    if findings.successful_root_cause < findings.total_tests * 0.7:
        recommendations["system_wide"].append(
            "Consider adding more explicit patterns for common Kubernetes issues to help "
            "both nodes recognize and diagnose problems more effectively."
        )
    
    if len(findings.performance_issues) > findings.total_tests * 0.2:
        recommendations["system_wide"].append(
            "Optimize the diagnostic flow to reduce latency. Consider batching kubectl commands "
            "or implementing early-exit conditions for obvious issues."
        )
    
    return recommendations

async def main():
    """Main entry point."""
    print("="*60)
    print("BATCH ANALYSIS OF TEST RESULTS")
    print("September 9, 2025 - After 16:25:07")
    print("="*60 + "\n")
    
    # Run analysis
    findings, all_analyses = await analyze_batch()
    
    # Generate recommendations
    recommendations = generate_prompt_recommendations(findings)
    
    # Display summary
    print("\n" + "="*60)
    print("ANALYSIS SUMMARY")
    print("="*60)
    
    print(f"\nTotal Tests Analyzed: {findings.total_tests}")
    print(f"Successful Root Cause Identification: {findings.successful_root_cause}/{findings.total_tests} ({findings.successful_root_cause/findings.total_tests*100:.1f}%)")
    
    print("\n📊 COMMON ISSUES DETECTED:")
    for issue, count in sorted(findings.common_issues.items(), key=lambda x: x[1], reverse=True):
        percentage = count / findings.total_tests * 100
        print(f"  • {issue.replace('_', ' ').title()}: {count} occurrences ({percentage:.1f}%)")
    
    print("\n⚠️ PROMPT GAPS IDENTIFIED:")
    for gap, count in sorted(findings.prompt_gaps.items(), key=lambda x: x[1], reverse=True):
        percentage = count / findings.total_tests * 100
        print(f"  • {gap.replace('_', ' ').title()}: {count} occurrences ({percentage:.1f}%)")
    
    if findings.performance_issues:
        print(f"\n⏱️ PERFORMANCE ISSUES ({len(findings.performance_issues)} slow tests):")
        for issue in findings.performance_issues[:5]:  # Show top 5
            print(f"  • {issue}")
    
    print("\n" + "="*60)
    print("PROMPT UPDATE RECOMMENDATIONS")
    print("="*60)
    
    print("\n🔬 DIAGNOSTICIAN NODE PROMPT UPDATES:")
    if recommendations["diagnostician_prompt"]:
        for i, rec in enumerate(recommendations["diagnostician_prompt"], 1):
            print(f"\n{i}. {rec}")
    else:
        print("  No specific updates needed")
    
    print("\n⚖️ JUDGE NODE PROMPT UPDATES:")
    if recommendations["judge_prompt"]:
        for i, rec in enumerate(recommendations["judge_prompt"], 1):
            print(f"\n{i}. {rec}")
    else:
        print("  No specific updates needed")
    
    print("\n🌐 SYSTEM-WIDE RECOMMENDATIONS:")
    if recommendations["system_wide"]:
        for i, rec in enumerate(recommendations["system_wide"], 1):
            print(f"\n{i}. {rec}")
    else:
        print("  System is performing well overall")
    
    # Save detailed analysis
    output_file = Path("comprehensive-results/analysis/batch_analysis_sep9.json")
    output_file.parent.mkdir(exist_ok=True)
    
    with open(output_file, "w") as f:
        json.dump({
            "summary": {
                "total_tests": findings.total_tests,
                "successful_root_cause": findings.successful_root_cause,
                "success_rate": f"{findings.successful_root_cause/findings.total_tests*100:.1f}%"
            },
            "common_issues": findings.common_issues,
            "prompt_gaps": findings.prompt_gaps,
            "performance_issues": findings.performance_issues,
            "recommendations": recommendations,
            "detailed_analyses": all_analyses
        }, f, indent=2)
    
    print(f"\n\n✅ Detailed analysis saved to: {output_file}")

if __name__ == "__main__":
    asyncio.run(main())