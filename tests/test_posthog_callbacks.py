#!/usr/bin/env python3
"""PostHog telemetry must not allocate anything on the degraded path.

`Posthog(...)` starts a background flush thread and a connection pool. It used
to be constructed *before* the `posthog.ai.langchain.CallbackHandler` import, so
an SDK that ships `posthog` but not the LangChain callback (older/partial
install) left an idle client running that nothing ever used or shut down.
Issue #64.
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from kubently.modules.a2a.protocol_bindings.a2a_server import agent as agent_mod  # noqa: E402


def _fake_posthog(monkeypatch, constructed, *, with_callback):
    """Install a stub `posthog` package, optionally missing the LangChain part."""

    class FakePosthog:
        def __init__(self, key, host=None):
            constructed.append((key, host))

    posthog = types.ModuleType("posthog")
    posthog.Posthog = FakePosthog
    monkeypatch.setitem(sys.modules, "posthog", posthog)

    if with_callback:
        # A real package needs __path__ for submodule imports to resolve.
        posthog.__path__ = []
        langchain = types.ModuleType("posthog.ai.langchain")
        langchain.CallbackHandler = lambda client=None: ("handler", client)
        ai = types.ModuleType("posthog.ai")
        ai.__path__ = []
        ai.langchain = langchain
        posthog.ai = ai
        monkeypatch.setitem(sys.modules, "posthog.ai", ai)
        monkeypatch.setitem(sys.modules, "posthog.ai.langchain", langchain)

    monkeypatch.setattr(agent_mod, "_POSTHOG_CLIENT", None)
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")


def test_no_client_constructed_when_callback_handler_is_missing(monkeypatch):
    constructed = []
    _fake_posthog(monkeypatch, constructed, with_callback=False)

    assert agent_mod._posthog_llm_callbacks() == []
    assert constructed == [], "a PostHog client was leaked on the degraded path"
    assert agent_mod._POSTHOG_CLIENT is None


def test_client_constructed_once_when_sdk_is_complete(monkeypatch):
    constructed = []
    _fake_posthog(monkeypatch, constructed, with_callback=True)

    first = agent_mod._posthog_llm_callbacks()
    second = agent_mod._posthog_llm_callbacks()

    assert len(first) == 1 and len(second) == 1
    assert len(constructed) == 1, "the client must stay a singleton"


def test_no_client_constructed_without_an_api_key(monkeypatch):
    constructed = []
    _fake_posthog(monkeypatch, constructed, with_callback=True)
    monkeypatch.delenv("POSTHOG_API_KEY")

    assert agent_mod._posthog_llm_callbacks() == []
    assert constructed == []
