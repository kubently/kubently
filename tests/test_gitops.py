#!/usr/bin/env python3
"""
Tests for the GitOps PR remediation core logic (Track P8).

Contracts under guard:
- Default OFF: tools/guidance exist ONLY when provider + repo + token are all
  configured — partial configuration stays off, and the prompt never mentions
  tools the model cannot call (same availability pattern as Loki/Prometheus).
- Size caps refuse oversized proposals with an actionable message.
- PR bodies always carry the machine-proposed / pending-human-review marker.
- The token can always be scrubbed from text bound for model context.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.a2a.protocol_bindings.a2a_server.gitops import (
    GITOPS_PROMPT_SECTION,
    MACHINE_PROPOSED_MARKER,
    build_pr_body,
    check_size_caps,
    count_changed_lines,
    gitops_guidance,
    gitops_tools_enabled,
    load_config,
    make_branch_name,
    missing_config_pieces,
    redact_secret,
    validate_repo_path,
)
from kubently.modules.config import get_prompt

REPO = os.path.join(os.path.dirname(__file__), "..")
ROOT_PROMPT = os.path.join(REPO, "prompts", "system.prompt.yaml")
CHART_PROMPT = os.path.join(
    REPO, "deployment", "helm", "kubently", "prompts", "system.prompt.yaml"
)

GITOPS_ENV = (
    "KUBENTLY_GITOPS_PROVIDER",
    "KUBENTLY_GITOPS_REPO",
    "KUBENTLY_GITOPS_BASE_BRANCH",
    "KUBENTLY_GITOPS_TOKEN",
    "KUBENTLY_GITOPS_API_BASE",
    "KUBENTLY_GITOPS_MAX_FILES",
    "KUBENTLY_GITOPS_MAX_LINES",
)


def _clear_env(monkeypatch):
    for var in GITOPS_ENV:
        monkeypatch.delenv(var, raising=False)


def _configure(monkeypatch, provider="github", repo="acme/manifests", token="tok-123"):
    _clear_env(monkeypatch)
    monkeypatch.setenv("KUBENTLY_GITOPS_PROVIDER", provider)
    monkeypatch.setenv("KUBENTLY_GITOPS_REPO", repo)
    monkeypatch.setenv("KUBENTLY_GITOPS_TOKEN", token)


# Availability gating — default OFF, all three settings required


def test_disabled_when_nothing_configured(monkeypatch):
    _clear_env(monkeypatch)
    assert gitops_tools_enabled() is False
    assert load_config() is None
    assert gitops_guidance() == ""


def test_partial_configuration_stays_off(monkeypatch):
    # repo without token
    _clear_env(monkeypatch)
    monkeypatch.setenv("KUBENTLY_GITOPS_PROVIDER", "github")
    monkeypatch.setenv("KUBENTLY_GITOPS_REPO", "acme/manifests")
    assert gitops_tools_enabled() is False
    assert "KUBENTLY_GITOPS_TOKEN" in missing_config_pieces()

    # token without repo
    _clear_env(monkeypatch)
    monkeypatch.setenv("KUBENTLY_GITOPS_PROVIDER", "gitlab")
    monkeypatch.setenv("KUBENTLY_GITOPS_TOKEN", "tok")
    assert gitops_tools_enabled() is False
    assert "KUBENTLY_GITOPS_REPO" in missing_config_pieces()


def test_unknown_provider_stays_off(monkeypatch):
    _configure(monkeypatch, provider="bitbucket")
    assert gitops_tools_enabled() is False
    assert any("PROVIDER" in piece for piece in missing_config_pieces())


def test_enabled_with_full_config_and_defaults(monkeypatch):
    _configure(monkeypatch)
    assert gitops_tools_enabled() is True
    config = load_config()
    assert config.provider == "github"
    assert config.repo == "acme/manifests"
    assert config.base_branch == "main"
    assert config.max_files == 5
    assert config.max_lines == 200
    assert config.api_base is None
    assert gitops_guidance() == GITOPS_PROMPT_SECTION


def test_config_overrides(monkeypatch):
    _configure(monkeypatch, provider="gitlab", repo="group/manifests")
    monkeypatch.setenv("KUBENTLY_GITOPS_BASE_BRANCH", "production")
    monkeypatch.setenv("KUBENTLY_GITOPS_API_BASE", "https://gitlab.example.com/api/v4")
    monkeypatch.setenv("KUBENTLY_GITOPS_MAX_FILES", "2")
    monkeypatch.setenv("KUBENTLY_GITOPS_MAX_LINES", "40")
    config = load_config()
    assert config.base_branch == "production"
    assert config.api_base == "https://gitlab.example.com/api/v4"
    assert (config.max_files, config.max_lines) == (2, 40)


def test_bad_cap_values_fall_back_to_defaults(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setenv("KUBENTLY_GITOPS_MAX_FILES", "not-a-number")
    monkeypatch.setenv("KUBENTLY_GITOPS_MAX_LINES", "-3")
    config = load_config()
    assert (config.max_files, config.max_lines) == (5, 200)


# Changed-line counting — the cap measures the CHANGE, not the file size


def test_new_file_counts_all_lines():
    assert count_changed_lines(None, "a\nb\nc") == 3


def test_single_field_edit_in_large_manifest_counts_two_lines():
    old = "\n".join(f"line-{i}" for i in range(500))
    new = old.replace("line-250", "line-250-changed")
    assert count_changed_lines(old, new) == 2  # one removed + one added


def test_identical_content_counts_zero():
    assert count_changed_lines("same\ncontent", "same\ncontent") == 0


# Size caps


def test_under_cap_passes():
    assert check_size_caps({"a.yaml": 4, "b.yaml": 6}, max_files=5, max_lines=200) is None


def test_file_cap_refusal():
    changes = {f"f{i}.yaml": 1 for i in range(6)}
    refusal = check_size_caps(changes, max_files=5, max_lines=200)
    assert refusal is not None and refusal.startswith("REFUSED")
    assert "6 files" in refusal and "cap of 5" in refusal


def test_line_cap_refusal_names_the_offenders():
    refusal = check_size_caps({"a.yaml": 150, "b.yaml": 80}, max_files=5, max_lines=200)
    assert refusal is not None and refusal.startswith("REFUSED")
    assert "230 lines" in refusal and "a.yaml: 150" in refusal


# Repo path hygiene


def test_valid_paths_pass():
    for path in ("deployment.yaml", "apps/api/deploy.yaml", "a-b_c.1/x@2.yaml"):
        assert validate_repo_path(path) is None, path


def test_bad_paths_rejected():
    for path in ("", "  ", "/etc/passwd", "../secrets.yaml", "a/../b.yaml",
                 "a//b.yaml", "a/./b.yaml", "a b.yaml", "a;rm.yaml"):
        assert validate_repo_path(path) is not None, path


# Branch names


def test_branch_names_are_slugged_prefixed_and_unique():
    one = make_branch_name("Raise api memory limit to 512Mi")
    two = make_branch_name("Raise api memory limit to 512Mi")
    assert one.startswith("kubently/raise-api-memory-limit")
    assert one != two  # uuid suffix
    assert make_branch_name("!!!").startswith("kubently/fix-")


# PR body — the human-review guardrail lives here


def test_pr_body_carries_marker_evidence_and_diff():
    files = {"apps/api/deploy.yaml": ("replicas: 1\n", "replicas: 3\n")}
    body = build_pr_body("OOMKills began 90s after revision 42.", files, "prod-eks")
    assert MACHINE_PROPOSED_MARKER in body
    assert "Machine-proposed" in body and "pending human review" in body
    assert "never merges" in body
    assert "OOMKills began 90s after revision 42." in body
    assert "`prod-eks`" in body
    assert "apps/api/deploy.yaml" in body
    assert "-replicas: 1" in body and "+replicas: 3" in body


def test_pr_body_marks_new_files():
    body = build_pr_body("evidence", {"new.yaml": (None, "kind: ConfigMap\n")})
    assert "(new file)" in body


# Token redaction


def test_redact_secret_strips_token_everywhere():
    leaky = "HTTP 401: bad credentials for Bearer tok-123 (tok-123)"
    assert "tok-123" not in redact_secret(leaky, "tok-123")
    assert redact_secret("", "tok-123") == ""
    assert redact_secret("text", "") == "text"


# Prompt injection: shipped prompts must carry the {{gitops_guidance}} hook,
# and get_prompt must render it in when enabled / to nothing when not.


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_prompt_files_declare_the_gitops_variable():
    for path in (ROOT_PROMPT, CHART_PROMPT):
        content = _read(path)
        assert "{{gitops_guidance}}" in content, f"{path} lost the gitops hook"


def test_prompt_renders_gitops_section_when_enabled(monkeypatch):
    monkeypatch.setenv("KUBENTLY_A2A_PROMPT_FILE", ROOT_PROMPT)
    _configure(monkeypatch)
    prompt = get_prompt(role="a2a", variables={"gitops_guidance": gitops_guidance()})
    assert "propose_fix_pr" in prompt
    assert "get_manifest_file" in prompt
    assert "{{gitops_guidance}}" not in prompt


def test_prompt_never_mentions_gitops_when_disabled(monkeypatch):
    monkeypatch.setenv("KUBENTLY_A2A_PROMPT_FILE", ROOT_PROMPT)
    _clear_env(monkeypatch)
    prompt = get_prompt(role="a2a", variables={"gitops_guidance": gitops_guidance()})
    assert "propose_fix_pr" not in prompt
    assert "get_manifest_file" not in prompt
    assert "{{gitops_guidance}}" not in prompt


def test_prompt_default_keeps_placeholder_out_even_without_variables(monkeypatch):
    monkeypatch.setenv("KUBENTLY_A2A_PROMPT_FILE", ROOT_PROMPT)
    prompt = get_prompt(role="a2a")
    assert "{{gitops_guidance}}" not in prompt
    assert "propose_fix_pr" not in prompt


def test_guidance_covers_the_required_topics():
    """Track P8 asks for specific prompt guardrails: propose-only, high
    confidence, minimal fixes, change-correlation citations, diff against
    the real repo file."""
    text = GITOPS_PROMPT_SECTION
    assert "NEVER merge" in text
    assert "HIGH-CONFIDENCE" in text
    assert "MINIMAL" in text
    assert "get_recent_changes" in text  # cite change-correlation evidence
    assert "get_manifest_file" in text  # diff against reality, not memory
    assert "read-only" in text
