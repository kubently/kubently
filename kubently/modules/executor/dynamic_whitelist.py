#!/usr/bin/env python3
"""
Dynamic Command Whitelist System for Kubently Agent.

Provides configurable, per-deployment command whitelist with hot-reloading
and multiple security modes.
"""

import hashlib
import logging
import os
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

logger = logging.getLogger("kubently-agent.dynamic-whitelist")


class SecurityMode(Enum):
    """Security modes for command validation."""

    READ_ONLY = "readOnly"
    EXTENDED_READ_ONLY = "extendedReadOnly"
    FULL_ACCESS = "fullAccess"


class WhitelistConfig:
    """Container for whitelist configuration."""

    def __init__(self, config_dict: Dict[str, Any]):
        """Initialize from configuration dictionary."""
        self.mode = SecurityMode(config_dict.get("mode", "readOnly"))

        commands = config_dict.get("commands", {})
        self.allowed_verbs = set(commands.get("allowedVerbs", []))
        self.restricted_resources = set(commands.get("restrictedResources", []))
        self.allowed_flags = set(commands.get("allowedFlags", []))
        self.forbidden_patterns = set(commands.get("forbiddenPatterns", []))

        limits = config_dict.get("limits", {})
        self.max_arguments = limits.get("maxArguments", 20)
        self.timeout_seconds = limits.get("timeoutSeconds", 30)

        self.reload_interval = config_dict.get("reloadIntervalSeconds", 30)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "mode": self.mode.value,
            "commands": {
                "allowedVerbs": list(self.allowed_verbs),
                "restrictedResources": list(self.restricted_resources),
                "allowedFlags": list(self.allowed_flags),
                "forbiddenPatterns": list(self.forbidden_patterns),
            },
            "limits": {
                "maxArguments": self.max_arguments,
                "timeoutSeconds": self.timeout_seconds,
            },
            "reloadIntervalSeconds": self.reload_interval,
        }


class DynamicCommandWhitelist:
    """Dynamic command whitelist with hot-reloading capability."""

    # Immutable security baseline - never allow these patterns
    IMMUTABLE_FORBIDDEN_PATTERNS = {
        # Authentication bypass attempts
        "--token",
        "--kubeconfig",
        "--server",
        "--insecure",
        "--username",
        "--password",
        "--client-key",
        "--client-certificate",
        # Write operations (immutable in all modes)
        "delete",
        "apply",
        "create",
        "patch",
        "replace",
        "edit",
        "scale",
        # Shell injection risks
        "&&",
        "||",
        ";",
        "|",
        "`",
        "$(",
        "${",
        # File system access
        "--kubeconfig=",
        "--token-file",
        "/etc/kubernetes",
    }

    # Default configurations per security mode
    MODE_DEFAULTS = {
        SecurityMode.READ_ONLY: {
            "allowedVerbs": {
                "get",
                "describe",
                "logs",
                "top",
                "explain",
                "api-resources",
                "api-versions",
                "events",
                "version",
            },
            "restrictedResources": {"secrets", "configmaps"},
            "allowedFlags": {
                "--namespace",
                "--all-namespaces",
                "--selector",
                "--show-labels",
                "--watch",
                "--follow",
                "--previous",
            },
        },
        SecurityMode.EXTENDED_READ_ONLY: {
            "allowedVerbs": {
                "get",
                "describe",
                "logs",
                "top",
                "explain",
                "api-resources",
                "api-versions",
                "events",
                "version",
                "port-forward",
                "exec",
            },
            "restrictedResources": {"secrets"},
            "allowedFlags": {
                "--namespace",
                "--all-namespaces",
                "--selector",
                "--show-labels",
                "--watch",
                "--follow",
                "--previous",
                "--port",
                "--stdin",
                "--tty",
                "--container",
            },
        },
        SecurityMode.FULL_ACCESS: {
            "allowedVerbs": {
                "get",
                "describe",
                "logs",
                "top",
                "explain",
                "api-resources",
                "api-versions",
                "events",
                "version",
                "port-forward",
                "exec",
                "cp",
                "proxy",
                "attach",
                "run",
            },
            "restrictedResources": set(),
            "allowedFlags": {
                "--namespace",
                "--all-namespaces",
                "--selector",
                "--show-labels",
                "--watch",
                "--follow",
                "--previous",
                "--port",
                "--stdin",
                "--tty",
                "--container",
                "--server-print",
                "--server-version",
            },
        },
    }

    def __init__(self, config_path: str = "/etc/kubently/whitelist.yaml"):
        """
        Initialize dynamic whitelist.

        Args:
            config_path: Path to configuration file
        """
        self.config_path = Path(config_path)
        self.current_config: Optional[WhitelistConfig] = None
        self.last_modified: Optional[float] = None
        self.config_hash: Optional[str] = None
        self._lock = threading.RLock()
        self._watcher_thread: Optional[threading.Thread] = None
        self._stop_watcher = threading.Event()

        # Load initial configuration
        self._load_config()

        # Start configuration watcher
        self._start_watcher()

    def _start_watcher(self) -> None:
        """Start background thread to watch for configuration changes."""
        if self._watcher_thread and self._watcher_thread.is_alive():
            return

        self._stop_watcher.clear()
        self._watcher_thread = threading.Thread(
            target=self._watch_config, daemon=True, name="config-watcher"
        )
        self._watcher_thread.start()
        logger.info("Configuration watcher started")

    def _watch_config(self) -> None:
        """Background thread to watch for configuration changes."""
        while not self._stop_watcher.is_set():
            try:
                reload_interval = self.current_config.reload_interval if self.current_config else 30
                time.sleep(reload_interval)

                if self._config_changed():
                    logger.info("Configuration change detected, reloading...")
                    self._load_config()

            except Exception as e:
                logger.error(f"Error in config watcher: {e}")

    def _config_changed(self) -> bool:
        """Check if configuration file has changed."""
        try:
            if not self.config_path.exists():
                return False

            current_mtime = self.config_path.stat().st_mtime
            if self.last_modified and current_mtime <= self.last_modified:
                return False

            # Check content hash to avoid false positives
            with open(self.config_path, "rb") as f:
                content_hash = hashlib.sha256(f.read()).hexdigest()

            if self.config_hash and content_hash == self.config_hash:
                return False

            return True

        except Exception as e:
            logger.error(f"Error checking config changes: {e}")
            return False

    def _load_config(self) -> None:
        """Load configuration from file."""
        try:
            if not self.config_path.exists():
                logger.warning(f"Config file not found: {self.config_path}, using defaults")
                self._use_defaults()
                return

            with open(self.config_path, "r") as f:
                config_data = yaml.safe_load(f)

            if not self._validate_config(config_data):
                logger.error("Invalid configuration, keeping previous config")
                if not self.current_config:
                    self._use_defaults()
                return

            # Apply mode defaults first
            mode = SecurityMode(config_data.get("mode", "readOnly"))
            merged_config = self._merge_with_defaults(config_data, mode)

            # Create new config
            new_config = WhitelistConfig(merged_config)

            # Update state
            with self._lock:
                self.current_config = new_config
                self.last_modified = self.config_path.stat().st_mtime
                with open(self.config_path, "rb") as f:
                    self.config_hash = hashlib.sha256(f.read()).hexdigest()

            logger.info(f"Configuration loaded successfully (mode: {new_config.mode.value})")

        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            if not self.current_config:
                self._use_defaults()

    def _merge_with_defaults(self, config_data: Dict, mode: SecurityMode) -> Dict:
        """Merge user configuration with mode defaults."""
        defaults = self.MODE_DEFAULTS.get(mode, self.MODE_DEFAULTS[SecurityMode.READ_ONLY])

        # Start with mode defaults
        merged = {
            "mode": mode.value,
            "commands": {
                "allowedVerbs": list(defaults["allowedVerbs"]),
                "restrictedResources": list(defaults["restrictedResources"]),
                "allowedFlags": list(defaults["allowedFlags"]),
                "forbiddenPatterns": list(self.IMMUTABLE_FORBIDDEN_PATTERNS),
            },
            "limits": {
                "maxArguments": 20,
                "timeoutSeconds": 30,
            },
            "reloadIntervalSeconds": 30,
        }

        # Merge user configuration
        if "commands" in config_data:
            commands = config_data["commands"]

            # Add custom verbs
            if "allowedVerbs" in commands:
                merged["commands"]["allowedVerbs"] = list(
                    set(merged["commands"]["allowedVerbs"]) | set(commands["allowedVerbs"])
                )

            # Add custom flags
            if "allowedFlags" in commands:
                merged["commands"]["allowedFlags"] = list(
                    set(merged["commands"]["allowedFlags"]) | set(commands["allowedFlags"])
                )

            # Add extra forbidden patterns
            if "forbiddenPatterns" in commands:
                merged["commands"]["forbiddenPatterns"] = list(
                    set(merged["commands"]["forbiddenPatterns"])
                    | set(commands["forbiddenPatterns"])
                )

            # Override restricted resources if specified
            if "restrictedResources" in commands:
                merged["commands"]["restrictedResources"] = commands["restrictedResources"]

        # Merge limits
        if "limits" in config_data:
            merged["limits"].update(config_data["limits"])

        # Merge reload interval
        if "reloadIntervalSeconds" in config_data:
            merged["reloadIntervalSeconds"] = config_data["reloadIntervalSeconds"]

        return merged

    def _use_defaults(self) -> None:
        """Use hardcoded safe defaults."""
        default_config = {
            "mode": "readOnly",
            "commands": {
                "allowedVerbs": list(self.MODE_DEFAULTS[SecurityMode.READ_ONLY]["allowedVerbs"]),
                "restrictedResources": list(
                    self.MODE_DEFAULTS[SecurityMode.READ_ONLY]["restrictedResources"]
                ),
                "allowedFlags": list(self.MODE_DEFAULTS[SecurityMode.READ_ONLY]["allowedFlags"]),
                "forbiddenPatterns": list(self.IMMUTABLE_FORBIDDEN_PATTERNS),
            },
            "limits": {
                "maxArguments": 20,
                "timeoutSeconds": 30,
            },
            "reloadIntervalSeconds": 30,
        }

        with self._lock:
            self.current_config = WhitelistConfig(default_config)

        logger.info("Using default safe configuration")

    def _validate_config(self, config: Dict) -> bool:
        """
        Validate configuration against security policies.

        Args:
            config: Configuration dictionary

        Returns:
            True if configuration is valid
        """
        try:
            # Validate mode
            mode_str = config.get("mode", "readOnly")
            try:
                mode = SecurityMode(mode_str)
            except ValueError:
                logger.error(f"Invalid security mode: {mode_str}")
                return False

            # Get commands config
            commands = config.get("commands", {})
            allowed_verbs = set(commands.get("allowedVerbs", []))

            # Ensure no forbidden verbs in allowed list
            forbidden_verbs = {"delete", "apply", "create", "patch", "replace", "edit"}
            if allowed_verbs & forbidden_verbs:
                logger.error(
                    f"Forbidden verbs found in allowedVerbs: {allowed_verbs & forbidden_verbs}"
                )
                return False

            # Validate mode restrictions
            if mode == SecurityMode.READ_ONLY:
                dangerous_verbs = {"exec", "port-forward", "proxy", "cp", "attach", "run"}
                if allowed_verbs & dangerous_verbs:
                    logger.error(
                        f"Dangerous verbs not allowed in readOnly mode: {allowed_verbs & dangerous_verbs}"
                    )
                    return False

            # Validate limits
            limits = config.get("limits", {})
            max_args = limits.get("maxArguments", 20)
            if not isinstance(max_args, int) or max_args < 1 or max_args > 100:
                logger.error(f"Invalid maxArguments: {max_args}")
                return False

            timeout = limits.get("timeoutSeconds", 30)
            if not isinstance(timeout, (int, float)) or timeout < 1 or timeout > 300:
                logger.error(f"Invalid timeoutSeconds: {timeout}")
                return False

            return True

        except Exception as e:
            logger.error(f"Config validation error: {e}")
            return False

    def validate_command(self, args: List[str]) -> tuple[bool, Optional[str]]:
        """
        Validate kubectl command against dynamic whitelist.

        Args:
            args: kubectl command arguments

        Returns:
            Tuple of (is_valid, rejection_reason)
        """
        with self._lock:
            if not self.current_config:
                return False, "No configuration loaded"

            config = self.current_config

        # Check argument count
        if len(args) > config.max_arguments:
            return False, f"Too many arguments (max: {config.max_arguments})"

        if not args:
            return False, "No command specified"

        # Check verb
        verb = args[0]
        if verb not in config.allowed_verbs:
            return False, f"Verb '{verb}' not allowed in {config.mode.value} mode"

        # Check for immutable forbidden patterns
        for arg in args:
            arg_lower = arg.lower()
            for pattern in self.IMMUTABLE_FORBIDDEN_PATTERNS:
                if pattern in arg_lower:
                    return False, f"Forbidden pattern '{pattern}' detected"

        # Check for configured forbidden patterns
        for arg in args:
            arg_lower = arg.lower()
            for pattern in config.forbidden_patterns:
                if pattern in arg_lower:
                    return False, f"Forbidden pattern '{pattern}' detected"

        # Check resource restrictions
        if config.restricted_resources and len(args) > 1:
            for resource in config.restricted_resources:
                if resource in args[1].lower():
                    return False, f"Access to resource '{resource}' is restricted"

        # Check flags
        for arg in args[1:]:
            if arg.startswith("-"):
                flag_base = arg.split("=")[0]
                if flag_base not in config.allowed_flags:
                    # Check if it's a forbidden flag
                    for forbidden in self.IMMUTABLE_FORBIDDEN_PATTERNS:
                        if forbidden in flag_base:
                            return False, f"Forbidden flag '{flag_base}' detected"

        return True, None

    def get_timeout(self) -> int:
        """Get configured command timeout."""
        with self._lock:
            if self.current_config:
                return self.current_config.timeout_seconds
            return 30

    def get_config_summary(self) -> Dict[str, Any]:
        """Get current configuration summary."""
        with self._lock:
            if not self.current_config:
                return {"status": "no_config"}

            return {
                "status": "active",
                "mode": self.current_config.mode.value,
                "allowed_verbs_count": len(self.current_config.allowed_verbs),
                "allowed_verbs": sorted(self.current_config.allowed_verbs),
                "config_path": str(self.config_path),
                "last_modified": self.last_modified,
            }

    def stop(self) -> None:
        """Stop the configuration watcher."""
        self._stop_watcher.set()
        if self._watcher_thread:
            self._watcher_thread.join(timeout=5)
