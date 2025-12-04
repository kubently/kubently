"""
Capability Module - Black Box Interface

Purpose: Store and retrieve executor capability advertisements
Interface: store_capabilities(), get_capabilities(), refresh_ttl()
Hidden: Redis storage, TTL management, serialization

Provides graceful degradation - missing capabilities don't break functionality.
Executors report what they can do; agent uses this for better UX but doesn't
require it for operation.
"""

from .capability import CapabilityModule, ExecutorCapabilities

__all__ = ["CapabilityModule", "ExecutorCapabilities"]
