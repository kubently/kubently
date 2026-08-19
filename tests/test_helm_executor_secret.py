#!/usr/bin/env python3
"""The API pod's sync-executor-tokens init container must honour executor.existingSecret.

Issue #85: a co-located release (api.enabled + executor.enabled) that sets
executor.existingSecret never renders <release>-executor-token, so the init
container's hardcoded secretKeyRef dangled and the API pod stuck in
CreateContainerConfigError.
"""

import os
import shutil
import subprocess
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

CHART = os.path.join(os.path.dirname(__file__), "..", "deployment", "helm", "kubently")

BASE_ARGS = [
    "--set", "api.env.LLM_PROVIDER=anthropic-claude",
    "--set", "executor.enabled=true",
    "--set", "executor.clusterId=local",
    "--set", "executor.apiUrl=http://kubently-api:8080",
]

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None, reason="helm binary not installed"
)


def _render(*extra_args):
    out = subprocess.run(
        ["helm", "template", "kubently", CHART, "-n", "kubently", *BASE_ARGS, *extra_args],
        capture_output=True, text=True, check=True,
    ).stdout
    return [doc for doc in yaml.safe_load_all(out) if doc]


def _executor_token_ref(docs):
    """secretKeyRef used by the API init container's EXECUTOR_TOKEN env var."""
    for doc in docs:
        if doc.get("kind") != "Deployment" or doc["metadata"]["name"] != "kubently-api":
            continue
        for container in doc["spec"]["template"]["spec"].get("initContainers", []):
            for env in container.get("env", []):
                if env["name"] == "EXECUTOR_TOKEN":
                    return env["valueFrom"]["secretKeyRef"]
    raise AssertionError("no EXECUTOR_TOKEN env var on any API init container")


def test_generated_secret_is_used_by_default():
    docs = _render("--set", "executor.token=deadbeef")
    assert _executor_token_ref(docs) == {"name": "kubently-executor-token", "key": "token"}


def test_existing_secret_is_used_when_set():
    docs = _render(
        "--set", "executor.existingSecret=my-custom-exec-secret",
        "--set", "executor.existingSecretKey=mytoken",
    )
    assert _executor_token_ref(docs) == {"name": "my-custom-exec-secret", "key": "mytoken"}


def test_referenced_secret_actually_exists_in_the_release():
    """Guards the real failure mode: a secretKeyRef nothing renders."""
    docs = _render("--set", "executor.token=deadbeef")
    ref = _executor_token_ref(docs)
    rendered = {d["metadata"]["name"] for d in docs if d.get("kind") == "Secret"}
    assert ref["name"] in rendered
