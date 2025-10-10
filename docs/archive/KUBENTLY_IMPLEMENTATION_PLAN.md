# Kubently Implementation Plan for Q CLI-Level Thoroughness

## Overview

This plan provides specific code changes to achieve Q CLI's debugging thoroughness across all Kubernetes scenarios. The key is to remove constraints and encourage natural LLM investigation patterns.

**UPDATE**: Based on further analysis, we recommend consolidating all kubectl tools into a single `execute_kubectl` tool, matching Q CLI's approach with `execute_bash`. See TOOL_CONSOLIDATION_PLAN.md for details.

## Implementation Changes

### 1. Update System Prompt in agent.py

Replace the current system prompt with one that encourages thorough investigation:

```python
# In agent.py, update the system prompt section

INVESTIGATION_SYSTEM_PROMPT = """You are a Kubernetes debugging expert focused on THOROUGH INVESTIGATION.

CRITICAL PRINCIPLES:
1. NEVER jump to conclusions - investigate systematically
2. Use multiple kubectl commands to verify each hypothesis
3. Check related resources even if not explicitly mentioned
4. Always compare actual state vs expected state
5. Validate findings with additional verification commands

INVESTIGATION APPROACH:
- Start with the reported symptom
- Use 5-10+ kubectl commands for even simple issues
- Check pods, services, endpoints, events, logs, and configs
- Build understanding incrementally
- Only synthesize after thorough investigation

IMPORTANT: Completeness and accuracy are MORE important than efficiency. Use as many tool calls as needed to FULLY understand the issue.

For each kubectl command, include:
- What you're investigating
- Why this command helps
- What you discovered
- What it means for the issue
"""

# Update the get_system_prompt method
def get_system_prompt(self) -> str:
    tools_str = "\n".join([f"- {tool.name}: {tool.description}" for tool in self.tools])
    
    return f"""{INVESTIGATION_SYSTEM_PROMPT}

## Available Tools:
{tools_str}

## Response Format:
Provide your investigation as a series of steps, each with:
1. The kubectl command and its purpose
2. Key findings from the output
3. What this tells us about the issue

After thorough investigation, provide a synthesis with:
- Root cause identification
- Evidence supporting your conclusion
- Recommended fix with specific commands
"""
```

### 2. Remove Structured Response Format Constraint

Replace the rigid ResponseFormat with a more flexible approach:

```python
# Remove or comment out the current ResponseFormat class
# class ResponseFormat(BaseModel):
#     thinking: str = Field(...)
#     response: str = Field(...)

# Remove the structured format instruction
# RESPONSE_FORMAT_INSTRUCTION = "..."

# Let the LLM respond naturally without JSON constraints
```

### 3. Update Tool Descriptions to Encourage Exploration

```python
# In the tool initialization section

async def execute_kubectl(
    cluster_id: str,
    command: str,
    resource: str = "",
    namespace: str = "default",
    extra_args: list[str] | None = None
) -> str:
    """Execute kubectl commands for thorough Kubernetes investigation.
    
    Use this liberally to explore and verify. Common investigation patterns:
    - kubectl get <resource> -n <namespace> --show-labels
    - kubectl describe <resource> <name> -n <namespace>
    - kubectl get events -n <namespace> --sort-by='.lastTimestamp'
    - kubectl logs <pod> -n <namespace> --tail=50
    - kubectl get endpoints <service> -n <namespace>
    - kubectl get <resource> -o yaml -n <namespace>
    
    IMPORTANT: Use multiple commands to build complete understanding.
    Don't assume - verify everything with additional commands.
    """
```

### 4. Add Investigation Tracking

Track the investigation progress to ensure thoroughness:

```python
# Add to KubentlyAgent class
class KubentlyAgent:
    def __init__(self, redis_client=None):
        # ... existing init code ...
        self.investigation_steps = []
        self.min_investigation_steps = 4  # Minimum steps for thoroughness
    
    async def track_investigation_step(self, command: str, purpose: str, findings: str):
        """Track each investigation step for thoroughness."""
        self.investigation_steps.append({
            "command": command,
            "purpose": purpose,
            "findings": findings,
            "timestamp": datetime.now().isoformat()
        })
        
        # Log structured data for analysis
        structured_log({
            "investigation_step": len(self.investigation_steps),
            "command": command,
            "purpose": purpose
        })
```

### 5. Modify Tool Call Interceptor

Update the tool call interceptor to encourage multiple calls:

```python
# In tool_call_interceptor.py or within agent.py

def should_continue_investigation(self, steps_taken: int) -> bool:
    """Encourage continued investigation."""
    if steps_taken < self.min_investigation_steps:
        return True
    
    # Check if recent findings suggest more investigation needed
    recent_findings = self.investigation_steps[-2:] if len(self.investigation_steps) >= 2 else []
    
    # Continue if recent steps revealed new questions
    for step in recent_findings:
        if any(keyword in step["findings"].lower() for keyword in 
               ["unclear", "need to check", "verify", "confirm", "strange", "unexpected"]):
            return True
    
    return False
```

### 6. Add Pre-Investigation Context

Before starting investigation, set the context:

```python
# Add to agent initialization or message handling

PRE_INVESTIGATION_PROMPT = """
Before investigating, remember:
1. A thorough investigation typically requires 5-10+ kubectl commands
2. Check the obvious, then verify with related resources
3. Don't trust assumptions - verify everything
4. Look for patterns and anomalies
5. Consider what the user might not have mentioned

Start with broad discovery, then narrow down to specifics.
"""
```

### 7. Example Investigation Pattern

Add examples to guide the LLM:

```python
EXAMPLE_INVESTIGATION = """
Example thorough investigation for "Service not working":

1. kubectl get svc -n <namespace>
   Purpose: See all services and their basic config
   
2. kubectl get pods -n <namespace> --show-labels
   Purpose: Check pod labels for selector matching
   
3. kubectl describe svc <service> -n <namespace>
   Purpose: Detailed service config including selectors
   
4. kubectl get endpoints <service> -n <namespace>
   Purpose: Verify if endpoints were created
   
5. kubectl get pods -l <selector> -n <namespace>
   Purpose: Confirm selector matches pods
   
6. kubectl describe pods -l <selector> -n <namespace>
   Purpose: Check pod status and readiness
   
7. kubectl get events -n <namespace> --field-selector involvedObject.name=<service>
   Purpose: Look for service-related events
   
8. kubectl logs <pod> -n <namespace> --tail=50
   Purpose: Check application logs for errors

Only after ALL these checks should you conclude the root cause.
"""
```

### 8. Update Default LLM Parameters

If possible, adjust LLM parameters to encourage exploration:

```python
# In LLM initialization
llm_config = {
    "temperature": 0.3,  # Slightly higher for exploration
    "max_tokens": 4096,  # Ensure enough tokens for thorough investigation
    # Remove any "be concise" or "be efficient" instructions
}
```

## Testing the Implementation

1. Test with scenario 13 (service selector mismatch)
   - Should perform 5+ kubectl commands
   - Should explicitly check pod labels
   - Should verify endpoints separately
   - Should show actual vs expected comparison

2. Test with other scenarios to ensure general thoroughness

3. Monitor average tool calls per investigation

## Rollout Plan

1. **Phase 1**: Update system prompt and remove response format constraints
2. **Phase 2**: Add investigation tracking and examples
3. **Phase 3**: Fine-tune based on test results
4. **Phase 4**: Add metrics to track thoroughness improvement

## Success Metrics

- Average kubectl commands per investigation: 5-7 (up from 2)
- Explicit verification steps: 80%+ of investigations
- Related resource checks: 90%+ include events/logs
- User satisfaction with thoroughness: Measure via feedback

## Key Insight

The main change is philosophical: prioritize thoroughness over efficiency. Q CLI succeeds because it lets the LLM investigate naturally without forcing quick conclusions. By removing Kubently's constraints and encouraging exploration, we can achieve similar thoroughness across all debugging scenarios.