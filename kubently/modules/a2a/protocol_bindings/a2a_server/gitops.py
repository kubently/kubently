"""GitOps PR remediation — core logic for propose_fix_pr / get_manifest_file.

Track P8 closes the remediation gap without touching the read-only-cluster
trust posture: when an investigation identifies a concrete manifest fix, the
agent proposes it as a pull request against the configured GitOps manifests
repository. A human reviews and merges; the GitOps controller (ArgoCD/Flux)
applies. The agent NEVER merges and NEVER mutates cluster state.

Execution locus — API-side (the A2A server process), deliberately NOT the
executor:
- The remediation target is the Git hosting service (GitHub/GitLab), an
  external SaaS the control plane can reach directly. Nothing about it is
  cluster-local, so routing writes through a cluster's executor buys no
  locality — the evidence tools run executor-side because Loki/Prometheus/
  ArgoCD live inside the cluster network; a manifests repo does not.
- Executors are the read-only arm of the product. Shipping a write-capable
  Git credential to every remote executor would multiply the credential
  blast radius across customer clusters and break the "the thing in your
  cluster can only read" story that IS the brand.
- One API-side token means one secret to scope (to the manifests repo only),
  one place to rotate, and PRs still work when a cluster's executor is down —
  the fix lives in git, not in the cluster.

Token isolation: the token is read from the environment inside the provider
client and attached only to outbound Authorization headers. It is never a
tool argument, never part of tool output, never recorded in interceptor
traces, and every provider error surfaced to the model passes through
redact_secret() as a belt-and-braces guarantee.

Availability contract: the tools are registered ONLY when provider, repo and
token are all configured (default OFF), and the matching prompt guidance is
injected through the {{gitops_guidance}} variable on the same switch — the
model is never told about tools it cannot call.

Deliberately import-light (stdlib only) so unit tests can exercise config,
caps and formatting without pulling the langchain/a2a stack agent.py needs.
All HTTP lives in gitops_tools.py.
"""

import difflib
import os
import re
import uuid
from dataclasses import dataclass

PROVIDER_ENV = "KUBENTLY_GITOPS_PROVIDER"
REPO_ENV = "KUBENTLY_GITOPS_REPO"
BASE_BRANCH_ENV = "KUBENTLY_GITOPS_BASE_BRANCH"
TOKEN_ENV = "KUBENTLY_GITOPS_TOKEN"
API_BASE_ENV = "KUBENTLY_GITOPS_API_BASE"
MAX_FILES_ENV = "KUBENTLY_GITOPS_MAX_FILES"
MAX_LINES_ENV = "KUBENTLY_GITOPS_MAX_LINES"

SUPPORTED_PROVIDERS = ("github", "gitlab")

DEFAULT_BASE_BRANCH = "main"
DEFAULT_MAX_FILES = 5
DEFAULT_MAX_LINES = 200

# Marker required in every machine-proposed PR body. Reviewers (and any
# tooling gating on it) must be able to tell at a glance that no human wrote
# the change and that it is pending human review.
MACHINE_PROPOSED_MARKER = (
    "🤖 **Machine-proposed change — pending human review.** "
    "This pull request was opened automatically by the Kubently diagnostic "
    "agent from investigation evidence. Kubently never merges its own "
    "proposals: review the diff against the cited evidence before merging, "
    "and close the PR if the fix is wrong."
)


@dataclass
class GitOpsConfig:
    """The configured Git remediation target."""

    provider: str
    repo: str
    base_branch: str
    token: str
    api_base: str | None
    max_files: int
    max_lines: int


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except ValueError:
        return default
    return value if value > 0 else default


def load_config() -> GitOpsConfig | None:
    """The Git remediation config, or None when not (fully) configured.

    Default OFF: all three of provider, repo and token must be present.
    A partial configuration (e.g. repo without token) also resolves to None —
    the caller logs which pieces are missing so operators can tell "off"
    from "misconfigured".
    """
    provider = os.getenv(PROVIDER_ENV, "").strip().lower()
    repo = os.getenv(REPO_ENV, "").strip().strip("/")
    token = os.getenv(TOKEN_ENV, "").strip()
    if provider not in SUPPORTED_PROVIDERS or not repo or not token:
        return None
    return GitOpsConfig(
        provider=provider,
        repo=repo,
        base_branch=os.getenv(BASE_BRANCH_ENV, "").strip() or DEFAULT_BASE_BRANCH,
        token=token,
        api_base=os.getenv(API_BASE_ENV, "").strip() or None,
        max_files=_int_env(MAX_FILES_ENV, DEFAULT_MAX_FILES),
        max_lines=_int_env(MAX_LINES_ENV, DEFAULT_MAX_LINES),
    )


def gitops_tools_enabled() -> bool:
    """Whether the propose_fix_pr / get_manifest_file tools should register."""
    return load_config() is not None


def missing_config_pieces() -> list[str]:
    """Which required settings are absent/invalid — for operator-facing logs."""
    missing = []
    provider = os.getenv(PROVIDER_ENV, "").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        missing.append(f"{PROVIDER_ENV} (must be one of {'/'.join(SUPPORTED_PROVIDERS)})")
    if not os.getenv(REPO_ENV, "").strip():
        missing.append(REPO_ENV)
    if not os.getenv(TOKEN_ENV, "").strip():
        missing.append(TOKEN_ENV)
    return missing


def redact_secret(text: str, token: str) -> str:
    """Strip the Git token from any text that could reach model context.

    Provider error bodies can echo request headers; nothing that leaves the
    tool layer may contain the credential.
    """
    if not text or not token:
        return text or ""
    return text.replace(token, "***")


# --------------------------------------------------------------------------
# Repo path hygiene
# --------------------------------------------------------------------------

_PATH_OK = re.compile(r"^[A-Za-z0-9._/@-]+$")


def validate_repo_path(path: str) -> str | None:
    """Reject path traversal / absolute / oddly-shaped repo paths.

    Returns an error string, or None when the path is acceptable.
    """
    if not path or not path.strip():
        return "Error: file path is empty."
    p = path.strip()
    if p.startswith("/") or p.startswith("\\"):
        return f"Error: '{path}' — repo paths must be relative (no leading slash)."
    parts = p.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return f"Error: '{path}' — repo paths may not contain '.', '..' or empty segments."
    if not _PATH_OK.match(p):
        return f"Error: '{path}' contains unsupported characters for a repo path."
    return None


# --------------------------------------------------------------------------
# Size caps
# --------------------------------------------------------------------------


def count_changed_lines(old_content: str | None, new_content: str) -> int:
    """Lines added + removed between the current and the proposed content.

    A new file counts every proposed line as added. Computed with difflib so
    a one-field edit in a 500-line manifest costs 2 lines, not 500 — the cap
    measures the CHANGE, which is what a reviewer has to read.
    """
    new_lines = (new_content or "").splitlines()
    if old_content is None:
        return len(new_lines)
    old_lines = old_content.splitlines()
    changed = 0
    for line in difflib.unified_diff(old_lines, new_lines, lineterm="", n=0):
        if line.startswith(("---", "+++")):
            continue
        if line.startswith(("+", "-")):
            changed += 1
    return changed


def check_size_caps(
    changes: dict[str, int], max_files: int, max_lines: int
) -> str | None:
    """Enforce the PR size cap. Returns a refusal message, or None when OK.

    changes maps file path -> changed-line count (see count_changed_lines).
    The refusal is explicit about which cap fired and what to do instead, so
    the model narrows the proposal rather than retrying blind.
    """
    if len(changes) > max_files:
        return (
            f"REFUSED: this proposal touches {len(changes)} files, above the "
            f"configured cap of {max_files}. Machine-proposed PRs must stay "
            f"small enough to review confidently. Narrow the fix to the "
            f"file(s) the root cause actually requires, or tell the operator "
            f"the full change set so a human can make the broader edit."
        )
    total = sum(changes.values())
    if total > max_lines:
        detail = ", ".join(f"{path}: {n}" for path, n in changes.items())
        return (
            f"REFUSED: this proposal changes {total} lines ({detail}), above "
            f"the configured cap of {max_lines}. Machine-proposed PRs must be "
            f"minimal. Propose only the specific fields that fix the root "
            f"cause (do not reformat or rewrite whole manifests), or hand the "
            f"change to a human."
        )
    return None


# --------------------------------------------------------------------------
# Branch / PR content
# --------------------------------------------------------------------------

_SLUG_STRIP = re.compile(r"[^a-z0-9-]+")


def make_branch_name(title: str) -> str:
    """A unique, readable branch name derived from the PR title."""
    slug = _SLUG_STRIP.sub("-", (title or "fix").lower()).strip("-")[:40].strip("-")
    return f"kubently/{slug or 'fix'}-{uuid.uuid4().hex[:8]}"


def build_diff_preview(files: dict[str, tuple[str | None, str]], max_chars: int = 6000) -> str:
    """A unified diff of the proposal for the PR body (truncated when huge)."""
    chunks = []
    for path, (old, new) in files.items():
        diff = "\n".join(
            difflib.unified_diff(
                (old or "").splitlines(),
                (new or "").splitlines(),
                fromfile=f"a/{path}" if old is not None else "/dev/null",
                tofile=f"b/{path}",
                lineterm="",
            )
        )
        if diff:
            chunks.append(diff)
    text = "\n\n".join(chunks)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (diff truncated in PR body; the commit holds the full change)"
    return text


def build_pr_body(
    evidence_summary: str,
    files: dict[str, tuple[str | None, str]],
    cluster_id: str | None = None,
) -> str:
    """The PR description: marker, evidence, and the proposed diff.

    The evidence summary is the investigation's RCA — including the
    change-correlation citation the prompt guidance requires — so the
    reviewer can judge the fix without opening Kubently.
    """
    lines = [
        MACHINE_PROPOSED_MARKER,
        "",
        "## Investigation summary",
        "",
        (evidence_summary or "").strip() or "(no evidence summary provided)",
        "",
    ]
    if cluster_id:
        lines += [f"**Cluster:** `{cluster_id}`", ""]
    lines += [
        "## Files changed",
        "",
    ]
    for path, (old, _new) in files.items():
        lines.append(f"- `{path}` ({'update' if old is not None else 'new file'})")
    diff = build_diff_preview(files)
    if diff:
        lines += ["", "## Proposed diff", "", "```diff", diff, "```"]
    lines += [
        "",
        "---",
        "_Opened by the Kubently agent (GitOps PR remediation). Do not merge "
        "without reviewing the diff against the evidence above._",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Prompt guidance — injected via {{gitops_guidance}} only when the tools are
# registered, so an unconfigured deployment's prompt never mentions them.
# --------------------------------------------------------------------------

GITOPS_PROMPT_SECTION = """\
## GitOps remediation (propose fixes as pull requests)

A Git remediation target is configured. When — and only when — an
investigation identifies a concrete manifest fix, you may propose it as a
pull request with propose_fix_pr. You NEVER merge, and you NEVER mutate
cluster state: a human reviews and merges the PR, and the GitOps controller
applies it. Your cluster access stays read-only.

PROPOSE ONLY WHEN ALL OF THESE HOLD:
- The root cause is HIGH-CONFIDENCE: evidence (events, logs, metrics, change
  timeline) points to one specific manifest field, not a hypothesis.
- The fix is MINIMAL and mechanical: an image tag, resource limit/request,
  env var, replica count, probe threshold — a few lines, not a redesign.
- You can CITE change-correlation evidence in the summary (e.g. "OOMKills
  began 90s after revision 42 halved the memory limit — get_recent_changes
  timeline attached"). If you never ran get_recent_changes, run it first.
If any of these fail, present the finding and the suggested change in your
answer instead of opening a PR.

WORKFLOW — never edit blind:
1. get_manifest_file the file(s) you intend to change and confirm the field
   you diagnosed is actually there and set the way the cluster shows it.
   Base your edit on the FETCHED content — never on memory of what such a
   manifest usually looks like. If the repo file differs from the live
   cluster object, say so (drift) and do not propose until you understand why.
2. Build the full new file content: the fetched content with ONLY the
   diagnosed fields changed. Preserve formatting, comments and ordering.
3. Call propose_fix_pr with the file(s), a clear title, and an evidence
   summary containing the RCA and the change-correlation citation.
4. Put the returned PR URL in your answer — the RCA is not complete without
   it — and state explicitly that a human must review and merge.

PRs are size-capped (files and changed lines). If the tool refuses for size,
narrow the proposal to the root-cause fields; do not split one oversized
change into several PRs to dodge the cap.
"""


def gitops_guidance() -> str:
    """The prompt section to inject — empty when the tools are not registered."""
    return GITOPS_PROMPT_SECTION if gitops_tools_enabled() else ""
