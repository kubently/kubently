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

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from queue import Queue
from threading import Thread
from typing import Any, Dict, List, Optional

import httpx
import requests
import sseclient

# Optional import - DynamicCommandWhitelist may not be available in all deployments
try:
    from kubently.modules.executor.dynamic_whitelist import DynamicCommandWhitelist
    WHITELIST_AVAILABLE = True
except ImportError:
    WHITELIST_AVAILABLE = False

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

        # Security validation: Warn if using HTTP in production
        if self.api_url.startswith("http://") and self.verify_ssl:
            logger.warning("⚠️  Using HTTP without TLS - this should only be used for local development!")
        elif self.api_url.startswith("https://"):
            logger.info("✅ Using HTTPS with TLS encryption")

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

    def _execute_command(self, command: Dict) -> None:
        """
        Execute a command and send result back.

        Args:
            command: Command dictionary with args and metadata
        """
        command_id = command.get("id", "unknown")
        logger.info(f"Executing command {command_id}")

        start_time = time.time()

        # Execute kubectl command
        result = self._run_kubectl(command.get("args", []))

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

    def _run_kubectl(self, args: List[str]) -> Dict:
        """
        Execute kubectl command.

        Args:
            args: kubectl command arguments

        Returns:
            Result dictionary with output and status
        """
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

    # Capability Reporting Methods

    def _get_capabilities_payload(self) -> Dict[str, Any]:
        """
        Gather capabilities from DynamicCommandWhitelist or use defaults.

        Returns:
            Dictionary with capability data for the API
        """
        # Try to use DynamicCommandWhitelist if available
        if WHITELIST_AVAILABLE:
            try:
                whitelist = DynamicCommandWhitelist(config_path=self.whitelist_config_path)
                summary = whitelist.get_config_summary()
                return {
                    "mode": summary.get("mode", "readOnly"),
                    "allowed_verbs": summary.get("allowed_verbs", []),
                    "restricted_resources": list(summary.get("restricted_resources", [])),
                    "allowed_flags": list(summary.get("allowed_flags", [])),
                    "executor_version": os.environ.get("EXECUTOR_VERSION", "unknown"),
                    "executor_pod": os.environ.get("HOSTNAME", "unknown"),
                }
            except Exception as e:
                logger.warning(f"Failed to load whitelist config: {e}, using defaults")

        # Default capabilities (readOnly mode)
        return {
            "mode": "readOnly",
            "allowed_verbs": ["get", "describe", "logs", "top", "explain", "api-resources"],
            "restricted_resources": ["secrets", "configmaps"],
            "allowed_flags": ["--namespace", "--all-namespaces", "--selector"],
            "executor_version": os.environ.get("EXECUTOR_VERSION", "unknown"),
            "executor_pod": os.environ.get("HOSTNAME", "unknown"),
        }

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
