"""Read-only ArgoCD Application query runner for the Kubently executor.

GitOps deployments change clusters through ArgoCD syncs, so "what changed?"
needs Application sync status and history. The control plane cannot reach an
in-cluster argocd-server, so queries travel the same outbound channel as
kubectl commands: API -> Redis pub/sub -> executor SSE -> result POST. This
module is the executor-side terminus of that path.

Security model (mirrors the Prometheus runner):

- The base URL and token come ONLY from local executor configuration
  (ARGOCD_URL / ARGOCD_TOKEN). The control plane never supplies a URL or
  credentials, so a compromised API key cannot turn the executor into an
  SSRF proxy or steal the token.
- Only fixed, read-only GET paths are reachable: get one application,
  list applications, and revision metadata. Nothing else is constructible
  from a request; application names and revisions are validated before they
  are placed in the path.
- When ARGOCD_URL is unset the runner reports "unavailable" without making
  any network call.

Results are compacted (Application objects are stripped down to identity,
sync/health status, and deployment history) and capped before they leave the
executor, so a large App-of-Apps install never floods Redis or the model's
context.

Deliberately import-light (stdlib + requests) to match sse_executor.py.
"""

import json
import logging
import os
import re

import requests

logger = logging.getLogger("kubently-argocd")

ALLOWED_OPERATIONS = {"get_app", "list_apps", "revision_metadata"}

# ArgoCD app names are k8s resource names; revisions are git refs/SHAs.
_APP_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")
_REVISION_PATTERN = re.compile(r"^[A-Za-z0-9._/-]{1,128}$")

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_APPS = 50
DEFAULT_MAX_HISTORY = 10
DEFAULT_MAX_OUTPUT_CHARS = 20000

UNAVAILABLE_MESSAGE = (
    "ArgoCD is not configured on this cluster's executor (ARGOCD_URL is "
    "unset). GitOps sync history is unavailable here; correlate changes "
    "with rollout history, helm history and events instead."
)


class ArgoCDRunner:
    """Executes validated read-only queries against a locally configured
    ArgoCD API and returns results in the executor's standard result shape."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: int | None = None,
        max_output_chars: int | None = None,
    ):
        self.base_url = (
            (base_url if base_url is not None else os.environ.get("ARGOCD_URL", ""))
            .strip()
            .rstrip("/")
        )
        self.token = (token if token is not None else os.environ.get("ARGOCD_TOKEN", "")).strip()
        self.timeout = timeout or int(
            os.environ.get("ARGOCD_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS))
        )
        self.max_output_chars = max_output_chars or int(
            os.environ.get("ARGOCD_MAX_OUTPUT_CHARS", str(DEFAULT_MAX_OUTPUT_CHARS))
        )
        # Optional CA bundle for a self-signed argocd-server certificate.
        # TLS verification stays on; ARGOCD_CA_CERT points requests at the CA.
        self.ca_cert_path = os.environ.get("ARGOCD_CA_CERT") or None

    @property
    def available(self) -> bool:
        return bool(self.base_url)

    def run(self, request: dict) -> dict:
        """Run one query and return the executor result shape:
        {"success", "output", "error", "status", "return_code"}."""
        if not self.available:
            return self._error(UNAVAILABLE_MESSAGE, status="UNAVAILABLE")

        operation = request.get("operation")
        if operation not in ALLOWED_OPERATIONS:
            return self._error(
                f"Unsupported ArgoCD operation '{operation}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_OPERATIONS))}."
            )

        app_name = request.get("app_name")
        if operation in ("get_app", "revision_metadata"):
            if not app_name or not _APP_NAME_PATTERN.match(str(app_name)):
                return self._error(f"Invalid or missing app_name '{app_name}'.")

        params = {}
        if operation == "get_app":
            path = f"/api/v1/applications/{app_name}"
        elif operation == "revision_metadata":
            revision = request.get("revision")
            if not revision or not _REVISION_PATTERN.match(str(revision)):
                return self._error(f"Invalid or missing revision '{revision}'.")
            path = f"/api/v1/applications/{app_name}/revisions/{revision}/metadata"
        else:  # list_apps
            path = "/api/v1/applications"
            selector = request.get("selector")
            if selector:
                if not re.match(r"^[A-Za-z0-9=.,_/-]{1,256}$", str(selector)):
                    return self._error(f"Invalid selector '{selector}'.")
                params["selector"] = str(selector)

        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            response = requests.get(
                f"{self.base_url}{path}",
                params=params,
                headers=headers,
                timeout=self.timeout,
                verify=self.ca_cert_path if self.ca_cert_path else True,
            )
        except requests.exceptions.Timeout:
            return self._error(f"ArgoCD query timed out after {self.timeout}s.", status="TIMEOUT")
        except requests.exceptions.RequestException as e:
            return self._error(f"Could not reach ArgoCD at {self.base_url}: {e}")

        try:
            body = response.json()
        except ValueError:
            return self._error(
                f"ArgoCD returned non-JSON (HTTP {response.status_code}): {response.text[:200]}"
            )

        if response.status_code != 200:
            detail = body.get("message") or body.get("error") or f"HTTP {response.status_code}"
            return self._error(f"ArgoCD query failed: {detail}")

        output = self._format(operation, body)
        return {
            "success": True,
            "output": output,
            "error": None,
            "status": "SUCCESS",
            "return_code": 0,
        }

    def _format(self, operation: str, body: dict) -> str:
        """Compact the response to what change correlation needs."""
        if operation == "get_app":
            payload = self._compact_app(body, max_history=DEFAULT_MAX_HISTORY)
        elif operation == "list_apps":
            items = body.get("items") or []
            total = len(items)
            # History is dropped in list mode — get_app has it per app.
            payload = {
                "items": [self._compact_app(app, max_history=0) for app in items[:DEFAULT_MAX_APPS]]
            }
            if total > DEFAULT_MAX_APPS:
                payload["kubently_truncation"] = (
                    f"showing {DEFAULT_MAX_APPS} of {total} applications — filter with a selector"
                )
        else:  # revision_metadata: already small (author/date/message)
            payload = body

        output = json.dumps(payload, separators=(",", ":"), default=str)
        if len(output) > self.max_output_chars:
            output = output[: self.max_output_chars] + (
                f"\n[truncated at {self.max_output_chars} chars]"
            )
        return output

    @staticmethod
    def _compact_app(app: dict, max_history: int) -> dict:
        """Reduce an Application object to identity + sync/health + history."""
        metadata = app.get("metadata") or {}
        spec = app.get("spec") or {}
        status = app.get("status") or {}
        sync = status.get("sync") or {}
        health = status.get("health") or {}
        operation_state = status.get("operationState") or {}

        compact = {
            "name": metadata.get("name"),
            "project": spec.get("project"),
            "destNamespace": (spec.get("destination") or {}).get("namespace"),
            "syncStatus": sync.get("status"),
            "syncRevision": sync.get("revision"),
            "healthStatus": health.get("status"),
            "lastOperation": {
                "phase": operation_state.get("phase"),
                "message": (operation_state.get("message") or "")[:200] or None,
                "finishedAt": operation_state.get("finishedAt"),
            },
        }

        if max_history:
            history = status.get("history") or []
            compact["history"] = [
                {
                    "id": h.get("id"),
                    "revision": h.get("revision"),
                    "deployedAt": h.get("deployedAt"),
                    "deployStartedAt": h.get("deployStartedAt"),
                    "source": {
                        "repoURL": (h.get("source") or {}).get("repoURL"),
                        "targetRevision": (h.get("source") or {}).get("targetRevision"),
                        "chart": (h.get("source") or {}).get("chart"),
                    },
                }
                for h in history[-max_history:]
            ]
        return compact

    @staticmethod
    def _error(message: str, status: str = "FAILED") -> dict:
        return {
            "success": False,
            "output": None,
            "error": message,
            "status": status,
            "return_code": -1,
        }
