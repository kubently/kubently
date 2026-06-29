# Kubently — Resume Punch List

Written 2026-06-29 after a competitive-landscape review. Strategic frame: Kubently's
defensible niche is **free, OSS, vendor-neutral, multi-cluster K8s troubleshooting via
outbound executors** — the one thing kagent (the strongest competitor) deliberately keeps
out of open-source and paywalls behind Solo Enterprise. Lead with that. Treat A2A as a
checkbox, not the headline.

See `.claude/.../memory/kubently-competitive-landscape.md` for the full competitive read.

---

## Status (updated 2026-06-29)
- ✅ **#1 MCP server — DONE.** `kubently/modules/mcp/{tools,server}.py`; mounted at `/mcp`
  in `main.py` lifespan (conditional on `mcp` SDK). Tools: `list_clusters`,
  `execute_kubectl`. Tests: `tests/test_mcp_tools.py` (3), `tests/test_mcp_server.py` (1).
  `mcp>=1.2.0` added to the `a2a` extra. Note: full app-boot verification is blocked locally
  by a pre-existing `a2a-sdk` version drift (`PushNotificationSender` import in
  `modules/a2a/__init__.py`); verify the mount end-to-end via `./deploy-test.sh`.
- ✅ **#3 Whitelist enforcement — DONE.** Wired `validate_command()` into
  `sse_executor._run_kubectl`; whitelist loaded in `__init__`. Default READ_ONLY config
  relaxed to allow configmaps (secrets still blocked). Made `sseclient` optional / dropped
  dead `httpx` import so the executor is unit-testable. Tests:
  `tests/test_executor_enforcement.py` (2), `tests/test_dynamic_whitelist.py` (+1).
- ✅ **#2 cloud-auth — DONE.** Added `executor.serviceAccount.annotations` passthrough to the
  chart (`executor-serviceaccount.yaml` + values.yaml) so IRSA / GKE Workload Identity work
  with zero code change. Docs: `docs/CLOUD_AUTH.md`. Verified via `helm template`.
- ⬜ #4 fan-out, mTLS — not started (deferred/optional).

---

## P0 — Sharpen the differentiator

### 1. Expose Kubently as an MCP server
**Why:** MCP won the agent tool-calling layer (~10k servers, ~97M monthly downloads). Today
Kubently only speaks A2A. Exposing your *multi-cluster execution* as MCP tools makes the whole
fleet callable from any MCP client (Claude Desktop, Cursor, other agents) — this amplifies the
differentiator instead of competing with the ecosystem.
**Scope (lazy):** thin adapter over the existing central API. The tool implementations already
exist in `agent.py`; the MCP server just wraps `/debug/clusters` + `/debug/execute`.
- Tools to expose: `list_clusters()`, `execute_kubectl(cluster_id, command)`, and optionally
  `get_pod_logs` / `debug_resource` (reuse existing impls).
- Use the official `mcp` Python SDK (FastMCP), streamable-HTTP transport.
- Mount at `/mcp/` alongside the A2A mount at `/a2a/` in the FastAPI app, OR run as a sibling
  process. Prefer mounting — fewer moving parts.
**Files:** new `kubently/modules/mcp/server.py`; wire into `kubently/main.py`. Reuse tool logic
from `kubently/modules/a2a/protocol_bindings/a2a_server/agent.py`.
**Effort:** S–M. Mostly plumbing; no new business logic.
**ponytail:** do NOT build a custom MCP framework. FastMCP + 3–4 tool wrappers. Done.

### 2. Document the cloud-auth extensibility (IRSA / GKE Workload Identity)
**Why:** This is a real strength and currently undocumented. The executor has zero cloud SDK
code — it shells to `kubectl`, so AWS IRSA/assume-role and GKE Workload Identity work purely via
ServiceAccount annotations on the executor pod. That's the vendor-neutral story done right.
**Scope:** a docs page + Helm values examples showing the SA annotations for each cloud. No code
change expected (verify the executor pod template lets you set SA + annotations via Helm).
**Files:** new `docs/CLOUD_AUTH.md`; check `kubently/modules/executor/k8s-deployment.yaml` /
Helm executor templates expose `serviceAccount.annotations`.
**Effort:** S (mostly docs). Add a Helm passthrough for SA annotations if missing.

---

## P0 — Close the real security gap (small change, real payoff)

### 3. Connect the existing whitelist enforcement to the execution path
**Why:** The enforcement logic already exists and is real — `validate_command()` is fully
implemented (`dynamic_whitelist.py:442`). It is simply **never called on the execution path**.
Repo-wide there are ZERO call sites for `validate_command` (only its definition + a README line).
`_run_kubectl` (`sse_executor.py:246`) runs `subprocess.run(["kubectl"] + args)` raw. The
whitelist is loaded only in `_get_capabilities_payload()` (`sse_executor.py:297`), which calls
`get_config_summary()` to *advertise* capabilities to `/executor/capabilities` — not to gate
execution. So the lock is built but not installed on the door, which is the dangerous kind: it
reads as protection in a security review.
**Current real defense without it:** executor-SA RBAC (read-only, no secrets) + agent-side verb
blocking in `validate_kubectl_command` (`agent.py:57`). Gap it closes: an API-key holder POSTing
directly to `/debug/execute` bypasses the agent-side check — today only RBAC stops a write verb.
Wiring the executor check makes it genuine three-layer defense.
**Scope (small):** load the whitelist once in `SSEExecutor.__init__`, then in `_run_kubectl`:
```python
if WHITELIST_AVAILABLE:
    ok, reason = self._whitelist.validate_command(args)
    if not ok:
        return {"success": False, "error": f"Blocked by whitelist: {reason}",
                "status": "BLOCKED", "return_code": -1}
```
**Files:** `kubently/modules/executor/sse_executor.py` (call site + init). Logic already exists in
`dynamic_whitelist.py` — no new validator needed. Add one unit test that a write verb returns
`BLOCKED`.
**Effort:** S. Also fix CLAUDE.md, which currently implies enforcement that doesn't happen.

---

## P2 — Optional, only if the use case is real

### 4. Cross-cluster simultaneous fan-out
**Why / honest scoping:** Multi-cluster targeting already works (one cluster per
`execute_kubectl` call; agent issues sequential calls for several). What's missing is a single
"broadcast to clusters [A,B,C] and aggregate" primitive. Only build this if "show me X across
ALL clusters at once" is a real user workflow — for interactive debugging, sequential is usually
fine.
**Scope if built:** add `execute_kubectl_multi(cluster_ids: list, command)` that publishes to N
channels concurrently and aggregates results by `command_id`. The Redis-per-cluster routing
already supports it; it's an aggregation layer, not new transport.
**Files:** `agent.py` (new tool), `main.py` (batch publish + result correlation).
**Effort:** M.
**ponytail:** skip until a user asks. YAGNI.

---

## Explicitly deferred (do NOT do yet)
- **mTLS / per-executor cert identity.** The zero-trust norm (argocd-agent, OCM) but not worth it
  for current scale/threat model. Redis bearer tokens are fine for now. Revisit if you target
  security-sensitive enterprises.

## Positioning / non-code
- Update README + docs to lead with: *"free, self-hosted, vendor-neutral multi-cluster kubectl
  troubleshooter that reaches clusters you can't (outbound-dial, no inbound ingress, no shared
  kubeconfig)."* A2A becomes a feature bullet, not the pitch.
- Watch kagent issues #417 / #1490 (multi-cluster, both closed not-planned) — if they reopen, the
  competitive gap is closing.
