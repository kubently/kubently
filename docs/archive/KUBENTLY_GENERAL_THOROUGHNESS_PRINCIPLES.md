# Kubently General Thoroughness Principles

## Key Discovery: Q CLI's Thoroughness Model

After analyzing Q CLI's codebase and behavior, the key insight is that **Q CLI doesn't use specific Kubernetes debugging prompts**. Instead, its thoroughness emerges from:

1. **Unrestricted Tool Access**: Q CLI provides `execute_bash` allowing any kubectl command
2. **Natural LLM Investigation**: When unconstrained, LLMs naturally perform thorough multi-step investigations
3. **Purpose-Driven Commands**: Each command execution includes a purpose statement
4. **No Efficiency Pressure**: No structured format forcing quick conclusions

## General Principles for Kubently Thoroughness

### 1. Remove Artificial Constraints

**Current Kubently Approach:**
```python
# Structured response format that encourages jumping to conclusions
class ResponseFormat(BaseModel):
    thinking: str  # Single thinking step
    response: str  # Final response
```

**Improved Approach:**
```python
# Allow iterative investigation
class InvestigationStep(BaseModel):
    command: str
    purpose: str
    findings: str

class ResponseFormat(BaseModel):
    investigation_steps: list[InvestigationStep]
    synthesis: str  # Only after thorough investigation
```

### 2. Encourage Natural Investigation Patterns

**System Prompt Modifications:**
```python
INVESTIGATION_PRINCIPLES = """
When debugging Kubernetes issues:

1. NEVER jump to conclusions - investigate thoroughly
2. Use multiple commands to verify each hypothesis
3. Check related resources even if not explicitly mentioned
4. Compare actual state vs expected state
5. Validate your findings with additional checks

IMPORTANT: Thoroughness is more important than efficiency. Use as many kubectl commands as needed to fully understand the issue.
"""
```

### 3. Implement Progressive Disclosure

Instead of revealing all information at once, encourage step-by-step discovery:

```python
PROGRESSIVE_INVESTIGATION = """
Follow this investigation pattern:
1. Identify the reported symptom
2. Check the most obvious cause
3. Verify with additional commands
4. Explore related resources
5. Validate edge cases
6. Synthesize findings

For each step, explain:
- What you're checking
- Why you're checking it
- What you found
- What it means
"""
```

### 4. Multi-Step Verification Pattern

Encourage checking the same resource from multiple angles:

```python
VERIFICATION_PATTERN = """
For each key resource:
1. List/get the resource
2. Describe for detailed info
3. Check related resources (events, logs, endpoints)
4. Verify dependencies
5. Confirm assumptions
"""
```

### 5. Remove Efficiency Bias

**Current Issue**: Kubently's response format encourages efficiency
**Solution**: Explicitly value thoroughness

```python
THOROUGHNESS_EMPHASIS = """
Your goal is COMPLETE UNDERSTANDING, not quick answers.

Good investigation:
- Uses 5-10 commands to verify a simple issue
- Checks multiple related resources
- Validates assumptions explicitly
- Shows evidence for conclusions

Poor investigation:
- Jumps to conclusions after 1-2 commands
- Assumes without verification
- Misses related issues
"""
```

## Implementation Strategy

### Phase 1: System Prompt Updates
1. Remove structured response format constraints
2. Add investigation principles to system prompt
3. Emphasize thoroughness over efficiency
4. Add progressive disclosure guidance

### Phase 2: Tool Enhancement
1. Make `execute_kubectl` more flexible with extra_args
2. Add command chaining support
3. Include purpose statements in tool calls
4. Remove artificial limitations

### Phase 3: Response Format Evolution
1. Support multi-step investigations
2. Show command progression
3. Include verification steps
4. Present synthesis only after thorough investigation

### Phase 4: Behavioral Reinforcement
1. Count and encourage multiple tool calls
2. Reward thorough investigations in examples
3. Penalize premature conclusions
4. Celebrate comprehensive analysis

## Example: Service Connectivity Issue

**Current Kubently (2 steps):**
1. kubectl get services
2. kubectl describe service (finds selector mismatch)
→ Conclusion: "Label mismatch"

**Improved Kubently (6+ steps):**
1. kubectl get services -n namespace
   - Purpose: Identify the service configuration
2. kubectl get pods -n namespace --show-labels
   - Purpose: See actual pod labels
3. kubectl describe service service-name -n namespace
   - Purpose: Verify selector configuration
4. kubectl get endpoints service-name -n namespace
   - Purpose: Confirm no endpoints created
5. kubectl get pods -l "selector" -n namespace
   - Purpose: Verify selector finds no pods
6. kubectl get events -n namespace --field-selector involvedObject.name=service-name
   - Purpose: Check for any service-related events
→ Synthesis: "Service selector 'app=wrong-label' doesn't match pod labels 'app=my-app'. No endpoints created. No matching pods found with selector query."

## Metrics for Success

1. **Average Tool Calls**: Increase from 2 to 5-7 per investigation
2. **Verification Steps**: At least 2 verification commands per hypothesis
3. **Related Resource Checks**: Always check events, logs, and dependencies
4. **Explicit Comparisons**: Show actual vs expected in findings
5. **Progressive Understanding**: Build knowledge incrementally

## Key Takeaway

Q CLI's thoroughness isn't from complex prompts—it's from giving the LLM freedom to investigate naturally. By removing Kubently's efficiency constraints and encouraging natural investigation patterns, we can achieve similar thoroughness across all Kubernetes troubleshooting scenarios.