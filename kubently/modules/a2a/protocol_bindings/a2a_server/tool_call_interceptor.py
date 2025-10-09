"""
Tool Call Interceptor for A2A Protocol

This module provides a way to intercept and log tool calls
made by the LangGraph agent for exposure via SSE events.
"""

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime
import asyncio
from collections import deque

logger = logging.getLogger(__name__)


class ToolCallInterceptor:
    """Captures tool calls made by the agent for SSE streaming."""
    
    def __init__(self, max_buffer_size: int = 100):
        """Initialize the interceptor.
        
        Args:
            max_buffer_size: Maximum number of tool calls to buffer
        """
        self.tool_calls = deque(maxlen=max_buffer_size)
        self._lock = asyncio.Lock()
        
    async def record_tool_call(
        self,
        tool_name: str,
        args: Dict[str, Any],
        thread_id: Optional[str] = None
    ) -> str:
        """Record a tool call.
        
        Args:
            tool_name: Name of the tool being called
            args: Arguments passed to the tool
            thread_id: Optional thread/context ID
            
        Returns:
            Tool call ID
        """
        async with self._lock:
            tool_call_id = f"tc_{datetime.now().timestamp()}"
            tool_call = {
                "id": tool_call_id,
                "tool_name": tool_name,
                "args": args,
                "thread_id": thread_id,
                "timestamp": datetime.now().isoformat(),
                "status": "started"
            }
            self.tool_calls.append(tool_call)
            logger.debug(f"Recorded tool call: {tool_call}")
            return tool_call_id
    
    async def record_tool_result(
        self,
        tool_call_id: str,
        result: Any,
        error: Optional[str] = None
    ):
        """Record the result of a tool call.
        
        Args:
            tool_call_id: ID of the tool call
            result: Result from the tool
            error: Optional error message
        """
        async with self._lock:
            # Find the tool call in our buffer
            for tool_call in self.tool_calls:
                if tool_call["id"] == tool_call_id:
                    tool_call["status"] = "error" if error else "completed"
                    tool_call["result"] = str(result)[:1000] if result else None
                    tool_call["error"] = error
                    tool_call["completed_at"] = datetime.now().isoformat()
                    logger.debug(f"Updated tool call result: {tool_call_id}")
                    break
    
    async def get_tool_calls_for_thread(
        self,
        thread_id: str,
        since_timestamp: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get all tool calls for a specific thread.
        
        Args:
            thread_id: Thread/context ID
            since_timestamp: Optional timestamp to filter calls after
            
        Returns:
            List of tool calls
        """
        async with self._lock:
            calls = [
                tc for tc in self.tool_calls
                if tc.get("thread_id") == thread_id
            ]
            
            if since_timestamp:
                calls = [
                    tc for tc in calls
                    if tc.get("timestamp", "") > since_timestamp
                ]
            
            return list(calls)
    
    def clear(self):
        """Clear all recorded tool calls."""
        self.tool_calls.clear()


# Global interceptor instance
_interceptor = ToolCallInterceptor()


def get_tool_call_interceptor() -> ToolCallInterceptor:
    """Get the global tool call interceptor instance."""
    return _interceptor