# Fleet Fan-Out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One agent tool call answers a kubectl question across many clusters in parallel (`execute_kubectl_multi`), with per-cluster truncation so fleet calls can't blow the context window.

**Architecture:** New import-light module `fleet.py` next to `agent.py` holds all fan-out logic (payload building, "all" resolution, concurrent `/debug/execute` calls via `asyncio.gather`, aggregation/truncation). `agent.py` adds a thin `@tool` closure that validates, traces via the interceptor, and delegates. No API/queue/executor changes. Spec: `docs/superpowers/specs/2026-08-14-fleet-fanout-design.md`, issue #53.

**Tech Stack:** httpx (existing dep, `httpx.MockTransport` for tests), pytest + pytest-asyncio (`asyncio_mode = "auto"` already set in pyproject.toml), Helm chart prompt YAML.

**Repo facts** (verified 2026-08-15):
- Tools are closures in `KubentlyAgent._initialize_tools` (`agent.py:254`), closing over `api_url`, `api_key`, `interceptor`; registered at `agent.py:488` as `self.tools = [list_clusters, execute_kubectl]`.
- `validate_kubectl_command(command, allow_write=False)` at `agent.py:57` raises `ValueError` on write verbs.
- `/debug/execute` payload: `{"cluster_id", "command_type" (verb), "args" (list), "namespace" (str|null), "timeout_seconds"}` → 200 with `{"output": ...}`.
- `/debug/clusters` → 200 with `{"clusters": ["id", ...]}`.
- TWO prompt files must stay in sync (they have unrelated pre-existing diffs — touch only our sections): `prompts/system.prompt.yaml` (local dev) and `deployment/helm/kubently/prompts/system.prompt.yaml` (deployed; chart currently `1.0.3`).
- Tests live flat in `tests/`, import via `sys.path.insert(0, ...)` (see `tests/test_webhook.py`).
- Do NOT import `agent.py` in unit tests — it pulls langchain/deepagents/a2a-sdk; `fleet.py` must import only stdlib + httpx.

---

### Task 1: `fleet.py` pure helpers (payload build, truncation, aggregation)

**Files:**
- Create: `kubently/modules/a2a/protocol_bindings/a2a_server/fleet.py`
- Test: `tests/test_fleet.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fleet.py`:

```python
#!/usr/bin/env python3
"""Unit tests for fleet fan-out (execute_kubectl_multi internals)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.a2a.protocol_bindings.a2a_server.fleet import (  # noqa: E402
    PER_CLUSTER_OUTPUT_CAP,
    build_execute_payload,
    format_fleet_results,
    format_section,
)


def test_build_execute_payload_basic():
    p = build_execute_payload("get pods -o wide", "default")
    assert p["command_type"] == "get"
    assert p["args"] == ["pods", "-o", "wide"]
    assert p["namespace"] is None
    assert p["timeout_seconds"] == 30


def test_build_execute_payload_namespace_flag_in_command_wins():
    p = build_execute_payload("get pods -n payments", "default")
    assert p["namespace"] == "payments"


def test_build_execute_payload_namespace_param():
    p = build_execute_payload("get pods", "payments")
    assert p["namespace"] == "payments"


def test_build_execute_payload_all_namespaces_adds_A():
    p = build_execute_payload("get pods", "all")
    assert "-A" in p["args"]
    assert p["namespace"] is None


def test_format_section_plain():
    s = format_section("prod-east", True, "pod-a Running\n")
    assert s == "=== cluster: prod-east ===\npod-a Running"


def test_format_section_error():
    s = format_section("prod-east", False, "HTTP 500: boom")
    assert s == "=== cluster: prod-east ===\nERROR: HTTP 500: boom"


def test_format_section_empty_collapses():
    assert format_section("prod-east", True, "  \n") == "=== cluster: prod-east === (no matching resources)"
    assert (
        format_section("prod-east", True, "No resources found in payments namespace.")
        == "=== cluster: prod-east === (no matching resources)"
    )


def test_format_section_truncates_at_cap():
    s = format_section("prod-east", True, "x" * (PER_CLUSTER_OUTPUT_CAP + 500))
    assert "[truncated — run execute_kubectl on prod-east for full output]" in s
    # header + capped body + truncation note; nowhere near the raw size
    assert len(s) < PER_CLUSTER_OUTPUT_CAP + 200


def test_format_fleet_results_joins_sections():
    out = format_fleet_results([("a", True, "ok"), ("b", False, "down")])
    assert "=== cluster: a ===\nok" in out
    assert "=== cluster: b ===\nERROR: down" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_fleet.py -v`
Expected: FAIL — `ModuleNotFoundError`/`ImportError` for `fleet`.

- [ ] **Step 3: Write the implementation**

Create `kubently/modules/a2a/protocol_bindings/a2a_server/fleet.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_fleet.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add kubently/modules/a2a/protocol_bindings/a2a_server/fleet.py tests/test_fleet.py
git commit -m "[claude]: fleet fan-out helpers: payload build, truncation, aggregation (#53)"
```

---

### Task 2: `fleet.py` async fan-out (`run_fleet_command`)

**Files:**
- Modify: `kubently/modules/a2a/protocol_bindings/a2a_server/fleet.py` (append)
- Test: `tests/test_fleet.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fleet.py`:

```python
import httpx  # noqa: E402

from kubently.modules.a2a.protocol_bindings.a2a_server.fleet import (  # noqa: E402
    MAX_FLEET_CLUSTERS,
    run_fleet_command,
)

API = "http://api.test"
KEY = "k"
PAYLOAD = {"command_type": "get", "args": ["pods"], "namespace": None, "timeout_seconds": 30}


def _mock_client(clusters, per_cluster):
    """MockTransport serving /debug/clusters and /debug/execute.

    per_cluster: cluster_id -> httpx.Response for its /debug/execute call.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/debug/clusters":
            return httpx.Response(200, json={"clusters": clusters})
        if request.url.path == "/debug/execute":
            import json

            cluster_id = json.loads(request.content)["cluster_id"]
            return per_cluster[cluster_id]
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_run_fleet_command_aggregates_named_clusters():
    client = _mock_client(
        ["a", "b"],
        {
            "a": httpx.Response(200, json={"output": "pod-a Running"}),
            "b": httpx.Response(200, json={"output": "pod-b CrashLoopBackOff"}),
        },
    )
    out = await run_fleet_command(API, KEY, ["a", "b"], PAYLOAD, client=client)
    assert "=== cluster: a ===\npod-a Running" in out
    assert "=== cluster: b ===\npod-b CrashLoopBackOff" in out


async def test_run_fleet_command_resolves_all():
    client = _mock_client(
        ["a", "b"],
        {
            "a": httpx.Response(200, json={"output": "x"}),
            "b": httpx.Response(200, json={"output": "y"}),
        },
    )
    out = await run_fleet_command(API, KEY, ["all"], PAYLOAD, client=client)
    assert "=== cluster: a ===" in out and "=== cluster: b ===" in out


async def test_run_fleet_command_error_isolation():
    client = _mock_client(
        ["a", "b"],
        {
            "a": httpx.Response(500, text="boom"),
            "b": httpx.Response(200, json={"output": "fine"}),
        },
    )
    out = await run_fleet_command(API, KEY, ["a", "b"], PAYLOAD, client=client)
    assert "=== cluster: a ===\nERROR: HTTP 500" in out
    assert "=== cluster: b ===\nfine" in out


async def test_run_fleet_command_cap():
    many = [f"c{i}" for i in range(MAX_FLEET_CLUSTERS + 1)]
    client = _mock_client(many, {})
    out = await run_fleet_command(API, KEY, many, PAYLOAD, client=client)
    assert "capped at" in out


async def test_run_fleet_command_no_clusters():
    client = _mock_client([], {})
    out = await run_fleet_command(API, KEY, ["all"], PAYLOAD, client=client)
    assert out == "No clusters are currently registered."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_fleet.py -v`
Expected: the 9 Task-1 tests pass; the 5 new ones FAIL with `ImportError: cannot import name 'run_fleet_command'`.

- [ ] **Step 3: Write the implementation**

Append to `fleet.py`:

```python
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
        if len(clusters) > MAX_FLEET_CLUSTERS:
            return (
                f"Error: {len(clusters)} clusters requested; fleet fan-out is capped at "
                f"{MAX_FLEET_CLUSTERS} per call. Narrow the cluster list and batch the query."
            )
        results = await asyncio.gather(
            *(_execute_on_cluster(client, api_url, api_key, c, payload_base) for c in clusters)
        )
        return format_fleet_results(list(results))
    finally:
        if owns_client:
            await client.aclose()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_fleet.py -v`
Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add kubently/modules/a2a/protocol_bindings/a2a_server/fleet.py tests/test_fleet.py
git commit -m "[claude]: fleet fan-out: concurrent multi-cluster execution with cap + error isolation (#53)"
```

---

### Task 3: Wire `execute_kubectl_multi` tool into the agent

**Files:**
- Modify: `kubently/modules/a2a/protocol_bindings/a2a_server/agent.py` (after the `execute_kubectl` closure ends at ~line 483; replace the `self.tools = ...` line at ~488)

- [ ] **Step 1: Add the tool closure**

In `_initialize_tools`, after the `execute_kubectl` function body and before the `self.tools = ...` line, insert:

```python
        from kubently.modules.a2a.protocol_bindings.a2a_server.fleet import (
            build_execute_payload,
            run_fleet_command,
        )

        @tool
        async def execute_kubectl_multi(
            cluster_ids: list[str],
            command: str,
            namespace: str = "default",
        ) -> str:
            """Run one read-only kubectl command across MANY clusters in parallel.

            Use this for fleet-wide questions ("across all clusters", "which clusters
            have X"). Pass ["all"] to target every registered cluster (capped at 10).
            Results are grouped per cluster; empty results collapse to one line and
            long outputs are truncated — drill into a specific cluster with
            execute_kubectl when you need full output.

            Keep fleet commands filtered and token-efficient (e.g.
            "get pods --field-selector status.phase!=Running").

            Args:
                cluster_ids: Target clusters, or ["all"] for every registered cluster
                command: Full kubectl command (verb, resource, flags) — read-only only
                namespace: Namespace to scope to ("all" adds -A)

            Returns:
                Aggregated output, one "=== cluster: <id> ===" section per cluster
            """
            try:
                validate_kubectl_command(command, allow_write=False)
            except ValueError as e:
                return str(e)

            debug_print(
                f"execute_kubectl_multi called: cluster_ids={cluster_ids}, command={command}"
            )
            tool_call_id = await interceptor.record_tool_call(
                tool_name="execute_kubectl_multi",
                args={"cluster_ids": cluster_ids, "command": command, "namespace": namespace},
                thread_id=getattr(self, "_current_thread_id", None),
            )
            try:
                output = await run_fleet_command(
                    api_url, api_key, cluster_ids, build_execute_payload(command, namespace)
                )
                await interceptor.record_tool_result(tool_call_id, output)
                return output
            except Exception as e:
                error_msg = f"Error executing fleet command: {e!s}"
                await interceptor.record_tool_result(tool_call_id, None, error_msg)
                return error_msg
```

Then replace:

```python
        self.tools = [list_clusters, execute_kubectl]
```

with:

```python
        self.tools = [list_clusters, execute_kubectl, execute_kubectl_multi]
```

- [ ] **Step 2: Verify syntax and that unit tests still pass**

Run: `python -m py_compile kubently/modules/a2a/protocol_bindings/a2a_server/agent.py && python -m pytest tests/test_fleet.py tests/test_dynamic_whitelist.py -v`
Expected: compile OK, all tests pass. (Full agent import is exercised in the Task 6 E2E, not here — agent.py needs the deployed stack.)

- [ ] **Step 3: Commit**

```bash
git add kubently/modules/a2a/protocol_bindings/a2a_server/agent.py
git commit -m "[claude]: agent: execute_kubectl_multi fleet tool with interceptor tracing (#53)"
```

---

### Task 4: Prompt updates (both YAML files) + chart bump

**Files:**
- Modify: `prompts/system.prompt.yaml` (Tools list ~line 310; after "## Cluster Selection" section ~line 324)
- Modify: `deployment/helm/kubently/prompts/system.prompt.yaml` (Tools list ~line 328; after its "## Cluster Selection" section)
- Modify: `deployment/helm/kubently/Chart.yaml:5` (`version: 1.0.3` → `1.0.4`)

The two prompt files have unrelated pre-existing diffs (todo_write vs write_todos, symptom-vs-root-cause section) — add our lines to BOTH, change nothing else.

- [ ] **Step 1: Add the tool to each Tools list**

In both files, after the `- execute_kubectl: ...` bullet, add (matching each file's two-space YAML block indent):

```yaml
  - execute_kubectl_multi: Run one read-only kubectl command across many clusters in parallel (fleet queries)
```

- [ ] **Step 2: Add the Fleet Queries section to both files**

Immediately after each file's `## Cluster Selection` bullet list (before `# Code References`), insert:

```yaml
  ## Fleet Queries

  - When the user asks about MULTIPLE clusters or the whole fleet ("across all clusters", "which clusters have X", "fleet-wide"), use execute_kubectl_multi with cluster_ids=["all"] (or the named subset) instead of sequential execute_kubectl calls.
  - Fleet results are a triage view: per-cluster output is truncated. Drill into a specific cluster with execute_kubectl when you need detail.
  - Keep fleet commands filtered and token-efficient (e.g. --field-selector status.phase!=Running); never dump unfiltered resources from every cluster.
  - For single-cluster questions keep using execute_kubectl.
```

- [ ] **Step 3: Bump the chart version**

`deployment/helm/kubently/Chart.yaml`: `version: 1.0.3` → `version: 1.0.4`.

- [ ] **Step 4: Validate YAML + helm template**

Run: `python -c "import yaml; yaml.safe_load(open('prompts/system.prompt.yaml')); yaml.safe_load(open('deployment/helm/kubently/prompts/system.prompt.yaml')); print('yaml ok')" && helm template kubently ./deployment/helm/kubently -f deployment/helm/test-values.yaml >/dev/null && echo "helm ok"`
Expected: `yaml ok` then `helm ok`.

- [ ] **Step 5: Commit**

```bash
git add prompts/system.prompt.yaml deployment/helm/kubently/prompts/system.prompt.yaml deployment/helm/kubently/Chart.yaml
git commit -m "[claude]: prompt: fleet-queries guidance + execute_kubectl_multi (chart 1.0.4) (#53)"
```

---

### Task 5: README repositioning

**Files:**
- Modify: `README.md` (Overview paragraph ~line 14; Key Features list ~line 18)

- [ ] **Step 1: Replace the Overview paragraph**

Replace the current single Overview paragraph with:

```markdown
Kubently (*Kubernetes + Agentically*) is a **free, self-hosted, vendor-neutral multi-cluster Kubernetes troubleshooter**. Ask one question, get AI-diagnosed answers from every cluster in your fleet in parallel — including clusters you can't reach directly: executors dial **outbound** to the central API, so there's no inbound ingress, no shared kubeconfig, and no per-cluster credentials to distribute.

Agents collaborate over the [A2A (Agent-to-Agent) protocol](https://a2a-protocol.org/latest/), and any MCP client (Claude Code, Cursor, Claude Desktop) can use Kubently as a tool out of the box.
```

- [ ] **Step 2: Reorder Key Features to lead with the differentiator**

Replace the Key Features bullet list with (same items, fleet-first order, two new leads):

```markdown
- **Multi-Cluster Fleet Troubleshooting**: One question fans out across all registered clusters in parallel
- **Outbound-Dial Executors**: Reach clusters behind firewalls/NAT — no inbound ingress, no shared kubeconfig
- **Natural Language Interface**: Conversational Kubernetes troubleshooting and debugging
- **Comprehensive Analysis**: Automated issue detection, root cause analysis, and solution recommendations
- **Multi-LLM Support**: Compatible with Google Gemini, OpenAI, Anthropic, and other providers
- **A2A Protocol**: Industry-standard agent-to-agent communication for complex workflows
- **MCP Server**: Optional [Model Context Protocol](docs/MCP.md) endpoint so MCP clients (Claude Desktop, Cursor, custom agents) get direct tool access
- **Security-First**: API key authentication, OAuth/OIDC support, and TLS with cert-manager
```

(Keep any remaining original bullets that follow, unchanged.)

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "[claude]: README: lead with multi-cluster fleet troubleshooting (#53)"
```

---

### Task 6: Changelog + E2E verification (two kind clusters)

**Files:**
- Modify: `CHANGELOG.md` (new entry at top)

- [ ] **Step 1: Changelog entry**

Add under a new `## [Unreleased] - 2026-08-15` heading at the top:

```markdown
### Added
- **Fleet fan-out: `execute_kubectl_multi` (chart 1.0.4)** — one read-only kubectl
  command runs across many clusters in parallel (`["all"]` targets every registered
  cluster, capped at 10). Per-cluster output is hard-truncated at 4KB with a
  drill-down hint and empty results collapse to one line, so fleet calls stay
  context-safe. New "Fleet Queries" prompt section steers the agent to it for
  fleet-wide questions. README repositioned around multi-cluster troubleshooting.
```

- [ ] **Step 2: Deploy and register a second cluster**

```bash
ANTHROPIC_API_KEY=<key> ./deployment/scripts/kind-e2e.sh
kind create cluster --name kubently-2
kubectl config use-context kind-kubently   # back to the API cluster context
kubectl port-forward -n kubently svc/kubently-api 8080:8080 &
TOKEN=$(curl -s -X POST http://localhost:8080/admin/agents/kind-kubently-2/token -H "X-API-Key: test-api-key" | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
kubectl --context kind-kubently-2 create namespace kubently
kubectl --context kind-kubently-2 create secret generic kubently-executor-token --from-literal=token="$TOKEN" -n kubently
helm install kubently-executor ./deployment/helm/kubently --kube-context kind-kubently-2 \
  --namespace kubently \
  --set api.enabled=false --set redis.enabled=false \
  --set executor.enabled=true --set executor.existingSecret=kubently-executor-token \
  --set executor.clusterId=kind-kubently-2 \
  --set executor.apiUrl=<API URL reachable from the second kind cluster>
```

Note: for kind-to-kind networking, use the API cluster's docker network address (e.g. `http://<kind-kubently-control-plane-IP>:<nodeport>`) — check how `kind-e2e.sh` exposes the API and reuse that mechanism. Verify both clusters registered: `curl -s http://localhost:8080/debug/clusters -H "X-API-Key: test-api-key"` → both IDs.

- [ ] **Step 3: Fleet query through A2A**

Break a pod on cluster 2 first (`kubectl --context kind-kubently-2 run bad --image=nosuchimage`), then per `docs/TEST_QUERIES.md` format:

```bash
curl -s -X POST http://localhost:8080/a2a/ -H "Content-Type: application/json" -H "X-API-Key: test-api-key" -d '{
  "jsonrpc": "2.0", "id": "1", "method": "message/stream",
  "params": {"message": {"messageId": "msg-fleet-1", "role": "user",
    "parts": [{"partId": "part-1", "text": "Which pods are failing across all clusters?"}]}}
}'
```

Expected: response covers BOTH clusters (the broken pod on `kind-kubently-2` found); server logs show a single `execute_kubectl_multi` tool call, not sequential `execute_kubectl` calls.

- [ ] **Step 4: Run the full unit suite**

Run: `python -m pytest tests/ -v --ignore=tests/e2e --ignore=tests/integration`
Expected: all pass.

- [ ] **Step 5: Commit and clean up**

```bash
git add CHANGELOG.md
git commit -m "[claude]: changelog: fleet fan-out (#53)"
kind delete cluster --name kubently-2   # after verification
```

---

### Deferred out of this plan
- Demo GIF recording (needs a human screen-capture pass; the Task 6 two-cluster setup is the scenario script for it).
- MCP `execute_kubectl` multi variant — MCP's `ask_kubently` already routes through this agent and gets fan-out for free.
