#!/usr/bin/env python3
"""
Tests for the GitOps PR remediation tools against a mocked Git provider API.

Every test runs against httpx.MockTransport — no network, no real GitHub or
GitLab. Contracts under guard:
- The full propose flow (branch off base -> commit -> PR) hits the provider
  API correctly for BOTH providers, and the returned URL reaches the model.
- The agent can only ever PROPOSE: no merge endpoint exists in the tool
  surface, and success output says a human must review and merge.
- Size-cap refusals happen BEFORE any write call reaches the provider.
- Token isolation: the token never appears in tool output or interceptor
  traces, even when the provider echoes it back in an error body.
"""

import base64
import json
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.a2a.protocol_bindings.a2a_server.gitops_tools import (
    build_gitops_tools,
)

TOKEN = "sekret-token-12345"


class FakeInterceptor:
    """Minimal stand-in for the tool call interceptor; records everything."""

    def __init__(self):
        self.calls = []
        self.results = []

    async def record_tool_call(self, tool_name, args, thread_id=None):
        self.calls.append({"tool_name": tool_name, "args": args, "thread_id": thread_id})
        return f"call-{len(self.calls)}"

    async def record_tool_result(self, tool_call_id, result, error=None):
        self.results.append({"id": tool_call_id, "result": result, "error": error})


class FakeGitHub:
    """In-memory GitHub REST v3 for the endpoints the provider uses."""

    def __init__(self, files=None, fail_all=False):
        # path -> content on the base branch
        self.files = dict(files or {})
        self.fail_all = fail_all
        self.branches_created = []
        self.commits = []  # (path, branch, decoded content, message)
        self.prs = []  # request bodies
        self.requests = []  # (method, path) log

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        if self.fail_all:
            # Echo the auth header back, like verbose upstream error pages do.
            return httpx.Response(
                500, text=f"upstream error; auth was {request.headers.get('authorization')}"
            )
        assert request.headers["authorization"] == f"Bearer {TOKEN}"

        if request.method == "GET" and path == "/repos/acme/manifests/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "base-sha-1"}})
        if request.method == "POST" and path == "/repos/acme/manifests/git/refs":
            body = json.loads(request.content)
            assert body["sha"] == "base-sha-1"
            self.branches_created.append(body["ref"])
            return httpx.Response(201, json={})
        if path.startswith("/repos/acme/manifests/contents/"):
            file_path = path.split("/contents/", 1)[1]
            if request.method == "GET":
                if file_path not in self.files:
                    return httpx.Response(404, json={"message": "Not Found"})
                content = base64.b64encode(self.files[file_path].encode()).decode()
                return httpx.Response(200, json={"content": content, "sha": f"sha-{file_path}"})
            if request.method == "PUT":
                body = json.loads(request.content)
                decoded = base64.b64decode(body["content"]).decode()
                if file_path in self.files:
                    assert body.get("sha") == f"sha-{file_path}"
                self.commits.append((file_path, body["branch"], decoded, body["message"]))
                return httpx.Response(201, json={})
        if request.method == "POST" and path == "/repos/acme/manifests/pulls":
            body = json.loads(request.content)
            self.prs.append(body)
            return httpx.Response(
                201, json={"html_url": "https://github.com/acme/manifests/pull/7"}
            )
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})

    @property
    def write_requests(self):
        return [(m, p) for m, p in self.requests if m in ("POST", "PUT", "DELETE", "PATCH")]


class FakeGitLab:
    """In-memory GitLab REST v4 for the endpoints the provider uses."""

    PROJECT = "/api/v4/projects/group%2Fmanifests"

    def __init__(self, files=None):
        self.files = dict(files or {})
        self.branches_created = []
        self.commits = []
        self.mrs = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        # raw_path keeps the %2F encodings that GitLab's routing relies on
        # (request.url.path percent-decodes them).
        path = request.url.raw_path.decode().split("?", 1)[0]
        assert request.headers["private-token"] == TOKEN
        if request.method == "GET" and path.startswith(f"{self.PROJECT}/repository/files/"):
            file_path = path.split("/repository/files/", 1)[1].replace("%2F", "/")
            if file_path not in self.files:
                return httpx.Response(404, json={"message": "404 File Not Found"})
            content = base64.b64encode(self.files[file_path].encode()).decode()
            return httpx.Response(200, json={"content": content})
        if request.method == "POST" and path == f"{self.PROJECT}/repository/branches":
            self.branches_created.append(dict(request.url.params))
            return httpx.Response(201, json={})
        if request.method == "POST" and path == f"{self.PROJECT}/repository/commits":
            self.commits.append(json.loads(request.content))
            return httpx.Response(201, json={})
        if request.method == "POST" and path == f"{self.PROJECT}/merge_requests":
            self.mrs.append(json.loads(request.content))
            return httpx.Response(
                201, json={"web_url": "https://gitlab.com/group/manifests/-/merge_requests/3"}
            )
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


def _configure(monkeypatch, provider="github", repo="acme/manifests", **extra):
    for var in ("KUBENTLY_GITOPS_PROVIDER", "KUBENTLY_GITOPS_REPO",
                "KUBENTLY_GITOPS_BASE_BRANCH", "KUBENTLY_GITOPS_TOKEN",
                "KUBENTLY_GITOPS_API_BASE", "KUBENTLY_GITOPS_MAX_FILES",
                "KUBENTLY_GITOPS_MAX_LINES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KUBENTLY_GITOPS_PROVIDER", provider)
    monkeypatch.setenv("KUBENTLY_GITOPS_REPO", repo)
    monkeypatch.setenv("KUBENTLY_GITOPS_TOKEN", TOKEN)
    for key, value in extra.items():
        monkeypatch.setenv(key, value)


def _build(monkeypatch, handler, **extra):
    interceptor = FakeInterceptor()
    tools = build_gitops_tools(
        interceptor, lambda: "thread-1", transport=httpx.MockTransport(handler)
    )
    by_name = {t.name: t for t in tools}
    return by_name, interceptor


DEPLOY_OLD = "\n".join(
    ["apiVersion: apps/v1", "kind: Deployment", "spec:", "  replicas: 1", "  x: y"]
) + "\n"
DEPLOY_NEW = DEPLOY_OLD.replace("replicas: 1", "replicas: 3")


# Registration gating


def test_no_tools_when_unconfigured(monkeypatch):
    for var in ("KUBENTLY_GITOPS_PROVIDER", "KUBENTLY_GITOPS_REPO", "KUBENTLY_GITOPS_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    assert build_gitops_tools(FakeInterceptor(), lambda: None) == []


def test_no_tools_when_partially_configured(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.delenv("KUBENTLY_GITOPS_TOKEN", raising=False)
    assert build_gitops_tools(FakeInterceptor(), lambda: None) == []


def test_tool_surface_has_no_merge_capability(monkeypatch):
    """Propose-only guardrail: reading and proposing are the ONLY tools."""
    _configure(monkeypatch)
    tools, _ = _build(monkeypatch, FakeGitHub().handler)
    assert sorted(tools) == ["get_manifest_file", "propose_fix_pr"]


# Read path (get_manifest_file)


@pytest.mark.asyncio
async def test_get_manifest_file_github(monkeypatch):
    _configure(monkeypatch)
    gh = FakeGitHub(files={"apps/api/deploy.yaml": DEPLOY_OLD})
    tools, interceptor = _build(monkeypatch, gh.handler)
    output = await tools["get_manifest_file"].ainvoke({"path": "apps/api/deploy.yaml"})
    assert output == DEPLOY_OLD
    assert interceptor.calls[0]["tool_name"] == "get_manifest_file"


@pytest.mark.asyncio
async def test_get_manifest_file_missing_is_explicit(monkeypatch):
    _configure(monkeypatch)
    tools, _ = _build(monkeypatch, FakeGitHub().handler)
    output = await tools["get_manifest_file"].ainvoke({"path": "nope.yaml"})
    assert "does not exist" in output
    assert "do not guess" in output


@pytest.mark.asyncio
async def test_get_manifest_file_rejects_traversal_without_network(monkeypatch):
    _configure(monkeypatch)
    gh = FakeGitHub()
    tools, _ = _build(monkeypatch, gh.handler)
    output = await tools["get_manifest_file"].ainvoke({"path": "../../etc/passwd"})
    assert "Error" in output
    assert gh.requests == []


@pytest.mark.asyncio
async def test_get_manifest_file_gitlab(monkeypatch):
    _configure(monkeypatch, provider="gitlab", repo="group/manifests")
    gl = FakeGitLab(files={"apps/api/deploy.yaml": DEPLOY_OLD})
    tools, _ = _build(monkeypatch, gl.handler)
    output = await tools["get_manifest_file"].ainvoke({"path": "apps/api/deploy.yaml"})
    assert output == DEPLOY_OLD


# Propose flow — GitHub


@pytest.mark.asyncio
async def test_propose_fix_pr_github_full_flow(monkeypatch):
    _configure(monkeypatch)
    gh = FakeGitHub(files={"apps/api/deploy.yaml": DEPLOY_OLD})
    tools, interceptor = _build(monkeypatch, gh.handler)

    output = await tools["propose_fix_pr"].ainvoke({
        "title": "Scale api to 3 replicas",
        "files": {"apps/api/deploy.yaml": DEPLOY_NEW},
        "evidence_summary": "Replica count dropped in revision 42 (see changes timeline).",
        "cluster_id": "prod-eks",
    })

    assert "https://github.com/acme/manifests/pull/7" in output
    assert "NOT merged" in output
    assert "human must review" in output

    # Branch off base, commit of the exact content, PR against main.
    assert len(gh.branches_created) == 1
    branch_ref = gh.branches_created[0]
    assert branch_ref.startswith("refs/heads/kubently/scale-api-to-3-replicas")
    (path, branch, content, message) = gh.commits[0]
    assert path == "apps/api/deploy.yaml"
    assert f"refs/heads/{branch}" == branch_ref
    assert content == DEPLOY_NEW
    assert message == "Scale api to 3 replicas"
    pr = gh.prs[0]
    assert pr["base"] == "main"
    assert pr["title"] == "Scale api to 3 replicas"
    # Machine-proposed marker + evidence land in the body the reviewer sees.
    assert "Machine-proposed" in pr["body"]
    assert "pending human review" in pr["body"]
    assert "revision 42" in pr["body"]
    assert "prod-eks" in pr["body"]
    assert "+  replicas: 3" in pr["body"]

    # Interceptor trace records the proposal, not the file contents.
    call = next(c for c in interceptor.calls if c["tool_name"] == "propose_fix_pr")
    assert call["args"]["files"] == ["apps/api/deploy.yaml"]


@pytest.mark.asyncio
async def test_propose_new_file_github(monkeypatch):
    _configure(monkeypatch)
    gh = FakeGitHub()  # file absent on base
    tools, _ = _build(monkeypatch, gh.handler)
    output = await tools["propose_fix_pr"].ainvoke({
        "title": "Add pdb",
        "files": {"apps/api/pdb.yaml": "kind: PodDisruptionBudget\n"},
        "evidence_summary": "evidence",
    })
    assert "pull/7" in output
    # New file: the PUT must not claim a prior sha.
    assert gh.commits[0][0] == "apps/api/pdb.yaml"


# Propose flow — GitLab


@pytest.mark.asyncio
async def test_propose_fix_pr_gitlab_full_flow(monkeypatch):
    _configure(monkeypatch, provider="gitlab", repo="group/manifests")
    gl = FakeGitLab(files={"apps/api/deploy.yaml": DEPLOY_OLD})
    tools, _ = _build(monkeypatch, gl.handler)

    output = await tools["propose_fix_pr"].ainvoke({
        "title": "Scale api to 3 replicas",
        "files": {"apps/api/deploy.yaml": DEPLOY_NEW, "apps/api/new.yaml": "kind: X\n"},
        "evidence_summary": "Replica count dropped in revision 42.",
    })

    assert "https://gitlab.com/group/manifests/-/merge_requests/3" in output
    assert gl.branches_created[0]["ref"] == "main"
    commit = gl.commits[0]
    actions = {a["file_path"]: a for a in commit["actions"]}
    assert actions["apps/api/deploy.yaml"]["action"] == "update"
    assert actions["apps/api/new.yaml"]["action"] == "create"
    mr = gl.mrs[0]
    assert mr["target_branch"] == "main"
    assert "Machine-proposed" in mr["description"]


# Size caps — refusal must precede any write


@pytest.mark.asyncio
async def test_file_cap_refused_before_any_provider_call(monkeypatch):
    _configure(monkeypatch, KUBENTLY_GITOPS_MAX_FILES="2")
    gh = FakeGitHub()
    tools, _ = _build(monkeypatch, gh.handler)
    output = await tools["propose_fix_pr"].ainvoke({
        "title": "Big change",
        "files": {f"f{i}.yaml": "x: 1\n" for i in range(3)},
        "evidence_summary": "evidence",
    })
    assert output.startswith("REFUSED")
    assert gh.requests == []  # not even reads


@pytest.mark.asyncio
async def test_line_cap_refused_before_any_write(monkeypatch):
    _configure(monkeypatch, KUBENTLY_GITOPS_MAX_LINES="10")
    gh = FakeGitHub(files={"apps/api/deploy.yaml": DEPLOY_OLD})
    tools, _ = _build(monkeypatch, gh.handler)
    big = "\n".join(f"line: {i}" for i in range(50)) + "\n"
    output = await tools["propose_fix_pr"].ainvoke({
        "title": "Rewrite manifest",
        "files": {"apps/api/deploy.yaml": big},
        "evidence_summary": "evidence",
    })
    assert output.startswith("REFUSED")
    assert "cap of 10" in output
    assert gh.write_requests == []  # reads only; no branch/commit/PR
    assert gh.branches_created == [] and gh.commits == [] and gh.prs == []


@pytest.mark.asyncio
async def test_identical_content_is_rejected_without_writes(monkeypatch):
    _configure(monkeypatch)
    gh = FakeGitHub(files={"apps/api/deploy.yaml": DEPLOY_OLD})
    tools, _ = _build(monkeypatch, gh.handler)
    output = await tools["propose_fix_pr"].ainvoke({
        "title": "No-op",
        "files": {"apps/api/deploy.yaml": DEPLOY_OLD},
        "evidence_summary": "evidence",
    })
    assert "identical" in output
    assert gh.write_requests == []


@pytest.mark.asyncio
async def test_missing_evidence_summary_is_refused(monkeypatch):
    _configure(monkeypatch)
    gh = FakeGitHub()
    tools, _ = _build(monkeypatch, gh.handler)
    output = await tools["propose_fix_pr"].ainvoke({
        "title": "Fix",
        "files": {"a.yaml": "x: 1\n"},
        "evidence_summary": "   ",
    })
    assert output.startswith("REFUSED")
    assert gh.requests == []


# Token isolation


@pytest.mark.asyncio
async def test_token_never_reaches_output_or_trace_even_on_provider_error(monkeypatch):
    _configure(monkeypatch)
    gh = FakeGitHub(fail_all=True)  # 500s that echo the Authorization header
    tools, interceptor = _build(monkeypatch, gh.handler)

    read_out = await tools["get_manifest_file"].ainvoke({"path": "a.yaml"})
    propose_out = await tools["propose_fix_pr"].ainvoke({
        "title": "Fix",
        "files": {"a.yaml": "x: 1\n"},
        "evidence_summary": "evidence",
    })

    blob = json.dumps([read_out, propose_out, interceptor.calls, interceptor.results])
    assert TOKEN not in blob
    assert "***" in read_out  # redaction visibly applied
    assert "Error" in propose_out


@pytest.mark.asyncio
async def test_token_not_in_successful_outputs_or_trace(monkeypatch):
    _configure(monkeypatch)
    gh = FakeGitHub(files={"apps/api/deploy.yaml": DEPLOY_OLD})
    tools, interceptor = _build(monkeypatch, gh.handler)
    read_out = await tools["get_manifest_file"].ainvoke({"path": "apps/api/deploy.yaml"})
    propose_out = await tools["propose_fix_pr"].ainvoke({
        "title": "Scale api",
        "files": {"apps/api/deploy.yaml": DEPLOY_NEW},
        "evidence_summary": "evidence",
    })
    blob = json.dumps([read_out, propose_out, interceptor.calls, interceptor.results])
    assert TOKEN not in blob
