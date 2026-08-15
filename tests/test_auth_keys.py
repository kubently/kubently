#!/usr/bin/env python3
"""API_KEYS parsing must accept commas AND newlines as separators.

The kubently-api-keys secret docs show newline-separated keys, but the env var
is consumed raw; newline-joined keys must not fuse into one garbage key.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.config.provider import EnvConfigProvider  # noqa: E402
from kubently.modules.auth.auth import AuthModule  # noqa: E402


def test_load_api_keys_comma_separated(monkeypatch):
    monkeypatch.setenv("API_KEYS", "key1,svc:key2")
    assert AuthModule(None).api_keys == {"key1": None, "key2": "svc"}


def test_load_api_keys_newline_separated(monkeypatch):
    monkeypatch.setenv("API_KEYS", "key1\nsvc:key2\n")
    assert AuthModule(None).api_keys == {"key1": None, "key2": "svc"}


def test_load_api_keys_mixed_separators(monkeypatch):
    monkeypatch.setenv("API_KEYS", "key1,key2\nkey3")
    assert set(AuthModule(None).api_keys) == {"key1", "key2", "key3"}


def test_extract_first_api_key_newline_separated():
    assert AuthModule.extract_first_api_key("key1\nkey2") == "key1"


def test_provider_auth_config_newline_separated(monkeypatch):
    monkeypatch.setenv("API_KEYS", "key1\nsvc:key2")
    cfg = EnvConfigProvider().get_auth_config()
    assert cfg.api_keys == ["key1", "svc:key2"]
