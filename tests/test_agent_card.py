#!/usr/bin/env python3
"""The agent card is a public contract — guard what it advertises.

Two things can silently rot here: a new tool family ships without a matching
skill (so remote agents never route those questions to Kubently), and the card
publishes a bind address that no client can dial.
"""

import ast
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.a2a.skills import SKILLS, advertised_tools, build_skills

A2A_SERVER_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "kubently"
    / "modules"
    / "a2a"
    / "protocol_bindings"
    / "a2a_server"
)


def registered_tool_names() -> set[str]:
    """Every @tool-decorated function in the agent's toolset modules."""
    names = set()
    for path in sorted(A2A_SERVER_DIR.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if getattr(target, "id", None) == "tool" or getattr(target, "attr", None) == "tool":
                    names.add(node.name)
    return names


def test_every_registered_tool_is_advertised_by_a_skill():
    """A new tool family cannot ship without updating the advertisement."""
    registered = registered_tool_names()
    assert registered, "found no @tool functions - the AST scan is broken, not the card"
    missing = registered - advertised_tools()
    assert not missing, (
        f"tools registered but not advertised on the agent card: {sorted(missing)}. "
        "Add them to a skill in kubently/modules/a2a/skills.py."
    )


def test_skills_do_not_claim_tools_that_no_longer_exist():
    stale = advertised_tools() - registered_tool_names()
    assert not stale, f"agent card advertises tools that are not registered: {sorted(stale)}"


def test_skill_ids_are_unique_and_populated():
    ids = [s["id"] for s in SKILLS]
    assert len(ids) == len(set(ids))
    for skill in SKILLS:
        assert skill["name"] and skill["description"] and skill["tags"]


# --- skills follow the same gating that registers the tools -----------------

GATED = {
    "prometheus-metrics": ("PROMETHEUS_URL", "http://prometheus:9090"),
    "loki-log-search": ("LOKI_URL", "http://loki:3100"),
}


@pytest.mark.parametrize("skill_id,env", sorted(GATED.items()))
def test_optional_skill_tracks_its_toolset(monkeypatch, skill_id, env):
    name, value = env
    monkeypatch.delenv(name, raising=False)
    assert skill_id not in [s["id"] for s in build_skills()]
    monkeypatch.setenv(name, value)
    assert skill_id in [s["id"] for s in build_skills()]


def test_cloud_skill_follows_the_cloud_kill_switch(monkeypatch):
    monkeypatch.delenv("KUBENTLY_CLOUD_TOOLS", raising=False)
    assert "cloud-telemetry" in [s["id"] for s in build_skills()]
    monkeypatch.setenv("KUBENTLY_CLOUD_TOOLS", "off")
    assert "cloud-telemetry" not in [s["id"] for s in build_skills()]


def test_incident_skill_needs_redis_like_the_tool_does(monkeypatch):
    monkeypatch.delenv("KUBENTLY_INCIDENT_HISTORY", raising=False)
    assert "incident-history" in [s["id"] for s in build_skills(has_redis=True)]
    assert "incident-history" not in [s["id"] for s in build_skills(has_redis=False)]


def test_core_kubectl_skills_are_always_present():
    ids = [s["id"] for s in build_skills()]
    for always_on in ("kubernetes-debug", "fleet-query", "pod-log-search", "change-correlation"):
        assert always_on in ids


# --- the advertised URL must be dialable ------------------------------------


def test_card_url_never_advertises_a_bind_address():
    """0.0.0.0 is where the server listens, not an address a client can reach."""
    pytest.importorskip("a2a")
    from kubently.modules.a2a import A2AModule

    url = A2AModule(host="0.0.0.0", port=8080).external_url
    assert "0.0.0.0" not in url
    assert url.endswith("/a2a/")


def test_configured_external_url_is_used_verbatim():
    pytest.importorskip("a2a")
    from kubently.modules.a2a import A2AModule

    configured = "https://kubently.example.com/a2a/"
    assert A2AModule(host="0.0.0.0", port=8080, external_url=configured).external_url == configured
