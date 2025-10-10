"""
Custom logging configuration to suppress health check logs
"""

import logging
import logging.config
from typing import Dict, Any


class HealthCheckFilter(logging.Filter):
    """Filter to suppress health check endpoint logs."""
    
    def filter(self, record: logging.LogRecord) -> bool:
        """Filter out health check requests from uvicorn access logs."""
        # Check if this is a uvicorn access log
        if record.name == "uvicorn.access":
            # Check if the message contains health check endpoint
            message = record.getMessage()
            if "/health" in message and "GET" in message:
                return False  # Suppress health check logs
        return True  # Allow all other logs


def get_logging_config() -> Dict[str, Any]:
    """Get logging configuration with health check suppression."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "health_check_filter": {
                "()": HealthCheckFilter
            }
        },
        "formatters": {
            "default": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            },
            "access": {
                "format": "%(message)s"
            }
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stdout"
            },
            "access": {
                "class": "logging.StreamHandler",
                "formatter": "access",
                "stream": "ext://sys.stdout",
                "filters": ["health_check_filter"]  # Apply filter to access logs
            }
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["default"],
                "level": "INFO",
                "propagate": False
            },
            "uvicorn.error": {
                "handlers": ["default"],
                "level": "INFO",
                "propagate": False
            },
            "uvicorn.access": {
                "handlers": ["access"],
                "level": "INFO",
                "propagate": False
            },
            "kubently": {
                "handlers": ["default"],
                "level": "INFO",
                "propagate": False
            }
        },
        "root": {
            "level": "INFO",
            "handlers": ["default"]
        }
    }