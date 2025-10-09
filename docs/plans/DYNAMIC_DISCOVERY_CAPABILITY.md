Persona: You are a senior software architect specializing in distributed systems and AI-powered tools. Your task is to implement a critical security and functionality feature: making an AI agent aware of the permissions of the remote executors it commands.

Primary Goal: Modify the Kubently system to allow the A2A (Agent-to-Agent) AI agent to dynamically discover the capabilities (e.g., allowed kubectl verbs) of a specific remote executor. This prevents the agent from attempting to use tools that are forbidden by the executor's configuration, improving efficiency and user experience.

Context: The system has two key components: an AI agent on the central API server and an executor running in each remote Kubernetes cluster. The executor has a security mode (readOnly, extendedReadOnly, etc.) defined in its dynamic_whitelist.py configuration, which limits what kubectl commands it can run. The agent currently has no knowledge of these per-cluster limitations.

You will implement a "meta-command" that uses the existing command execution pathway to fetch these capabilities.

Implementation Plan:
Step 1: Enhance the Executor to Report Its Capabilities

The executor needs to respond to a special, non-kubectl command.

File to Modify: kubently/modules/executor/sse_executor.py

Modify the _run_kubectl method:

Before attempting to run a subprocess, check if the received command is a special meta-command. Let's use kubently-internal get-capabilities.

If the command matches, do not execute it as a subprocess.

Instead, load the DynamicCommandWhitelist configuration that the executor is already using.

Call the get_config_summary() method on the whitelist instance to get its current state (mode, allowed verbs, etc.).

Return this summary as a JSON string in the output field of the result dictionary, ensuring success is true.

File to Modify: kubently/modules/executor/dynamic_whitelist.py

Update get_config_summary():

Ensure this method returns a dictionary containing at least the mode and the sorted list of allowed_verbs. This is the information the agent needs.

Step 2: Create a New Tool for the Agent

The agent needs a new tool to trigger this meta-command.

File to Modify: kubently/modules/a2a/protocol_bindings/a2a_server/agent.py

Define a new tool function:

Create a new asynchronous tool named get_cluster_capabilities.

It should take one argument: cluster_id: str.

The tool's implementation will call the existing execute_kubectl tool, but with our special meta-command.

command: kubently-internal

resource: get-capabilities

namespace: default (or any placeholder, it will be ignored)

The docstring for this tool is critical. It must instruct the LLM on when and why to use it. Example: "You MUST use this tool before running any other command on a cluster for the first time in a conversation to discover its allowed capabilities and security mode. This will tell you which kubectl commands like 'get', 'logs', or 'exec' are available."

Register the new tool:

Add get_cluster_capabilities to the list of tools provided to the create_react_agent function.

Step 3: Update the System Prompt to Enforce a "Check-First" Policy

This is the most important step. The agent's "brain" must be taught to use its new capability.

File to Modify: prompts/system.prompt.yaml

Add a new core rule to the content section:

Add a new bullet point under "Core rules" that is clear and unambiguous.

Rule Text: "- Capability Check: Before executing any operation on a cluster for the first time in a conversation, you MUST use the get_cluster_capabilities tool to determine its permissions. You MUST NOT attempt to use a tool if the capabilities check indicates its verb (e.g., 'exec', 'port-forward') is not in the 'allowed_verbs' list."

Add a new few-shot example:

Create a new example that demonstrates the correct "check-then-act" sequence.

Example 7: Capability check before a restricted command
- User: "exec into the nginx pod in the kind cluster"
- Assistant (thought): "The user wants to 'exec', which is a restricted command. I must first check the capabilities of the 'kind' cluster before proceeding. I have not checked this cluster yet in this conversation."
- Assistant (tool: get_cluster_capabilities with cluster_id="kind")
  returns: "{'mode': 'readOnly', 'allowed_verbs': ['get', 'describe', 'logs', ...]}"
- Assistant (thought): "The capabilities check for the 'kind' cluster shows it is in 'readOnly' mode and the verb 'exec' is not in the allowed_verbs list. Therefore, I cannot fulfill this request."
- Assistant: "I cannot execute into the pod. The target cluster 'kind' is in a read-only security mode and does not permit 'exec' commands."
