#!/usr/bin/env python3
"""Conversation threads must be private to the authenticated caller.

The A2A contextId is client-supplied and is used directly as the LangGraph
checkpointer's thread namespace. Un-namespaced, caller A could resume caller B's
conversation — replaying B's questions, kubectl output and cluster internals —
just by reusing B's contextId. _namespaced_thread_id binds the thread to the
caller's validated key.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from kubently.modules.auth.context import current_api_key  # noqa: E402


def _load_namespacer():
    """Import just the helper without pulling in the heavy a2a/langchain stack."""
    path = (Path(__file__).parent.parent / "kubently/modules/a2a/protocol_bindings"
            / "a2a_server/agent.py")
    src = path.read_text()
    start = src.index("def _namespaced_thread_id")
    end = src.index("def _posthog_llm_callbacks")
    ns: dict = {}
    exec("import hashlib\n" + src[start:end], ns)
    return ns["_namespaced_thread_id"]


_namespaced_thread_id = _load_namespacer()


def test_same_context_id_differs_across_callers():
    """The core isolation property: identical contextId, different callers,
    different checkpointer namespaces."""
    token_a = current_api_key.set("tenant-a-key")
    a = _namespaced_thread_id("shared-context-123")
    current_api_key.reset(token_a)

    token_b = current_api_key.set("tenant-b-key")
    b = _namespaced_thread_id("shared-context-123")
    current_api_key.reset(token_b)

    assert a != b, "two tenants reusing a contextId must not share a thread"
    assert "shared-context-123" in a and "shared-context-123" in b


def test_stable_across_turns_for_same_caller():
    """Multi-turn memory only works if the namespace is deterministic."""
    token = current_api_key.set("tenant-a-key")
    first = _namespaced_thread_id("ctx-1")
    second = _namespaced_thread_id("ctx-1")
    current_api_key.reset(token)
    assert first == second


def test_key_not_leaked_into_thread_id():
    """The namespace is a hash prefix — never the raw credential (thread ids
    reach logs, traces and Redis keys)."""
    token = current_api_key.set("super-secret-key")
    tid = _namespaced_thread_id("ctx-1")
    current_api_key.reset(token)
    assert "super-secret-key" not in tid


def test_unauthenticated_passthrough_unchanged():
    """Direct/local invocation (no caller) keeps existing behaviour."""
    assert current_api_key.get() is None
    assert _namespaced_thread_id("ctx-1") == "ctx-1"
    assert _namespaced_thread_id(None) is None
