"""Fleet fan-out: run one read-only kubectl command across many clusters concurrently.

Deliberately import-light (stdlib + httpx only) so unit tests can import it
without pulling the langchain/a2a stack that agent.py requires.
"""

import asyncio

import httpx

MAX_FLEET_CLUSTERS = 10
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
