#!/usr/bin/env python3
"""
SSE Kubently Executor - Server-Sent Events based executor.

This executor uses SSE for real-time command reception, eliminating
polling and providing instant command delivery in a horizontally
scaled environment.

Optional capability reporting allows the executor to advertise its
DynamicCommandWhitelist configuration to the central API, enabling
capability-aware agent behavior.
"""

import json
import logging
import os
import subprocess
import sys
import time
from queue import Queue
from threading import Thread
from typing import Any

import requests

# sseclient is only needed at runtime for the SSE stream; make it optional so the
# command-execution logic stays importable (and unit-testable) without it.
try:
    import sseclient
except ImportError:
    sseclient = None

# Optional import - DynamicCommandWhitelist may not be available in all deployments
try:
    from kubently.modules.executor.dynamic_whitelist import DynamicCommandWhitelist
    WHITELIST_AVAILABLE = True
except ImportError:
    WHITELIST_AVAILABLE = False

# Optional import - cloud read operations (boto3 / google-cloud SDKs may be absent)
try:
    from kubently.modules.executor.cloud import CloudOpsManager
    CLOUD_AVAILABLE = True
except ImportError:
    CLOUD_AVAILABLE = False

from kubently.modules.executor.argocd import ArgoCDRunner
from kubently.modules.executor.helm import HelmRunner
from kubently.modules.executor.logsearch import LogSearchRunner
from kubently.modules.executor.loki import LokiRunner
from kubently.modules.executor.prometheus import PrometheusRunner

# Configure logging
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("kubently-sse-executor")


class SSEKubentlyExecutor:
    """Executor that uses Server-Sent Events for real-time command streaming."""

    def __init__(self):
        """Initialize SSE executor configuration."""
        # Required configuration
        self.api_url = os.environ.get("KUBENTLY_API_URL")
        self.cluster_id = os.environ.get("CLUSTER_ID")
        self.token = os.environ.get("KUBENTLY_TOKEN")

        if not all([self.api_url, self.cluster_id, self.token]):
            logger.error("Missing required environment variables")
            sys.exit(1)

        # TLS configuration
        self.verify_ssl = os.environ.get("KUBENTLY_SSL_VERIFY", "true").lower() == "true"
        self.ca_cert_path = os.environ.get("KUBENTLY_CA_CERT", None)

        # Capability reporting configuration (optional, disabled by default)
        # Enable with KUBENTLY_REPORT_CAPABILITIES=true
        self.report_capabilities = os.environ.get(
            "KUBENTLY_REPORT_CAPABILITIES", "false"
        ).lower() == "true"
        self.heartbeat_interval = int(os.environ.get("KUBENTLY_HEARTBEAT_INTERVAL", "300"))
        self.whitelist_config_path = os.environ.get(
            "KUBENTLY_WHITELIST_CONFIG", "/etc/kubently/whitelist.yaml"
        )

        # Track last heartbeat time
        self._last_heartbeat = 0

        # Load the command whitelist for enforcement (defense-in-depth on top of RBAC).
        # Always on when available; falls back to safe READ_ONLY defaults if no config file.
        self._whitelist = None
        if WHITELIST_AVAILABLE:
            try:
                self._whitelist = DynamicCommandWhitelist(config_path=self.whitelist_config_path)
            except Exception as e:
                logger.warning(f"Failed to load command whitelist ({e}); enforcement disabled")

        # Cloud read operations (workload identity; disabled by default).
        # KUBENTLY_CLOUD_MODE: "off" (default), "auto", "aws", or "gcp".
        # The pod's ambient identity (IRSA / EKS Pod Identity / GKE Workload
        # Identity) is the only credential source — no keys are configured here.
        self._cloud = None
        cloud_mode = os.environ.get("KUBENTLY_CLOUD_MODE", "off").lower()
        if cloud_mode != "off":
            if CLOUD_AVAILABLE:
                self._cloud = CloudOpsManager(
                    mode=cloud_mode,
                    aws_region=os.environ.get("KUBENTLY_CLOUD_AWS_REGION") or None,
                    gcp_project=os.environ.get("KUBENTLY_CLOUD_GCP_PROJECT") or None,
                    refresh_interval=int(
                        os.environ.get("KUBENTLY_CLOUD_REFRESH_INTERVAL", "3600")
                    ),
                )
                logger.info(f"Cloud operations enabled (mode: {cloud_mode})")
            else:
                logger.warning(
                    "KUBENTLY_CLOUD_MODE set but cloud module unavailable "
                    "(install boto3 / google-cloud SDKs); cloud ops disabled"
                )
        # The agent discovers cloud access through the capability report, so
        # cloud mode implies capability reporting.
        if self._cloud is not None and not self.report_capabilities:
            logger.info("Cloud mode enabled; turning on capability reporting")
            self.report_capabilities = True

        # Security validation: Warn if using HTTP in production
        if self.api_url.startswith("http://") and self.verify_ssl:
            logger.warning("⚠️  Using HTTP without TLS - this should only be used for local development!")
        elif self.api_url.startswith("https://"):
            logger.info("✅ Using HTTPS with TLS encryption")

        # Log search runs through the same kubectl runner as ordinary commands,
        # so the whitelist and read-only enforcement apply unchanged.
        self._logsearch = LogSearchRunner(kubectl_runner=self._run_kubectl)

        # Optional tool runners. Each is configured entirely from local env
        # (LOKI_URL, PROMETHEUS_URL, HELM_HISTORY_ENABLED, ARGOCD_URL/
        # ARGOCD_TOKEN) — the control plane never supplies URLs, credentials,
        # or raw argv. When unconfigured, their commands get a clear
        # "unavailable" error back.
        self._loki = LokiRunner()
        if self._loki.available:
            logger.info(f"Loki log search enabled: {self._loki.base_url}")
        self._prometheus = PrometheusRunner()
        if self._prometheus.available:
            logger.info(f"Prometheus tool enabled: {self._prometheus.base_url}")
        self._helm = HelmRunner()
        if self._helm.available:
            logger.info("Helm history tool enabled (read-only history/list)")
        self._argocd = ArgoCDRunner()
        if self._argocd.available:
            logger.info(f"ArgoCD tool enabled: {self._argocd.base_url}")

        # Command queue for processing
        self.command_queue = Queue()

        # Headers for authentication
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Cluster-ID": self.cluster_id,
        }

        logger.info(f"SSE executor initialized for cluster: {self.cluster_id}")
        if self.report_capabilities:
            logger.info(f"Capability reporting enabled (heartbeat every {self.heartbeat_interval}s)")

    def run(self) -> None:
        """
        Main executor loop.

        Starts SSE listener and command processor threads.
        If capability reporting is enabled, reports capabilities on startup.
        """
        logger.info("Starting SSE executor")

        # Report capabilities on startup (if enabled)
        # This is best-effort - failure doesn't prevent executor from running
        if self.report_capabilities:
            self._report_capabilities_on_startup()

        # Start command processor thread
        processor_thread = Thread(target=self._process_commands, daemon=True)
        processor_thread.start()

        # Run SSE listener (main thread)
        while True:
            try:
                self._connect_sse()
            except KeyboardInterrupt:
                logger.info("Executor stopped by user")
                sys.exit(0)
            except Exception as e:
                logger.error(f"SSE connection error: {e}")
                logger.info("Reconnecting in 5 seconds...")
                time.sleep(5)

    def _connect_sse(self) -> None:
        """
        Connect to SSE endpoint and listen for commands.
        """
        if sseclient is None:
            raise RuntimeError("sseclient is required to run the executor; install sseclient-py")

        url = f"{self.api_url}/executor/stream"
        logger.info(f"Connecting to SSE endpoint: {url}")

        # Configure TLS verification
        verify_setting = self.ca_cert_path if self.ca_cert_path else self.verify_ssl

        # Create SSE connection
        response = requests.get(url, headers=self.headers, stream=True, verify=verify_setting)

        if response.status_code != 200:
            raise Exception(f"Failed to connect: {response.status_code}")

        # Create SSE client
        client = sseclient.SSEClient(response)

        logger.info("SSE connection established")

        # Listen for events
        for event in client.events():
            try:
                if event.event == "connected":
                    data = json.loads(event.data)
                    logger.info(f"Connected to server: {data}")

                elif event.event == "command":
                    # Parse and queue command
                    command = json.loads(event.data)
                    logger.info(f"Received command: {command.get('id', 'unknown')}")
                    self.command_queue.put(command)

                elif event.event == "keepalive":
                    # Keepalive received, connection is healthy
                    logger.debug("Keepalive received")

                # Always check heartbeat on any event (not just keepalive)
                # This ensures high command traffic doesn't starve the heartbeat
                self._maybe_send_heartbeat()

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse event data: {e}")
            except Exception as e:
                logger.error(f"Error processing event: {e}")

    def _process_commands(self) -> None:
        """
        Process commands from the queue.

        Runs in a separate thread to avoid blocking SSE listener.
        """
        logger.info("Command processor started")

        while True:
            try:
                # Get command from queue (blocks until available)
                command = self.command_queue.get()

                # Execute command
                self._execute_command(command)

            except Exception as e:
                logger.error(f"Error processing command: {e}")

    def _execute_command(self, command: dict) -> None:
        """
        Execute a command and send result back.

        Args:
            command: Command dictionary with args and metadata
        """
        command_id = command.get("id", "unknown")
        logger.info(f"Executing command {command_id}")

        start_time = time.time()

        # Dispatch on the command's tool. kubectl is the default so command
        # envelopes from older API versions (no "tool" field) keep working.
        result = self._run_tool(command)

        # Add execution metadata
        result["command_id"] = command_id
        result["execution_time_ms"] = int((time.time() - start_time) * 1000)
        result["executed_at"] = time.time()

        # Send result back
        try:
            # Configure TLS verification
            verify_setting = self.ca_cert_path if self.ca_cert_path else self.verify_ssl

            response = requests.post(
                f"{self.api_url}/executor/results",
                json=result,
                headers=self.headers,
                timeout=10,
                verify=verify_setting,
            )

            if response.status_code != 200:
                logger.error(f"Failed to submit result: {response.status_code}")

        except Exception as e:
            logger.error(f"Failed to submit result for {command_id}: {e}")

    def _run_tool(self, command: dict) -> dict:
        """
        Route a command envelope to its tool runner.

        Each tool enforces its own allowlist locally: kubectl commands go
        through the DynamicCommandWhitelist; log searches compose only
        whitelist-checked `get pods` / `logs` invocations; loki and prometheus
        queries are limited to fixed read-only GET paths against the locally
        configured base URLs; helm is limited to read-only history/list
        subcommands built from validated fields; argocd is limited to
        read-only GET paths against the locally configured URL; cloud
        operations go through the cloud operation allowlist.
        """
        tool = command.get("tool", "kubectl")

        if tool == "kubectl":
            return self._run_kubectl(command.get("args", []))
        if tool == "log_search":
            return self._logsearch.run(command.get("request") or {})
        if tool == "loki":
            return self._loki.run(command.get("request") or {})
        if tool == "prometheus":
            return self._prometheus.run(command.get("request") or {})
        if tool == "helm":
            return self._helm.run(command.get("request") or {})
        if tool == "argocd":
            return self._argocd.run(command.get("request") or {})
        if tool == "cloud":
            return self._run_cloud_operation(command)

        logger.warning(f"Rejected command with unknown tool: {tool}")
        return {
            "success": False,
            "error": f"Unknown tool '{tool}'. This executor supports: kubectl, log_search, loki, prometheus, helm, argocd, cloud.",
            "status": "BLOCKED",
            "return_code": -1,
        }

    def _run_kubectl(self, args: list[str]) -> dict:
        """
        Execute kubectl command.

        Args:
            args: kubectl command arguments

        Returns:
            Result dictionary with output and status
        """
        # Enforce the whitelist before executing (defense-in-depth; RBAC is the backstop).
        if self._whitelist is not None:
            allowed, reason = self._whitelist.validate_command(args)
            if not allowed:
                logger.warning(f"Blocked by whitelist: {' '.join(args)} ({reason})")
                return {
                    "success": False,
                    "error": f"Blocked by whitelist: {reason}",
                    "status": "BLOCKED",
                    "return_code": -1,
                }

        try:
            # Prepend kubectl to args
            cmd = ["kubectl"] + args

            logger.debug(f"Running: {' '.join(cmd)}")

            # Execute command
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,  # 30 second timeout
            )

            # Combine stdout and stderr for output
            output = process.stdout
            if process.stderr:
                output += "\n" + process.stderr

            return {
                "success": process.returncode == 0,
                "output": output,
                "stdout": process.stdout,
                "stderr": process.stderr,
                "status": "SUCCESS" if process.returncode == 0 else "FAILED",
                "return_code": process.returncode,
            }

        except subprocess.TimeoutExpired:
            logger.error("Command timed out")
            return {
                "success": False,
                "error": "Command timed out",
                "status": "TIMEOUT",
                "return_code": -1,
            }

        except Exception as e:
            logger.error(f"Command execution failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "status": "ERROR",
                "return_code": -1,
            }

    def _run_cloud_operation(self, command: dict) -> dict:
        """
        Execute a whitelisted cloud read operation via the CloudOpsManager.

        Args:
            command: Command envelope whose "request" carries "operation" and
                "params" (top-level fallback accepted for older envelopes)

        Returns:
            Result dictionary in the same shape as _run_kubectl results,
            with the structured cloud payload JSON-encoded in "output".
        """
        request = command.get("request") or command
        operation = request.get("operation", "")
        params = request.get("params") or {}

        if self._cloud is None:
            return {
                "success": False,
                "error": (
                    "Cloud operations are not enabled on this executor "
                    "(set KUBENTLY_CLOUD_MODE and grant a workload identity)"
                ),
                "status": "ERROR",
                "return_code": -1,
            }

        # Allowlist enforcement happens inside the manager, before any SDK call
        result = self._cloud.execute(operation, params)
        payload = result.to_dict()

        if result.success:
            return {
                "success": True,
                "output": json.dumps(payload, default=str),
                "status": "SUCCESS",
                "return_code": 0,
            }
        return {
            "success": False,
            "output": json.dumps(payload, default=str),
            "error": result.error,
            "status": "BLOCKED" if result.error_code == "OPERATION_NOT_ALLOWED" else "FAILED",
            "return_code": -1,
        }

    # Capability Reporting Methods

    def _get_capabilities_payload(self) -> dict[str, Any]:
        """
        Gather capabilities from DynamicCommandWhitelist or use defaults.

        Returns:
            Dictionary with capability data for the API
        """
        # Reuse the whitelist loaded in __init__ (avoids a second config-watcher thread)
        payload = None
        if self._whitelist is not None:
            try:
                summary = self._whitelist.get_config_summary()
                payload = {
                    "mode": summary.get("mode", "readOnly"),
                    "allowed_verbs": summary.get("allowed_verbs", []),
                    "restricted_resources": list(summary.get("restricted_resources", [])),
                    "allowed_flags": list(summary.get("allowed_flags", [])),
                    "executor_version": os.environ.get("EXECUTOR_VERSION", "unknown"),
                    "executor_pod": os.environ.get("HOSTNAME", "unknown"),
                }
            except Exception as e:
                logger.warning(f"Failed to load whitelist config: {e}, using defaults")

        if payload is None:
            # Default capabilities (readOnly mode)
            payload = {
                "mode": "readOnly",
                "allowed_verbs": ["get", "describe", "logs", "top", "explain", "api-resources"],
                "restricted_resources": ["secrets", "configmaps"],
                "allowed_flags": ["--namespace", "--all-namespaces", "--selector"],
                "executor_version": os.environ.get("EXECUTOR_VERSION", "unknown"),
                "executor_pod": os.environ.get("HOSTNAME", "unknown"),
            }

        # Advertise cloud telemetry access (workload identity), if held.
        # capability_payload() returns None when no cloud identity is detected,
        # so the agent knows not to offer cloud tools for this cluster.
        if self._cloud is not None:
            try:
                cloud = self._cloud.capability_payload()
                if cloud:
                    payload["cloud"] = cloud
            except Exception as e:
                logger.warning(f"Cloud capability detection failed: {e}")

        return payload

    def _report_capabilities_on_startup(self) -> None:
        """
        Report capabilities to the API on startup.

        This is best-effort - if it fails, the executor continues normally.
        The API may not have the capability endpoint (older version), which is fine.
        """
        try:
            url = f"{self.api_url}/executor/capabilities"
            payload = self._get_capabilities_payload()

            logger.info(f"Reporting capabilities to {url}")
            logger.debug(f"Capability payload: {payload}")

            verify_setting = self.ca_cert_path if self.ca_cert_path else self.verify_ssl

            response = requests.post(
                url,
                json=payload,
                headers=self.headers,
                timeout=10,
                verify=verify_setting,
            )

            if response.status_code == 200:
                logger.info(f"Capabilities reported successfully (mode: {payload['mode']})")
                self._last_heartbeat = time.time()
            elif response.status_code == 404:
                # API doesn't have capability endpoint - older version
                logger.info("Capability endpoint not available (API may be older version), skipping")
                # Disable further reporting to avoid repeated 404s
                self.report_capabilities = False
            else:
                logger.warning(f"Capability report returned {response.status_code}: {response.text}")

        except requests.exceptions.ConnectionError:
            logger.warning("Could not connect to API for capability reporting, will retry on heartbeat")
        except Exception as e:
            logger.warning(f"Failed to report capabilities: {e}")

    def _send_heartbeat(self) -> None:
        """
        Send heartbeat to refresh capability TTL.

        Called periodically during SSE keepalive processing.
        """
        try:
            url = f"{self.api_url}/executor/heartbeat"
            verify_setting = self.ca_cert_path if self.ca_cert_path else self.verify_ssl

            response = requests.post(
                url,
                headers=self.headers,
                timeout=5,
                verify=verify_setting,
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "not_found":
                    # Capabilities expired - re-report them
                    logger.info("Capabilities expired, re-reporting...")
                    self._report_capabilities_on_startup()
                else:
                    logger.debug("Heartbeat sent successfully")
                    self._last_heartbeat = time.time()
            elif response.status_code == 404:
                # API doesn't have heartbeat endpoint - disable
                logger.debug("Heartbeat endpoint not available, disabling capability reporting")
                self.report_capabilities = False
            else:
                logger.debug(f"Heartbeat returned {response.status_code}")

        except Exception as e:
            logger.debug(f"Heartbeat failed: {e}")

    def _maybe_send_heartbeat(self) -> None:
        """
        Send heartbeat if enough time has passed since the last one.

        Called during SSE keepalive processing for efficiency.
        """
        if not self.report_capabilities:
            return

        # Periodic cloud identity/permission re-detection: when due, force a
        # fresh detection and re-report full capabilities (roles get scoped,
        # granted, and revoked in the customer's IAM at any time).
        if self._cloud is not None and self._cloud.refresh_due():
            try:
                self._cloud.detect(force=True)
            except Exception as e:
                logger.warning(f"Cloud identity re-detection failed: {e}")
            self._report_capabilities_on_startup()
            return

        current_time = time.time()
        if current_time - self._last_heartbeat >= self.heartbeat_interval:
            self._send_heartbeat()


def main():
    """Main entry point."""
    executor = SSEKubentlyExecutor()

    try:
        executor.run()
    except KeyboardInterrupt:
        logger.info("Agent stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
