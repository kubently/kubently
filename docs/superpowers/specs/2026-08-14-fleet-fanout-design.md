# Fleet Fan-Out Design

**Date:** 2026-08-14
**Status:** Approved
**Roadmap:** increment 1 of `2026-08-14-fleet-first-roadmap-design.md`

## Problem

Multi-cluster targeting works one cluster per `execute_kubectl` call; fleet-wide
questions ("what's failing across all clusters?") force the agent into slow
sequential calls. The parallel fan-out described in SYSTEM_DESIGN.md was never
implemented.

## Approach

Agent-side gather — no API, queue, or executor changes. The agent's existing
`execute_kubectl` tool already POSTs per-cluster to `/debug/execute`
(`agent.py:454`); fan-out is concurrent HTTP calls one layer up.

New tool in `kubently/modules/a2a/protocol_bindings/a2a_server/agent.py`:

```
execute_kubectl_multi(cluster_ids: list[str], command: str, namespace: str = "default") -> str
```

- Validates the command **once** with existing `validate_kubectl_command`
  (read-only enforcement unchanged).
- `cluster_ids=["all"]` resolves to all registered clusters via `/debug/clusters`.
- Fan-out cap of 10 clusters per call (bounds output-token blowup; over the cap,
  the tool returns an error asking the agent to narrow or batch).
- `asyncio.gather` over the same `/debug/execute` HTTP call used today, with
  per-cluster error isolation: an unreachable/erroring cluster reports its error
  inline under its header; the rest of the batch still returns.
- Output grouped by cluster: `=== cluster: prod-east ===` sections.
- Interceptor tracing (`record_tool_call` / `record_tool_result`) per the repo's
  tool-tracing rule, one call covering the batch with per-cluster results.

Prompt: new "Fleet queries" section in the system prompt — use
`execute_kubectl_multi` when the user asks about multiple/all clusters; keep
single-cluster `execute_kubectl` otherwise.

## Not doing

- No `/debug/execute-multi` API endpoint (no consumer needs it; the A2A agent,
  CLI chat, and MCP `ask_kubently` all route through this agent tool).
- No Redis batch publish / command-id correlation — concurrency lives at the
  HTTP layer.

## Testing

- Unit: aggregation formatting, `["all"]` resolution, fan-out cap, per-cluster
  error isolation (one cluster 500s → others still report).
- E2E: kind-based smoke with 2 registered clusters, one fleet query returning
  both sections.

## Ships with

- README repositioning: lead with "free, self-hosted, vendor-neutral
  multi-cluster kubectl troubleshooter (outbound-dial — no inbound ingress, no
  shared kubeconfig)"; A2A/MCP become feature bullets.
- 30-second demo GIF: three kind clusters, distinct failures, one fleet
  question, parallel diagnosis.
