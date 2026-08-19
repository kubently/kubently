"""
CloudOpsManager: the executor's single entry point for cloud read operations.

Responsibilities:
- Detect which cloud identity the pod holds (STS GetCallerIdentity on AWS,
  metadata server on GCP) — at startup and periodically.
- Probe which permission families are actually usable with that identity.
- Enforce the code-level operation allowlist on every dispatch.
- Produce the `cloud` section of the executor's capability report so the
  agent knows whether cloud tools exist before trying them.
"""

import logging
import time
from datetime import UTC, datetime
from typing import Any

from .base import CloudIdentity, CloudOperationResult, CloudProvider
from .operations import ALLOWED_CLOUD_OPERATIONS, operations_for_provider

logger = logging.getLogger("kubently-executor.cloud")

DEFAULT_REFRESH_INTERVAL = 3600  # re-detect identity/permissions hourly


class CloudOpsManager:
    """Detects the pod's cloud identity and executes whitelisted read ops."""

    def __init__(
        self,
        mode: str = "auto",
        aws_region: str | None = None,
        gcp_project: str | None = None,
        refresh_interval: int = DEFAULT_REFRESH_INTERVAL,
        providers: list[CloudProvider] | None = None,
    ):
        """
        Args:
            mode: "auto" (try AWS then GCP), "aws", or "gcp". ("off" is
                handled by the caller by not constructing a manager.)
            aws_region / gcp_project: optional overrides passed to providers.
            refresh_interval: seconds between identity/permission re-detection.
            providers: injectable provider list for tests; overrides mode.
        """
        self.mode = mode
        self.refresh_interval = max(60, int(refresh_interval))
        self._candidates = (
            providers
            if providers is not None
            else self._build_candidates(mode, aws_region, gcp_project)
        )
        self._active: CloudProvider | None = None
        self.identity: CloudIdentity | None = None
        self.usable_families: dict[str, bool] = {}
        self._last_detection: float = 0.0

    @staticmethod
    def _build_candidates(
        mode: str, aws_region: str | None, gcp_project: str | None
    ) -> list[CloudProvider]:
        candidates: list[CloudProvider] = []
        if mode in ("auto", "aws"):
            try:
                from .aws_provider import AWSProvider

                candidates.append(AWSProvider(region=aws_region))
            except Exception as e:
                logger.warning(f"AWS provider unavailable: {e}")
        if mode in ("auto", "gcp"):
            try:
                from .gcp_provider import GCPProvider

                candidates.append(GCPProvider(project=gcp_project))
            except Exception as e:
                logger.warning(f"GCP provider unavailable: {e}")
        return candidates

    # ----------------------------------------------------------- detection

    def detect(self, force: bool = False) -> CloudIdentity | None:
        """
        Detect the held cloud identity and usable permissions.

        Cached for refresh_interval; pass force=True to re-detect now.
        Never raises — a pod with no cloud identity simply reports none.
        """
        now = time.time()
        if (
            not force
            and self._last_detection
            and (now - self._last_detection < self.refresh_interval)
        ):
            return self.identity

        self._last_detection = now
        for provider in self._candidates:
            try:
                identity = provider.detect_identity()
            except Exception as e:  # providers shouldn't raise, but never crash
                logger.warning(f"Identity detection failed for {provider.name}: {e}")
                continue
            if identity:
                self._active = provider
                self.identity = identity
                try:
                    self.usable_families = provider.probe_permissions()
                except Exception as e:
                    logger.warning(f"Permission probing failed for {provider.name}: {e}")
                    self.usable_families = {}
                logger.info(
                    f"Cloud identity detected: {identity.provider} "
                    f"({identity.principal}), usable families: "
                    f"{[f for f, ok in self.usable_families.items() if ok]}"
                )
                return identity

        self._active = None
        self.identity = None
        self.usable_families = {}
        logger.info("No cloud identity detected; cloud operations unavailable")
        return None

    def refresh_due(self) -> bool:
        """True when the periodic re-detection interval has elapsed."""
        return time.time() - self._last_detection >= self.refresh_interval

    # ------------------------------------------------------------ dispatch

    def execute(self, operation: str, params: dict[str, Any]) -> CloudOperationResult:
        """
        Execute one cloud operation, enforcing the code-level allowlist.

        The allowlist check happens here, before any provider code runs —
        IAM alone is never the only barrier.
        """
        spec = ALLOWED_CLOUD_OPERATIONS.get(operation)
        if spec is None:
            return CloudOperationResult(
                success=False,
                operation=operation,
                provider="unknown",
                error=(
                    f"Operation '{operation}' is not on the cloud operation "
                    f"allowlist. Allowed: {sorted(ALLOWED_CLOUD_OPERATIONS)}"
                ),
                error_code="OPERATION_NOT_ALLOWED",
            )

        if self._active is None:
            self.detect()
        if self._active is None:
            return CloudOperationResult(
                success=False,
                operation=operation,
                provider=spec.provider,
                error="No cloud identity detected on this executor",
                error_code="NO_CLOUD_IDENTITY",
            )
        if spec.provider != self._active.name:
            return CloudOperationResult(
                success=False,
                operation=operation,
                provider=self._active.name,
                error=(
                    f"Operation '{operation}' targets provider '{spec.provider}' "
                    f"but this executor holds a '{self._active.name}' identity"
                ),
                error_code="PROVIDER_MISMATCH",
            )
        return self._active.execute(operation, params)

    # -------------------------------------------------------- capabilities

    def capability_payload(self) -> dict[str, Any] | None:
        """
        The `cloud` section of the executor capability report, or None when
        no cloud identity is held (so the agent registers no cloud tools).
        """
        self.detect()
        if self.identity is None or self._active is None:
            return None

        # Advertise only operations whose permission family probed as usable
        operations = sorted(
            spec.name
            for spec in operations_for_provider(self._active.name)
            if self.usable_families.get(spec.family, False)
        )
        return {
            "provider": self.identity.provider,
            "identity": self.identity.to_dict(),
            "operations": operations,
            "usable_families": {f: ok for f, ok in sorted(self.usable_families.items())},
            "checked_at": datetime.fromtimestamp(self._last_detection, tz=UTC).isoformat(),
        }
