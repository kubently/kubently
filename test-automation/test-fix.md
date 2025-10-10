# Kubently Test Analysis Report

**Date**: 2025-09-14 16:30:14
**Analysis Model**: gemini-2.5-pro

## Root Cause Analysis
- **Identified Correctly**: Yes
- **Confidence**: 100%
- **Explanation**: The agent correctly identified the root cause of the issue: a mismatch between the Service's `targetPort` (8080) and the container's listening port (80) in the backing Pods. It followed a logical diagnostic path: inspecting the service, seeing no endpoints, inspecting the pods, and comparing the ports.

## System Prompt Enhancements

### Reasoning Enhancement
**Placement**: After the 'Core rules' section, before the 'Pod/Resource search strategy'.
**Rationale**: The agent currently reasons from first principles, which is slow. Explicitly providing a diagnostic strategy for a common problem type transforms its behavior from exploratory to methodical. This codifies expert knowledge directly into its reasoning process, making it faster and more reliable.

**Suggested Text**:
```
### Diagnostic Strategies

When a user reports a problem, first classify it and then follow the appropriate strategy. This is more effective than random `get` commands.

- **For Service/Connectivity Issues**: The root cause is almost always a mismatch between the Service and its backing Pods. Your goal is to prove or disprove this connection.
  1. `describe service`: Look for `Selector`, `TargetPort`, and `Endpoints`. An empty `Endpoints` list is your strongest clue.
  2. `get pods -l <selector>`: Confirm pods with the correct labels exist and are `Running`.
  3. `describe pod <pod-name>`: Find the `containerPort`.
  4. **Compare `TargetPort` and `containerPort`**. This comparison is the key to solving the problem.
```

## Prompt Improvements

#### CRITICAL: The current system prompt lacks explicit, structured guidance on how to debug common Kubernetes problems. It details tool usage and cluster selection but does not provide 'playbooks' for specific scenarios like service connectivity failure.
**Expected Benefit**: This will provide the agent with a deterministic, efficient workflow for a very common class of problems. It will reduce the number of exploratory tool calls, decrease response time, and increase the reliability of the diagnostic process by forcing it to look for key indicators like `<none>` endpoints early.

**Suggested Improvement**:
```
Add a new section to the system prompt titled 'Debugging Playbooks'. Under this section, add the following text:

**Debugging Playbooks**

**Scenario: Service Connectivity Issues ('traffic not reaching pods', 'service not working')**
1.  **Start with the Service**: Your first step is to get a complete picture of the service. Use `execute_kubectl` with `describe service [service-name] -n [namespace]`.
2.  **Analyze the `describe` output CRITICALLY**: 
    - **Check `Endpoints`**: If the `Endpoints` field shows `<none>`, this is the primary problem. It means the service has not found any healthy pods matching its selector.
    - **Check `Selector`**: Note the label selector (e.g., `app=web`).
    - **Check `TargetPort`**: Note the port the service is trying to send traffic to.
3.  **Investigate Pods**: If endpoints are `<none>`:
    - **Verify Pods Exist**: Run `execute_kubectl` with `get pods -l [selector] -n [namespace]`. If this returns no pods, the selector is wrong or no pods are deployed. Report this.
    - **Verify Pod Port**: If pods *do* exist, `describe` one of them (`describe pod [pod-name] -n [namespace]`) and inspect the `Containers` section to find the `Port` the container is listening on.
4.  **Synthesize Findings**: Compare the Service's `TargetPort` with the container's `Port`. If they do not match, you have found the root cause. Report the mismatch clearly.
```

## Recommended Tool Implementations

### check_service_connectivity (Priority: critical)
**Description**: Analyzes the entire path from a Service to its backing Pods. It checks for selector matches, endpoint status, and port alignment. It should be the PREFERRED tool for any user query related to a service not working or traffic not reaching pods. It returns a structured object summarizing the findings.

**Function Signature**:
```python
def check_service_connectivity(cluster_id: str, service_name: str, namespace: str) -> dict
```

**Implementation Notes**: The tool should internally perform the following:
1. `kubectl describe service [service_name] -n [namespace]`. Parse the output for `Selector`, `TargetPort`, and the `Endpoints` list.
2. If endpoints are populated, return a success message.
3. If endpoints are empty, run `kubectl get pods -l [selector] -n [namespace]`. 
4. If no pods are found, report that the selector is not matching any pods.
5. If pods are found, pick one and run `kubectl describe pod [pod_name] -n [namespace]`. Parse the output for the container's listening port.
6. Compare the Service's `targetPort` with the container's port and return a detailed finding about the match or mismatch.

## Architecture Improvements

### Workflow
**Description**: Instead of a single, monolithic agent trying to reason about everything, implement a DAG (Directed Acyclic Graph) or State Machine-based workflow engine. The initial user query is classified, which routes it to a starting node in the graph (e.g., 'Service Triage Node'). This node executes a specific tool (`check_service_connectivity`). Based on the structured output (e.g., `{status: 'NO_ENDPOINTS', reason: 'PORT_MISMATCH'}`), the workflow can transition to a 'Report Fix' node or, if the issue was different (e.g., `{status: 'NO_PODS'}`), it could transition to a 'Deployment Triage Node'.
**Complexity**: medium

**Implementation Approach**:
1. Use a library like `luigi`, `airflow`, or a simpler custom state machine.
2. Define nodes as classes with `run()` methods that call tools.
3. The LLM's role shifts from driving the entire process to primarily classifying the initial problem and summarizing the final results from the workflow's output.

**Expected Benefits**: This architecture makes debugging logic explicit, testable, and more reliable. It reduces the burden on the LLM to perform complex, multi-step reasoning, leading to faster and more consistent results. It also makes it easier to add new diagnostic paths without modifying a massive central prompt.

## Recommendations
- **Immediate/Critical**: Update the system prompt with the 'Debugging Playbooks' section outlined in `prompt_improvements`. This is the fastest way to improve performance on common scenarios.
- **Next Step/High**: Implement the `check_service_connectivity` tool as specified in `tool_implementations`. This will drastically reduce latency and improve efficiency for all service-related debugging tasks.
- **Strategic/Medium**: Redesign the prompt to encourage the use of specialized tools over generic `execute_kubectl` when a playbook matches. For example: 'If the user query matches a Debugging Playbook, ALWAYS prefer using the corresponding high-level tool if available (e.g., `check_service_connectivity` for service issues).'
- **Long-Term**: Investigate a workflow-based architecture as described in `architecture_improvements` to handle more complex and multi-stage debugging scenarios in a more robust and scalable manner.
