"""Request-scoped auth context.

current_api_key carries the caller's validated API key from the auth layer into
downstream code (e.g. agent tools making internal HTTP calls), so those calls can
execute with the caller's privileges instead of a shared internal key. ContextVars
propagate through asyncio.create_task, so background work spawned from a request
inherits the caller's key automatically.
"""

from contextvars import ContextVar
from typing import Optional

current_api_key: ContextVar[Optional[str]] = ContextVar("current_api_key", default=None)
