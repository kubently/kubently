"""Fleet fan-out: run one read-only kubectl command across many clusters concurrently.

Deliberately import-light (stdlib + httpx only) so unit tests can import it
without pulling the langchain/a2a stack that agent.py requires.
"""

import asyncio
import os

import httpx

MAX_FLEET_CLUSTERS = 10  # default; override per-deploy with KUBENTLY_MAX_FLEET_CLUSTERS
PER_CLUSTER_OUTPUT_CAP = 4000
_TRUNCATION_NOTE = "\n[truncated — run execute_kubectl on {cluster_id} for full output]"


def build_execute_payload(command: str, namespace: str = "default") -> dict:
    """Build the /debug/execute payload (minus cluster_id) from a kubectl command string."""
    parts = command.split()
    verb = parts[0]
    args = parts[1:]

    actual_namespace = None
    if "-n" in parts:
        idx = parts.index("-n")
        if idx + 1 < len(parts):
            actual_namespace = parts[idx + 1]
    elif "--namespace" in parts:
        idx = parts.index("--namespace")
        if idx + 1 < len(parts):
            actual_namespace = parts[idx + 1]
    elif namespace == "all":
        if "-A" not in args and "--all-namespaces" not in args:
            args = args + ["-A"]
    elif namespace != "default":
        actual_namespace = namespace

    return {
        "command_type": verb,
        "args": args,
        "namespace": actual_namespace,
        "timeout_seconds": 30,
    }


def format_section(cluster_id: str, success: bool, output: str) -> str:
    """One per-cluster block: collapse empty results, hard-truncate long ones."""
    header = f"=== cluster: {cluster_id} ==="
    if not success:
        return f"{header}\nERROR: {output}"
    text = (output or "").strip()
    if not text or text.startswith("No resources found"):
        return f"{header} (no matching resources)"
    if len(text) > PER_CLUSTER_OUTPUT_CAP:
        text = text[:PER_CLUSTER_OUTPUT_CAP] + _TRUNCATION_NOTE.format(cluster_id=cluster_id)
    return f"{header}\n{text}"


def format_fleet_results(results: list[tuple[str, bool, str]]) -> str:
    return "\n\n".join(format_section(c, ok, out) for c, ok, out in results)


async def _resolve_clusters(
    client: httpx.AsyncClient, api_url: str, api_key: str, cluster_ids: list[str]
) -> list[str]:
    if [c.lower() for c in cluster_ids] != ["all"]:
        return cluster_ids
    resp = await client.get(f"{api_url}/debug/clusters", headers={"X-Api-Key": api_key})
    resp.raise_for_status()
    return resp.json().get("clusters", [])


async def _execute_on_cluster(
    client: httpx.AsyncClient, api_url: str, api_key: str, cluster_id: str, payload_base: dict
) -> tuple[str, bool, str]:
    try:
        resp = await client.post(
            f"{api_url}/debug/execute",
            headers={"X-Api-Key": api_key},
            json={**payload_base, "cluster_id": cluster_id},
        )
        if resp.status_code == 200:
            return (cluster_id, True, resp.json().get("output", ""))
        return (cluster_id, False, f"HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:  # per-cluster isolation: one bad cluster never sinks the batch
        return (cluster_id, False, str(e))


async def run_fleet_command(
    api_url: str,
    api_key: str,
    cluster_ids: list[str],
    payload_base: dict,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Resolve targets, fan out concurrently, return aggregated per-cluster output."""
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=35.0)
    try:
        clusters = await _resolve_clusters(client, api_url, api_key, cluster_ids)
        if not clusters:
            return "No clusters are currently registered."
        max_clusters = int(os.getenv("KUBENTLY_MAX_FLEET_CLUSTERS", str(MAX_FLEET_CLUSTERS)))
        if len(clusters) > max_clusters:
            return (
                f"Error: {len(clusters)} clusters requested; fleet fan-out is capped at "
                f"{max_clusters} per call. Narrow the cluster list and batch the query."
            )
        results = await asyncio.gather(
            *(_execute_on_cluster(client, api_url, api_key, c, payload_base) for c in clusters)
        )
        return format_fleet_results(list(results))
    finally:
        if owns_client:
            await client.aclose()
