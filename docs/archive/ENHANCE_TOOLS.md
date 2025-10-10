Persona: You are a senior software architect focused on creating powerful and secure AI developer tools. You will be enhancing a Kubernetes debugging agent to give it more precise control over its information-gathering capabilities.

Primary Goal: Your task is to upgrade the AI agent's primary execute_kubectl tool to allow it to pass additional arguments, such as output formatters (-o yaml, -o jsonpath). This will enable the agent to view the full configuration of a resource and request specific fields, significantly improving its diagnostic abilities.

Context: The agent's current execute_kubectl tool is too restrictive. It only accepts command, resource, and namespace, with no way to pass other vital kubectl flags. This prevents the agent from performing fundamental debugging tasks like viewing a pod's YAML manifest. You will add this capability while ensuring the system's security whitelist is updated to permit these new, safe flags.

Implementation Plan:
Step 1: Update the API Model to Accept Extra Arguments

File to Modify: kubently/modules/api/models.py

Modify ExecuteCommandRequest: Add a new optional field to this Pydantic model:

extra_args: Optional[List[str]] = Field(None, description="A list of additional, safe arguments to pass to the kubectl command, like ['-o', 'yaml'].")

Step 2: Update the API Endpoint to Process Extra Arguments

File to Modify: kubently/main.py

Modify the execute_command function:

Find the line where kubectl_args is constructed.

After that list is created, add logic to check if request.extra_args exists and, if so, extend the kubectl_args list with its contents.

# After building the initial kubectl_args...
if request.extra_args:
    kubectl_args.extend(request.extra_args)

Step 3: Enhance the Agent's execute_kubectl Tool

File to Modify: kubently/modules/a2a/protocol_bindings/a2a_server/agent.py

Update the tool's function signature: Add a new optional extra_args parameter to the execute_kubectl tool.

async def execute_kubectl(
    cluster_id: str,
    command: str,
    resource: str = "pods",
    namespace: str = "default",
    extra_args: Optional[list[str]] = None
) -> str:
