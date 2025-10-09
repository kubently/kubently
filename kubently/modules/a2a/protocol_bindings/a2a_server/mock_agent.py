"""Mock Kubently Agent for testing without LLM"""

import json
import logging
import subprocess
from typing import Any, Dict

logger = logging.getLogger(__name__)


class MockKubentlyAgent:
    """Mock agent that directly executes kubectl commands for testing."""

    SUPPORTED_CONTENT_TYPES = ["text/plain"]

    def __init__(self):
        """Initialize mock agent."""
        logger.info("MockKubentlyAgent initialized")

    async def handle_message(self, messages: list) -> Dict[str, Any]:
        """Handle incoming messages and execute kubectl commands."""
        if not messages:
            return {"error": "No messages provided"}

        # Get the last user message
        user_message = None
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", {})
                if isinstance(content, dict):
                    user_message = content.get("text", "")
                else:
                    user_message = str(content)
                break

        if not user_message:
            return {"error": "No user message found"}

        # Simple keyword-based command mapping
        response = self._process_query(user_message)

        return {"messages": [{"role": "assistant", "content": {"type": "text", "text": response}}]}

    def _process_query(self, query: str) -> str:
        """Process query and execute kubectl commands."""
        query_lower = query.lower()

        try:
            # Map queries to kubectl commands
            if "pods" in query_lower and "all namespace" in query_lower:
                cmd = ["kubectl", "get", "pods", "--all-namespaces"]
            elif "pods" in query_lower and "kubently" in query_lower:
                cmd = ["kubectl", "get", "pods", "-n", "kubently"]
            elif "logs" in query_lower and "redis" in query_lower:
                cmd = ["kubectl", "logs", "-n", "kubently", "deployment/redis", "--tail=10"]
            elif "describe" in query_lower and "kubently-api" in query_lower:
                cmd = ["kubectl", "describe", "deployment", "kubently-api", "-n", "kubently"]
            elif "services" in query_lower:
                namespace = "kubently" if "kubently" in query_lower else None
                cmd = ["kubectl", "get", "services"]
                if namespace:
                    cmd.extend(["-n", namespace])
            elif "deployment" in query_lower:
                namespace = "kubently" if "kubently" in query_lower else None
                cmd = ["kubectl", "get", "deployments"]
                if namespace:
                    cmd.extend(["-n", namespace])
            else:
                return f"I understand you want to know about '{query}', but I can only execute specific kubectl commands in test mode. Try asking about pods, services, deployments, or logs."

            # Execute kubectl command
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

            if result.returncode == 0:
                output = result.stdout
                if not output:
                    output = "Command executed successfully but returned no output."
                return f"Here's the output of '{' '.join(cmd)}':\n\n{output}"
            else:
                return f"Error executing command: {result.stderr}"

        except subprocess.TimeoutExpired:
            return "Command timed out after 10 seconds"
        except Exception as e:
            logger.error(f"Error processing query: {e}")
            return f"Error processing query: {str(e)}"
