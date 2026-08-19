#!/usr/bin/env python3
"""Config knobs must not exist unless something honours them.

REQUIRE_AUTH, API_DEBUG and CORS_ORIGINS were parsed and then dropped on the
floor (issue #86). Authentication is unconditional -- every route depends on
verify_api_key / verify_dual_auth / verify_executor_auth -- so REQUIRE_AUTH is
enforce-only: setting it to false must fail loudly, never disable auth. The
inert APIConfig (API_DEBUG / CORS_ORIGINS, with no CORSMiddleware anywhere) is
gone; re-adding a CORS_ORIGINS default of "*" would newly open the API to any
browser origin.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.config.provider import AuthConfig, EnvConfigProvider


def test_require_auth_true_is_accepted(monkeypatch):
    monkeypatch.setenv("API_KEYS", "key1")
    monkeypatch.setenv("REQUIRE_AUTH", "true")
    assert EnvConfigProvider().get_auth_config().api_keys_enabled is True


def test_require_auth_false_refuses_to_start(monkeypatch):
    """Must raise, not silently keep enforcing and not disable auth."""
    monkeypatch.setenv("API_KEYS", "key1")
    monkeypatch.setenv("REQUIRE_AUTH", "false")
    with pytest.raises(ValueError, match="REQUIRE_AUTH cannot be disabled"):
        EnvConfigProvider().get_auth_config()


def test_auth_config_has_no_unhonoured_require_auth_field():
    assert "require_auth" not in AuthConfig.__dataclass_fields__


def test_no_inert_api_config(monkeypatch):
    """API_DEBUG / CORS_ORIGINS must not be parsed while nothing consumes them."""
    monkeypatch.setenv("CORS_ORIGINS", "https://evil.example")
    assert not hasattr(EnvConfigProvider(), "get_api_config")
