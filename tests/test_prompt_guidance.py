#!/usr/bin/env python3
"""Guards on shipped prompt content.

Prompts are product behaviour, but nothing compiles them, so regressions here are
invisible until an agent quietly gives a wrong answer. These two guards cover the
failures that actually happened:

1. `status.phase!=Running` guidance, which hides CrashLoopBackOff pods.
2. The root and chart copies of the system prompt drifting apart.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

REPO = os.path.join(os.path.dirname(__file__), "..")
ROOT_PROMPT = os.path.join(REPO, "prompts", "system.prompt.yaml")
CHART_PROMPT = os.path.join(REPO, "deployment", "helm", "kubently", "prompts", "system.prompt.yaml")
ROOT_DIGEST = os.path.join(REPO, "prompts", "fleet_report.prompt.yaml")
CHART_DIGEST = os.path.join(
    REPO, "deployment", "helm", "kubently", "prompts", "fleet_report.prompt.yaml"
)
AGENT = os.path.join(
    REPO, "kubently", "modules", "a2a", "protocol_bindings", "a2a_server", "agent.py"
)

# The trap: a pod stuck in CrashLoopBackOff reports status.phase=Running, so any
# guidance steering the agent to select on "phase is not Running" hides the single
# most common Kubernetes failure. Confirmed on a live cluster: a deployment with 4
# restarts returned zero rows, and the agent duly reported the cluster healthy.
BAD_FILTER = "status.phase!=Running"


WARNING_WORDS = ("never", "not use", "do not", "critical", "misses", "hides", "instead")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _unwarned_mentions(path):
    """Lines mentioning the bad filter with no warning nearby.

    Warning *about* the trap is the point; recommending it is the bug. The
    surrounding lines count as context because the warnings wrap across lines.
    """
    lines = _read(path).splitlines()
    bad = []
    for i, line in enumerate(lines):
        if BAD_FILTER not in line:
            continue
        window = " ".join(lines[max(0, i - 3) : i + 4]).lower()
        if not any(w in window for w in WARNING_WORDS):
            bad.append(f"{os.path.basename(path)}:{i + 1}: {line.strip()}")
    return bad


def test_no_phase_not_running_guidance_in_prompts():
    unwarned = []
    for path in (ROOT_PROMPT, CHART_PROMPT, ROOT_DIGEST, CHART_DIGEST):
        unwarned += _unwarned_mentions(path)
    assert not unwarned, "prompt recommends the CrashLoopBackOff-blind filter:\n" + "\n".join(
        unwarned
    )


def test_no_phase_not_running_guidance_in_agent_tools():
    unwarned = _unwarned_mentions(AGENT)
    assert not unwarned, "agent tool docstring recommends the filter:\n" + "\n".join(unwarned)


def test_prompt_copies_do_not_drift():
    """The chart copy is mounted over the image's baked-in copy, so the chart is
    what runs in a cluster while the root copy is what runs in local dev. They
    drifted by 49 lines once — long enough for local dev to be told about a
    `todo_write` tool that had been renamed."""
    for root, chart in ((ROOT_PROMPT, CHART_PROMPT), (ROOT_DIGEST, CHART_DIGEST)):
        assert _read(root) == _read(chart), (
            f"{os.path.basename(root)} differs between prompts/ and "
            f"deployment/helm/kubently/prompts/ — the chart copy is what runs in-cluster; "
            f"copy it to the root so local dev matches"
        )
