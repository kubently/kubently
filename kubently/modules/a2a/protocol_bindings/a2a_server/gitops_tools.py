"""GitOps PR remediation — provider clients and agent tools.

HTTP layer for gitops.py (see that module for the design rationale and the
API-side execution decision). Two providers, one contract:

    get_file(path, ref)        -> file content or None (absent)
    create_branch(branch)      -> branch off the configured base
    commit_files(branch, ...)  -> one logical commit of the proposed files
    create_pr(branch, ...)     -> PR/MR URL

The token is attached only to outbound request headers inside this module.
Every error string that could reach model context passes through
redact_secret(). Tests inject an httpx.MockTransport through the `transport`
parameter, so no test ever needs a real Git host.

Kept out of agent.py so the agent-toolset area stays minimal and additive
(sibling branches land there concurrently).
"""

import base64
import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from .fleet import cap_output
from .gitops import (
    GitOpsConfig,
    build_pr_body,
    check_size_caps,
    count_changed_lines,
    gitops_tools_enabled,
    load_config,
    make_branch_name,
    missing_config_pieces,
    redact_secret,
    validate_repo_path,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30.0


class GitProviderError(Exception):
    """A provider API failure; the message is already token-redacted."""


class _BaseProvider:
    def __init__(self, config: GitOpsConfig, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=REQUEST_TIMEOUT)

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    async def _request(
        self,
        method: str,
        url: str,
        expect: tuple[int, ...],
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> Any:
        """One provider API call; non-expected statuses raise redacted errors."""
        try:
            async with self._client() as client:
                response = await client.request(
                    method, url, headers=self._headers(), json=json_body, params=params
                )
        except Exception as e:
            raise GitProviderError(
                redact_secret(f"request to Git provider failed: {e!s}", self.config.token)
            ) from None
        if allow_404 and response.status_code == 404:
            return None
        if response.status_code not in expect:
            raise GitProviderError(
                redact_secret(
                    f"Git provider returned HTTP {response.status_code}: {response.text[:300]}",
                    self.config.token,
                )
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {}

    async def get_file(self, path: str, ref: str | None = None) -> str | None:
        raise NotImplementedError

    async def create_branch(self, branch: str) -> None:
        raise NotImplementedError

    async def commit_files(
        self, branch: str, files: dict[str, tuple[str | None, str]], message: str
    ) -> None:
        raise NotImplementedError

    async def create_pr(self, branch: str, title: str, body: str) -> str:
        raise NotImplementedError


class GitHubProvider(_BaseProvider):
    """GitHub REST v3 (github.com or GHE via KUBENTLY_GITOPS_API_BASE)."""

    @property
    def api(self) -> str:
        return (self.config.api_base or "https://api.github.com").rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.token}",
            "Accept": "application/vnd.github+json",
        }

    async def get_file(self, path: str, ref: str | None = None) -> str | None:
        result = await self._request(
            "GET",
            f"{self.api}/repos/{self.config.repo}/contents/{quote(path)}",
            expect=(200,),
            params={"ref": ref or self.config.base_branch},
            allow_404=True,
        )
        if result is None:
            return None
        if isinstance(result, list):
            raise GitProviderError(f"'{path}' is a directory, not a file")
        return base64.b64decode(result.get("content") or "").decode("utf-8", "replace")

    async def _file_sha(self, path: str, ref: str) -> str | None:
        result = await self._request(
            "GET",
            f"{self.api}/repos/{self.config.repo}/contents/{quote(path)}",
            expect=(200,),
            params={"ref": ref},
            allow_404=True,
        )
        return result.get("sha") if isinstance(result, dict) else None

    async def create_branch(self, branch: str) -> None:
        ref = await self._request(
            "GET",
            f"{self.api}/repos/{self.config.repo}/git/ref/heads/{quote(self.config.base_branch)}",
            expect=(200,),
        )
        base_sha = ((ref or {}).get("object") or {}).get("sha")
        if not base_sha:
            raise GitProviderError(f"could not resolve base branch '{self.config.base_branch}'")
        await self._request(
            "POST",
            f"{self.api}/repos/{self.config.repo}/git/refs",
            expect=(201,),
            json_body={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )

    async def commit_files(
        self, branch: str, files: dict[str, tuple[str | None, str]], message: str
    ) -> None:
        # Contents API: one commit per file. Fine at the enforced size cap,
        # and far simpler than the trees API.
        for path, (old_content, new_content) in files.items():
            body: dict[str, Any] = {
                "message": message,
                "content": base64.b64encode(new_content.encode()).decode(),
                "branch": branch,
            }
            if old_content is not None:
                sha = await self._file_sha(path, branch)
                if sha:
                    body["sha"] = sha
            await self._request(
                "PUT",
                f"{self.api}/repos/{self.config.repo}/contents/{quote(path)}",
                expect=(200, 201),
                json_body=body,
            )

    async def create_pr(self, branch: str, title: str, body: str) -> str:
        result = await self._request(
            "POST",
            f"{self.api}/repos/{self.config.repo}/pulls",
            expect=(201,),
            json_body={
                "title": title,
                "head": branch,
                "base": self.config.base_branch,
                "body": body,
            },
        )
        url = (result or {}).get("html_url")
        if not url:
            raise GitProviderError("PR created but no html_url in provider response")
        return url


class GitLabProvider(_BaseProvider):
    """GitLab REST v4 (gitlab.com or self-hosted via KUBENTLY_GITOPS_API_BASE)."""

    @property
    def api(self) -> str:
        return (self.config.api_base or "https://gitlab.com/api/v4").rstrip("/")

    @property
    def project(self) -> str:
        return quote(self.config.repo, safe="")

    def _headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self.config.token}

    async def get_file(self, path: str, ref: str | None = None) -> str | None:
        result = await self._request(
            "GET",
            f"{self.api}/projects/{self.project}/repository/files/{quote(path, safe='')}",
            expect=(200,),
            params={"ref": ref or self.config.base_branch},
            allow_404=True,
        )
        if result is None:
            return None
        return base64.b64decode(result.get("content") or "").decode("utf-8", "replace")

    async def create_branch(self, branch: str) -> None:
        await self._request(
            "POST",
            f"{self.api}/projects/{self.project}/repository/branches",
            expect=(201,),
            params={"branch": branch, "ref": self.config.base_branch},
        )

    async def commit_files(
        self, branch: str, files: dict[str, tuple[str | None, str]], message: str
    ) -> None:
        actions = [
            {
                "action": "update" if old_content is not None else "create",
                "file_path": path,
                "content": new_content,
            }
            for path, (old_content, new_content) in files.items()
        ]
        await self._request(
            "POST",
            f"{self.api}/projects/{self.project}/repository/commits",
            expect=(201,),
            json_body={"branch": branch, "commit_message": message, "actions": actions},
        )

    async def create_pr(self, branch: str, title: str, body: str) -> str:
        result = await self._request(
            "POST",
            f"{self.api}/projects/{self.project}/merge_requests",
            expect=(201,),
            json_body={
                "source_branch": branch,
                "target_branch": self.config.base_branch,
                "title": title,
                "description": body,
            },
        )
        url = (result or {}).get("web_url")
        if not url:
            raise GitProviderError("MR created but no web_url in provider response")
        return url


def make_provider(
    config: GitOpsConfig, transport: httpx.AsyncBaseTransport | None = None
) -> _BaseProvider:
    if config.provider == "github":
        return GitHubProvider(config, transport)
    return GitLabProvider(config, transport)


# --------------------------------------------------------------------------
# Tool construction
# --------------------------------------------------------------------------


def build_gitops_tools(
    interceptor: Any,
    thread_id_getter: Callable[[], str | None],
    transport: httpx.AsyncBaseTransport | None = None,
) -> list:
    """Build [get_manifest_file, propose_fix_pr], or [] when not configured.

    Default OFF. A partial configuration logs which pieces are missing (so
    operators can tell "off" from "misconfigured") and still returns [].
    """
    if not gitops_tools_enabled():
        missing = missing_config_pieces()
        if len(missing) < 3:  # something was set, but not everything
            logger.warning(
                f"GitOps remediation partially configured — tools stay OFF. Missing: {missing}"
            )
        else:
            logger.info("GitOps remediation not configured; propose_fix_pr disabled")
        return []

    config = load_config()
    provider = make_provider(config, transport)
    logger.info(
        f"GitOps remediation tools registered: {config.provider} repo "
        f"{config.repo} (base {config.base_branch}, caps {config.max_files} "
        f"files / {config.max_lines} lines)"
    )

    from langchain_core.tools import tool

    @tool
    async def get_manifest_file(path: str, ref: str = "") -> str:
        """Read one file from the configured GitOps manifests repository.

        ALWAYS call this before proposing a fix with propose_fix_pr: base
        your edit on the file as it actually exists in git, never on memory
        of what such a manifest usually looks like. Also useful to check for
        drift between the repo and the live cluster object.

        This is read-only and touches only the one configured manifests
        repository — it cannot read any other repo.

        Args:
            path: File path inside the repo (e.g. "apps/api/deployment.yaml")
            ref: Branch/tag/commit to read at (default: the configured base branch)

        Returns:
            The file content, or a clear message when the file does not exist.
        """
        tool_call_id = await interceptor.record_tool_call(
            tool_name="get_manifest_file",
            args={"path": path, "ref": ref},
            thread_id=thread_id_getter(),
        )
        path_error = validate_repo_path(path)
        if path_error:
            await interceptor.record_tool_result(tool_call_id, None, path_error)
            return path_error
        try:
            content = await provider.get_file(path.strip(), ref.strip() or None)
            if content is None:
                output = (
                    f"File '{path}' does not exist in {config.repo} at "
                    f"'{ref.strip() or config.base_branch}'. Check the path — "
                    f"do not guess manifest content for a file you cannot read."
                )
            else:
                output = cap_output(content)
            await interceptor.record_tool_result(tool_call_id, output)
            return output
        except GitProviderError as e:
            error_msg = f"Error reading '{path}' from {config.repo}: {e!s}"
            await interceptor.record_tool_result(tool_call_id, None, error_msg)
            return error_msg
        except Exception as e:
            error_msg = redact_secret(f"Error reading '{path}': {e!s}", config.token)
            await interceptor.record_tool_result(tool_call_id, None, error_msg)
            return error_msg

    @tool
    async def propose_fix_pr(
        title: str,
        files: dict[str, str],
        evidence_summary: str,
        cluster_id: str = "",
    ) -> str:
        """Propose a manifest fix as a pull request for HUMAN review — never merged by you.

        Creates a branch off the configured base branch, commits the proposed
        file content, and opens a PR whose body carries your evidence summary
        and is clearly marked machine-proposed. A human reviews and merges;
        the GitOps controller applies. You have NO merge capability and must
        never claim the fix is applied — only that it is proposed.

        PRECONDITIONS (refuse yourself if unmet):
        - High-confidence RCA pointing at specific manifest fields
        - Minimal fix (a few lines), based on content you actually fetched
          with get_manifest_file
        - evidence_summary cites the correlated change (get_recent_changes)

        Proposals above the configured size cap (files / changed lines) are
        refused — narrow the fix rather than splitting it across PRs.

        Args:
            title: Short imperative PR title (e.g. "Raise api memory limit to 512Mi")
            files: Map of repo file path -> FULL new file content (the fetched
                content with only the diagnosed fields changed)
            evidence_summary: The RCA with its evidence and change-correlation
                citation — this becomes the PR body a human reviews against
            cluster_id: Cluster the investigation ran against (for the PR body)

        Returns:
            The PR URL to include in your RCA, or a refusal/error message.
        """
        tool_call_id = await interceptor.record_tool_call(
            tool_name="propose_fix_pr",
            args={
                # The token is not an argument and file contents stay out of
                # the trace — paths + summary are what test analysis needs.
                "title": title,
                "files": sorted(files or {}),
                "evidence_summary": evidence_summary,
                "cluster_id": cluster_id,
            },
            thread_id=thread_id_getter(),
        )

        async def _finish(output: str, error: bool = False) -> str:
            if error:
                await interceptor.record_tool_result(tool_call_id, None, output)
            else:
                await interceptor.record_tool_result(tool_call_id, output)
            return output

        if not files:
            return await _finish("Error: no files provided — nothing to propose.", error=True)
        if not (title or "").strip():
            return await _finish("Error: a PR title is required.", error=True)
        if not (evidence_summary or "").strip():
            return await _finish(
                "REFUSED: an evidence summary is required. A machine-proposed "
                "PR without its supporting evidence cannot be reviewed — "
                "include the RCA and the change-correlation citation.",
                error=True,
            )
        for path in files:
            path_error = validate_repo_path(path)
            if path_error:
                return await _finish(path_error, error=True)

        # Cheap cap first: file count needs no network.
        if len(files) > config.max_files:
            refusal = check_size_caps(
                {path: 0 for path in files}, config.max_files, config.max_lines
            )
            return await _finish(refusal)

        try:
            # Fetch current content at base: the diff basis for the line cap,
            # the PR-body diff, and create-vs-update on commit.
            staged: dict[str, tuple[str | None, str]] = {}
            for path, new_content in files.items():
                old_content = await provider.get_file(path.strip())
                staged[path.strip()] = (old_content, new_content or "")

            changes = {path: count_changed_lines(old, new) for path, (old, new) in staged.items()}
            if all(n == 0 for n in changes.values()):
                return await _finish(
                    "Error: the proposed content is identical to what is "
                    "already in the repo — nothing to change. Re-check the "
                    "diagnosis (the fix may already be merged, or the live "
                    "cluster has drifted from git)."
                )
            refusal = check_size_caps(changes, config.max_files, config.max_lines)
            if refusal:
                return await _finish(refusal)

            branch = make_branch_name(title)
            body = build_pr_body(evidence_summary, staged, cluster_id.strip() or None)
            await provider.create_branch(branch)
            await provider.commit_files(branch, staged, title.strip())
            pr_url = await provider.create_pr(branch, title.strip(), body)

            total = sum(changes.values())
            return await _finish(
                f"Proposed fix PR created (NOT merged): {pr_url}\n"
                f"Branch: {branch} -> {config.base_branch} in {config.repo}; "
                f"{len(staged)} file(s), {total} changed line(s).\n"
                f"A human must review and merge it before anything changes in "
                f"the cluster — include this URL in your RCA and say so "
                f"explicitly."
            )
        except GitProviderError as e:
            return await _finish(f"Error creating fix PR in {config.repo}: {e!s}", error=True)
        except Exception as e:
            return await _finish(
                redact_secret(f"Error creating fix PR: {e!s}", config.token), error=True
            )

    return [get_manifest_file, propose_fix_pr]
