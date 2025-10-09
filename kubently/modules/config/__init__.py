"""
Config Module - Black Box Interface

Purpose: Application configuration management
Interface: get_config(), set_config(), load_from_env()
Hidden: Config sources, validation logic, environment parsing

Can be replaced with different config systems (Consul, etcd, AWS Parameter Store).
"""

import os
from typing import Any, Dict


# Configuration Contract: Required and Optional Keys
# This defines the black box interface - what the config module guarantees to provide

REQUIRED_CONFIG_KEYS = {
    "redis_host": "Redis server hostname",
    "redis_port": "Redis server port number",
    "redis_db": "Redis database number",
    "host": "API server bind address",
    "port": "API server port",
    "log_level": "Logging level (DEBUG, INFO, WARNING, ERROR)",
    "session_ttl": "Session time-to-live in seconds",
    "command_timeout": "Command execution timeout in seconds",
    "max_commands_per_fetch": "Maximum commands to fetch per executor poll",
}

OPTIONAL_CONFIG_KEYS = {
    "redis_password": {
        "description": "Redis authentication password",
        "default": None,
    },
    "debug": {
        "description": "Enable debug mode",
        "default": False,
    },
    "a2a_external_url": {
        "description": "External URL for A2A agent card (e.g., https://api.example.com/a2a/)",
        "default": None,  # Computed from host:port if not provided
    },
}


class ConfigModule:
    """Configuration management module."""

    def __init__(self):
        """Initialize with environment variables."""
        self._config = self._load_from_env()
        self._validate_required_keys()

    def _validate_required_keys(self) -> None:
        """
        Validate that all required configuration keys are present.

        Raises:
            ValueError: If required keys are missing
        """
        missing_keys = []
        for key in REQUIRED_CONFIG_KEYS:
            if key not in self._config or self._config[key] is None:
                missing_keys.append(key)

        if missing_keys:
            raise ValueError(
                f"Missing required configuration keys: {', '.join(missing_keys)}. "
                f"Check environment variables and deployment configuration."
            )

    def _load_from_env(self) -> Dict[str, Any]:
        """Load configuration from environment."""
        # Parse Redis port (might be in tcp://host:port format from K8s)
        redis_port_env = os.getenv("REDIS_PORT", "6379")
        if redis_port_env.startswith("tcp://"):
            # Extract port from tcp://host:port format
            redis_port = int(redis_port_env.split(":")[-1])
        else:
            redis_port = int(redis_port_env)

        return {
            # Redis settings
            "redis_host": os.getenv("REDIS_HOST", "kubently-redis-master"),
            "redis_port": redis_port,
            "redis_db": int(os.getenv("REDIS_DB", "0")),
            "redis_password": os.getenv("REDIS_PASSWORD"),  # Optional: for authenticated Redis
            # API settings
            "host": os.getenv("API_HOST", "0.0.0.0"),
            "port": int(os.getenv("API_PORT", "8080")),
            "log_level": os.getenv("LOG_LEVEL", "INFO"),
            "debug": os.getenv("DEBUG", "false").lower() == "true",
            # Session settings
            "session_ttl": int(os.getenv("SESSION_TTL", "3600")),
            "command_timeout": int(os.getenv("COMMAND_TIMEOUT", "30")),
            "max_commands_per_fetch": int(os.getenv("MAX_COMMANDS_PER_FETCH", "10")),
            # A2A settings (A2A is core functionality, not optional)
            "a2a_external_url": os.getenv(
                "A2A_EXTERNAL_URL"
            ),  # Optional: external URL for A2A agent card
        }

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set configuration value."""
        self._config[key] = value

    def get_all(self) -> Dict[str, Any]:
        """Get all configuration values."""
        return self._config.copy()

    @staticmethod
    def get_config_schema() -> Dict[str, Any]:
        """
        Get the configuration schema (contract) for this module.

        This documents the black box interface - what keys are required,
        what keys are optional, and what their purposes are.

        Returns:
            Dictionary with 'required' and 'optional' key specifications

        Example:
            >>> schema = ConfigModule.get_config_schema()
            >>> print(schema['required']['redis_host'])
            'Redis server hostname'
        """
        return {
            "required": REQUIRED_CONFIG_KEYS.copy(),
            "optional": OPTIONAL_CONFIG_KEYS.copy(),
        }


# Singleton instance
_instance = None


def get_config() -> ConfigModule:
    """Get the configuration module singleton."""
    global _instance
    if _instance is None:
        _instance = ConfigModule()
    return _instance


# Public prompt loader interface
from .prompts import get_prompt

__all__ = ["get_config", "ConfigModule", "get_prompt"]
