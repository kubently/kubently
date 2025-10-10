Persona: You are a senior Python developer and Kubernetes expert, tasked with enhancing the capabilities of an AI debugging agent. You understand the security implications of providing powerful tools to an AI and will implement them with necessary safeguards.

Primary Goal: Expand the toolset of the Kubently A2A (Agent-to-Agent) AI agent to include more advanced diagnostic and remediation capabilities. You will add new tools for resource metrics, interactive commands, and workload restarts, and you will update the agent's core logic and prompt to use them responsibly.

Implementation Plan:
Step 1: Add New Tool Definitions

File to Modify: kubently/modules/a2a/protocol_bindings/a2a_server/agent.py

Define the new tool functions: Add the Python code for the new tools listed below inside this file. Ensure they are decorated with @tool and have detailed, high-quality docstrings explaining their purpose and usage to the LLM.

get_events_for_resource(cluster_id: str, resource_name: str, namespace: str) -> str

get_resource_usage(cluster_id: str, resource_type: str, resource_name: str, namespace: Optional[str] = None) -> str

execute_interactive_command_in_pod(cluster_id: str, pod_name: str, namespace: str, command: List[str]) -> str

restart_workload(cluster_id: str, workload_type: str, workload_name: str, namespace: str) -> str

(Optional Advanced Tool) patch_resource(...)

Register the new tools: Add the newly created tool functions to the self.tools list that is passed to the create_react_agent.

Step 2: Update the Executor's Whitelist

The remote executor needs to know that these new kubectl verbs are potentially valid.

File to Modify: kubently/modules/executor/dynamic_whitelist.py

Update MODE_DEFAULTS: Add the new kubectl verbs to the allowedVerbs sets for the appropriate security modes.

top should be added to readOnly.

exec and port-forward are already correctly in extendedReadOnly.

rollout and patch should be added to fullAccess.

Step 3: Update the System Prompt

The agent must be taught how to use its new tools safely.

File to Modify: prompts/system.prompt.yaml

Update Tool Usage Policy: Add descriptions of the new tools to the "Available tools" section so the agent knows they exist.

Add Remediation Safety Rule: Add a new, explicit rule to the "Core rules" section.

Rule Text: "- Confirmation for Mutative Actions: For any tool that modifies the cluster state (such as restart_workload or patch_resource), you MUST first describe the action you are about to take and ask the user for explicit confirmation (e.g., "I am about to restart the 'api-gateway' deployment. Shall I proceed?"). Do not proceed without a 'yes' or equivalent affirmative response."

Add New Few-Shot Examples: Add examples demonstrating the use of the new tools, including a remediation example that shows the confirmation step.

Example 8: Using a Remediation Tool with Confirmation
- User: "The api-gateway deployment seems stuck. Please restart it."
- Assistant (thought): "The user wants to restart a deployment. This is a mutative action. I will use the `restart_workload` tool, but first I must get confirmation."
- Assistant: "I can restart the 'api-gateway' deployment for you. This will cause a brief service interruption as new pods are created. Shall I proceed?"
- User: "yes go ahead"
- Assistant (thought): "The user has confirmed. I will now call the `restart_workload` tool."
- Assistant (tool: restart_workload with cluster_id="kind", workload_type="deployment", workload_name="api-gateway", namespace="default")
  returns: "deployment.apps/api-gateway restarted"
- Assistant: "The 'api-gateway' deployment has been successfully restarted."
