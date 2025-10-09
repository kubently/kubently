# Kubently Thoroughness Improvements

## Executive Summary

Based on comparative analysis between Q CLI and Kubently for scenario 13 (service selector mismatch), Q CLI demonstrated superior debugging thoroughness through:
- 4 sequential diagnostic steps vs Kubently's 2
- Explicit pod label verification with `--show-labels`
- Separate endpoint validation
- Clear actual vs expected value comparisons

## Key Improvements Needed

### 1. System Prompt Engineering

Update the agent's system prompt in `agent.py` to include mandatory investigation patterns:

```python
SERVICE_DEBUGGING_PATTERN = """
When a user reports that a Service is not sending traffic to its Pods, you MUST follow this exact investigation pattern:

1. **Inspect the Service:**
   - Run `kubectl describe service <service-name>` to identify the Selector
   - State the selector clearly to the user

2. **Verify Pod Labels:**
   - Run `kubectl get pods --show-labels` to see labels on ALL relevant pods
   - Do NOT assume the selector is wrong; verify what exists first

3. **Verify Selector Match:**
   - Run `kubectl get pods -l <selector>` using the exact selector from the service
   - Compare output with user's expectation
   - If output is empty, you have found a primary cause

4. **Inspect Endpoints:**
   - Run `kubectl get endpoints <service-name>`
   - If <none> appears in ENDPOINTS column, explicitly state that control plane found no matching and ready pods

5. **Check Pod Readiness (if pods match but endpoints empty):**
   - Run `kubectl describe pod <pod-name>` on matching pods
   - Check Conditions section (e.g., Ready: False)
   - Check Events for CrashLoopBackOff, failing probes, or image pull errors

6. **Synthesize and Conclude:**
   - Present clear diagnosis comparing Service Selector (Expected) vs Pod Labels (Actual)
   - Provide precise kubectl command to fix the identified issue
"""
```

### 2. Implement "Show, Don't Just Tell" Principle

Modify response format to show evidence before conclusions:

```python
EVIDENCE_BASED_FORMAT = """
When presenting findings, follow this format:

WEAK: "The problem is a label mismatch."

STRONG: "I checked the service `my-service` and its selector is `app=my-app`. 
I then searched for pods with that label and found none. 
However, I see a pod named `my-app-pod` with the labels `app=my-typo`. 
The selector is not matching the pod's labels."
"""
```

### 3. Add Debugging Templates

Create templates for common Kubernetes issues:

```python
DEBUGGING_TEMPLATES = {
    "service_connectivity": SERVICE_DEBUGGING_PATTERN,
    "pod_crash": POD_CRASH_PATTERN,
    "config_issues": CONFIG_DEBUGGING_PATTERN,
    "resource_limits": RESOURCE_DEBUGGING_PATTERN,
    "network_policies": NETWORK_POLICY_PATTERN
}
```

### 4. Enhance Tool Execution

Modify the agent to encourage multiple tool calls:

```python
def get_system_prompt(self):
    return f"""You are a Kubernetes debugging expert focused on THOROUGHNESS over efficiency.

{self.RESPONSE_FORMAT_INSTRUCTION}

CRITICAL REQUIREMENTS:
1. Always perform multiple verification steps - do NOT jump to conclusions
2. Use --show-labels when checking pods to see actual labels
3. Verify endpoints separately from services
4. Show actual vs expected comparisons
5. Follow debugging patterns for specific issue types

{SERVICE_DEBUGGING_PATTERN}

Remember: Completeness and accuracy are more important than minimizing tool calls.
"""
```

### 5. Implement Comparison Logic

Add a comparison formatter to highlight mismatches:

```python
def format_comparison(expected, actual):
    return f"""
    🔍 COMPARISON:
    Expected: {expected}
    Actual:   {actual}
    Status:   {'✅ MATCH' if expected == actual else '❌ MISMATCH'}
    """
```

### 6. Update Response Structure

Enhance the ResponseFormat class:

```python
class ResponseFormat(BaseModel):
    thinking: str = Field(
        description="Step-by-step reasoning with each kubectl command and why it's needed"
    )
    evidence: list[str] = Field(
        description="Raw outputs from kubectl commands executed",
        default_factory=list
    )
    comparisons: list[dict] = Field(
        description="Actual vs expected comparisons found during investigation",
        default_factory=list
    )
    response: str = Field(
        description="Final response with clear problem statement and actionable fix"
    )
```

## Implementation Priority

1. **High Priority:** Update system prompt with debugging patterns
2. **High Priority:** Add --show-labels to pod checking
3. **Medium Priority:** Implement comparison formatting
4. **Medium Priority:** Add debugging templates
5. **Low Priority:** Enhance response structure

## Expected Outcomes

- Increase average tool calls from 2 to 4-5 for service issues
- Provide clear actual vs expected comparisons
- Reduce ambiguity in problem identification
- Match Q CLI's thoroughness while maintaining AI flexibility

## Testing

Test with scenario 13 to verify:
1. Agent checks pods with --show-labels
2. Agent verifies endpoints separately
3. Response shows actual vs expected labels
4. Clear fix recommendation provided