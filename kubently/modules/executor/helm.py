"""Read-only Helm release history runner for the Kubently executor.

Change correlation ("what changed before this incident?") needs Helm release
history, and only the executor inside the cluster can read it. Helm commands
travel the same outbound channel as kubectl commands: API -> Redis pub/sub ->
executor SSE -> result POST. This module is the executor-side terminus.

Security model (mirrors the kubectl whitelist philosophy — the executor, not
the caller, decides what it will execute):

- Only two read-only subcommands are reachable: `helm history` and
  `helm list`. The runner builds the argv itself from validated fields; the
  control plane never supplies raw arguments, so no other subcommand or flag
  is constructible from a request.
- Release names and namespaces are validated against strict patterns before
  they are placed in the argv.
- The feature is opt-in: HELM_HISTORY_ENABLED must be "true" (the Helm chart
  sets it from executor.helmHistory.enabled, which also grants the RBAC on
  release Secrets that `helm history` needs). When disabled — or when the
  helm binary is absent — the runner reports "unavailable" without executing
  anything.

Output is JSON (`-o json`) and capped before it leaves the executor so a
cluster with hundreds of releases never floods Redis or the model's context.

Deliberately import-light (stdlib only) to match sse_executor.py.
"""

import logging
import os
import re
import shutil
import subprocess

logger = logging.getLogger("kubently-helm")

# The complete surface area: subcommand -> argv builder input. Nothing else is
# reachable, and both subcommands are read-only.
ALLOWED_SUBCOMMANDS = {"history", "list"}

# Helm release names: DNS-1123-ish (helm enforces max 53 chars).
_RELEASE_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,51}[a-z0-9])?$")
_NAMESPACE_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_REVISIONS = 10
DEFAULT_MAX_RELEASES = 20
DEFAULT_MAX_OUTPUT_CHARS = 20000

UNAVAILABLE_MESSAGE = (
    "Helm history is not enabled on this cluster's executor "
    "(HELM_HISTORY_ENABLED is not 'true', or the helm binary is missing). "
    "Release history is unavailable here; correlate changes with "
    "rollout history and events instead."
)


class HelmRunner:
    """Executes validated read-only helm subcommands and returns results in
    the executor's standard result shape."""

    def __init__(
        self,
        enabled: bool | None = None,
        timeout: int | None = None,
        max_output_chars: int | None = None,
        helm_path: str | None = None,
    ):
        self.enabled = (
            enabled
            if enabled is not None
            else os.environ.get("HELM_HISTORY_ENABLED", "false").strip().lower() == "true"
        )
        self.timeout = timeout or int(os.environ.get("HELM_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS)))
        self.max_output_chars = max_output_chars or int(
            os.environ.get("HELM_MAX_OUTPUT_CHARS", str(DEFAULT_MAX_OUTPUT_CHARS))
        )
        self.helm_path = helm_path or shutil.which("helm")

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.helm_path)

    def run(self, request: dict) -> dict:
        """Run one helm request and return the executor result shape:
        {"success", "output", "error", "status", "return_code"}."""
        if not self.available:
            return self._error(UNAVAILABLE_MESSAGE, status="UNAVAILABLE")

        subcommand = request.get("subcommand")
        if subcommand not in ALLOWED_SUBCOMMANDS:
            return self._error(
                f"Unsupported helm subcommand '{subcommand}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_SUBCOMMANDS))}."
            )

        argv, error = self._build_argv(subcommand, request)
        if error:
            return self._error(error)

        logger.debug(f"Running: {' '.join(argv)}")
        try:
            process = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return self._error(
                f"helm {subcommand} timed out after {self.timeout}s.", status="TIMEOUT"
            )
        except Exception as e:
            return self._error(f"helm execution failed: {e}")

        if process.returncode != 0:
            # Helm's own error (release not found, RBAC denied on release
            # Secrets, ...) — pass it through, it is actionable for the model.
            detail = (process.stderr or process.stdout or "").strip()
            return self._error(f"helm {subcommand} failed: {detail[:500]}")

        output = self._cap(process.stdout)
        return {
            "success": True,
            "output": output,
            "error": None,
            "status": "SUCCESS",
            "return_code": 0,
        }

    def _build_argv(self, subcommand: str, request: dict) -> tuple[list, str | None]:
        """Construct the full argv from validated fields. Returns (argv, error)."""
        namespace = request.get("namespace")
        if namespace is not None and not _NAMESPACE_PATTERN.match(str(namespace)):
            return [], f"Invalid namespace '{namespace}'."

        if subcommand == "history":
            release = request.get("release_name")
            if not release or not _RELEASE_NAME_PATTERN.match(str(release)):
                return [], f"Invalid or missing release_name '{release}'."
            max_revisions = self._bounded_int(request.get("max"), DEFAULT_MAX_REVISIONS, upper=50)
            argv = [
                self.helm_path,
                "history",
                str(release),
                "-o",
                "json",
                "--max",
                str(max_revisions),
            ]
            if namespace:
                argv.extend(["-n", str(namespace)])
            return argv, None

        # subcommand == "list"
        max_releases = self._bounded_int(request.get("max"), DEFAULT_MAX_RELEASES, upper=100)
        argv = [
            self.helm_path,
            "list",
            "-o",
            "json",
            "--max",
            str(max_releases),
            "--all",
            "--date",
            "--reverse",
        ]
        if namespace:
            argv.extend(["-n", str(namespace)])
        else:
            argv.append("--all-namespaces")
        return argv, None

    @staticmethod
    def _bounded_int(value, default: int, upper: int, lower: int = 1) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return default
        return max(lower, min(n, upper))

    def _cap(self, output: str) -> str:
        output = output.strip()
        if len(output) > self.max_output_chars:
            # Best effort: keep valid context by truncating with a clear note.
            output = output[: self.max_output_chars] + (
                f"\n[truncated at {self.max_output_chars} chars — "
                "narrow with a namespace or lower max]"
            )
        return output

    @staticmethod
    def _error(message: str, status: str = "FAILED") -> dict:
        return {
            "success": False,
            "output": None,
            "error": message,
            "status": status,
            "return_code": -1,
        }
