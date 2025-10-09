"""
Session Module - Black Box Interface

Purpose: Manage debugging session lifecycle
Interface: create_session(), get_session(), end_session()
Hidden: Session storage, TTL management, state transitions

Replaceable with any session backend (database, in-memory, distributed cache).
"""

from .session import SessionModule

__all__ = ["SessionModule"]
