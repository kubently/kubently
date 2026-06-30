"""
MCP tool adapters for Kubently.

Thin async wrappers over the central API (the same endpoints the A2A agent uses).
Keeping these free of the `mcp` SDK import makes them unit-testable and lets the
API boot even when the SDK isn't installed.

Read-only safety is enforced downstream by the executor whitelist + RBAC, so these
adapters stay deliberately thin.
"""


import httpx

_TIMEOUT = 30.0


async def list_clusters(api_url: str, api_key: str) -> list[str]:
    """Return the IDs of clusters currently available for troubleshooting."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            f"{api_url}/debug/clusters",
            headers={"X-Api-Key": api_key},
        )
        if response.status_code != 200:
            return []
        return response.json().get("clusters", [])


async def execute_kubectl(
    api_url: str,
    api_key: str,
    cluster_id: str,
    command: str,
    namespace: str = "default",
) -> str:
    """Run a kubectl command against a cluster and return its output.

    `command` is the kubectl command without the leading `kubectl` (e.g. "get pods").
    """
    parts = command.split()
    if not parts:
        return "Error: empty command"
    verb, args = parts[0], parts[1:]

    payload = {
        "cluster_id": cluster_id,
        "command_type": verb,
        "args": args,
        "namespace": namespace,
        "timeout_seconds": 30,
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(
            f"{api_url}/debug/execute",
            headers={"X-Api-Key": api_key},
            json=payload,
        )
        if response.status_code != 200:
            return f"Error: HTTP {response.status_code}: {response.text}"
        return response.json().get("output", "")
