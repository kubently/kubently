#!/usr/bin/env python3
"""
Validation tests for the log-search and Loki API request models.

These models are the API-side gate for values that end up as kubectl argv
entries (in --flag=value form) or Loki query parameters on the executor.
"""

import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.api.models import LogSearchRequest, LokiQueryRequest


def make_search(**overrides):
    base = {
        "cluster_id": "kind",
        "namespace": "payments",
        "selector": "app=api",
        "query": "error",
    }
    base.update(overrides)
    return LogSearchRequest(**base)


# LogSearchRequest


def test_minimal_selector_request_valid_with_defaults():
    req = make_search()
    assert req.use_regex is False
    assert req.tail_lines == 2000
    assert req.previous is False
    assert req.timeout_seconds == 60


def test_pod_name_mode_valid():
    req = make_search(selector=None, pod_name="api-7f9b5")
    assert req.pod_name == "api-7f9b5"


def test_selector_and_pod_name_mutually_exclusive():
    with pytest.raises(ValidationError, match="exactly one"):
        make_search(pod_name="api-1")
    with pytest.raises(ValidationError, match="exactly one"):
        make_search(selector=None)


@pytest.mark.parametrize("field", ["namespace", "pod_name", "container"])
def test_names_must_be_dns_safe(field):
    """A leading dash could otherwise be parsed as a kubectl flag."""
    overrides = {field: "--previous"}
    if field == "pod_name":
        overrides["selector"] = None
    with pytest.raises(ValidationError, match="Invalid Kubernetes name"):
        make_search(**overrides)


@pytest.mark.parametrize("since", ["1h", "30m", "45s", "1h30m"])
def test_valid_since_durations(since):
    assert make_search(since=since).since == since


@pytest.mark.parametrize("since", ["1 hour", "-1h", "1d", "90"])
def test_invalid_since_durations(since):
    with pytest.raises(ValidationError, match="Invalid duration"):
        make_search(since=since)


def test_since_time_accepts_rfc3339_rejects_garbage():
    assert make_search(since_time="2026-08-17T00:00:00Z").since_time
    with pytest.raises(ValidationError, match="Invalid timestamp"):
        make_search(since_time="yesterday")


def test_query_and_tail_bounds():
    with pytest.raises(ValidationError):
        make_search(query="")
    with pytest.raises(ValidationError):
        make_search(query="x" * 600)
    with pytest.raises(ValidationError):
        make_search(tail_lines=100000)
    with pytest.raises(ValidationError):
        make_search(context_lines=50)


# LokiQueryRequest


def make_loki(**overrides):
    base = {"cluster_id": "kind", "query": '{app="api"} |= "error"'}
    base.update(overrides)
    return LokiQueryRequest(**base)


def test_minimal_loki_request_defaults():
    req = make_loki()
    assert req.limit == 100
    assert req.direction.value == "backward"
    assert req.start is None and req.end is None


def test_loki_timestamps_validated():
    assert make_loki(start="1700000000", end="2026-08-17T00:00:00Z")
    with pytest.raises(ValidationError, match="Invalid timestamp"):
        make_loki(start="last tuesday")


def test_loki_limit_and_direction_bounds():
    with pytest.raises(ValidationError):
        make_loki(limit=0)
    with pytest.raises(ValidationError):
        make_loki(limit=100000)
    with pytest.raises(ValidationError):
        make_loki(direction="sideways")


def test_loki_query_size_bounds():
    with pytest.raises(ValidationError):
        make_loki(query="")
    with pytest.raises(ValidationError):
        make_loki(query="x" * 3000)
