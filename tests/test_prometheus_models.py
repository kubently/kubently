#!/usr/bin/env python3
"""
Validation tests for PrometheusQueryRequest.

The API is the first validation layer for metric queries (the executor's path
allowlist is the second): malformed range parameters must be rejected here so
they never reach an executor, and only the two known query types exist.
"""

import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.api.models import PrometheusQueryRequest, PrometheusQueryType


def test_minimal_instant_query():
    req = PrometheusQueryRequest(cluster_id="prod", query="up")
    assert req.query_type == PrometheusQueryType.INSTANT
    assert req.timeout_seconds == 30


def test_range_query_requires_start_end_step():
    with pytest.raises(ValidationError, match="start, end, step"):
        PrometheusQueryRequest(cluster_id="prod", query="up", query_type="range")

    # Partially specified is still an error
    with pytest.raises(ValidationError, match="end, step"):
        PrometheusQueryRequest(
            cluster_id="prod", query="up", query_type="range", start="1700000000"
        )


def test_valid_range_query():
    req = PrometheusQueryRequest(
        cluster_id="prod",
        query="rate(kube_pod_container_status_restarts_total[5m])",
        query_type="range",
        start="2026-08-16T10:00:00Z",
        end="1700003600",
        step="60s",
    )
    assert req.query_type == PrometheusQueryType.RANGE


def test_unknown_query_type_rejected():
    with pytest.raises(ValidationError):
        PrometheusQueryRequest(cluster_id="prod", query="up", query_type="admin")


def test_empty_and_oversized_query_rejected():
    with pytest.raises(ValidationError):
        PrometheusQueryRequest(cluster_id="prod", query="")
    with pytest.raises(ValidationError):
        PrometheusQueryRequest(cluster_id="prod", query="x" * 4001)


@pytest.mark.parametrize("bad_time", ["yesterday", "16/08/2026", "now()", "1700000000; rm"])
def test_malformed_timestamps_rejected(bad_time):
    with pytest.raises(ValidationError, match="Invalid timestamp"):
        PrometheusQueryRequest(cluster_id="prod", query="up", time=bad_time)


@pytest.mark.parametrize("good_time", ["1700000000", "1700000000.5", "2026-08-16T10:00:00Z",
                                       "2026-08-16T10:00:00+02:00", "2026-08-16T10:00:00"])
def test_valid_timestamps_accepted(good_time):
    req = PrometheusQueryRequest(cluster_id="prod", query="up", time=good_time)
    assert req.time == good_time


@pytest.mark.parametrize("bad_step", ["fast", "60ss", "-30s", "1 m"])
def test_malformed_step_rejected(bad_step):
    with pytest.raises(ValidationError, match="duration"):
        PrometheusQueryRequest(
            cluster_id="prod", query="up", query_type="range",
            start="0", end="1", step=bad_step,
        )


@pytest.mark.parametrize("good_step", ["30s", "5m", "1h", "15", "500ms"])
def test_valid_step_accepted(good_step):
    req = PrometheusQueryRequest(
        cluster_id="prod", query="up", query_type="range",
        start="0", end="1", step=good_step,
    )
    assert req.step == good_step


def test_timeout_bounds():
    with pytest.raises(ValidationError):
        PrometheusQueryRequest(cluster_id="prod", query="up", timeout_seconds=0)
    with pytest.raises(ValidationError):
        PrometheusQueryRequest(cluster_id="prod", query="up", timeout_seconds=61)
