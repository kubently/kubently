"""
A2A Module - Black Box Interface

Purpose: Enable agent-to-agent communication for multi-agent systems
Interface: A2A protocol server on port 8000
Hidden: Protocol handling, tool mapping, LLM integration

Can be disabled or replaced with different protocol implementations.
Runs in same process but maintains complete separation.
"""

import asyncio
import logging
from threading import Thread
from typing import Optional

logger = logging.getLogger(__name__)

# Try to import only the lightweight A2A server primitives at module import time.
# Heavy dependencies (LangChain, LLMs, etc.) will be imported lazily inside get_app().
try:
    import uvicorn
    from a2a.server.apps import A2AStarletteApplication
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.tasks import InMemoryTaskStore, PushNotificationSender
    from a2a.types import AgentCapabilities, AgentCard, AgentSkill, Task

    A2A_AVAILABLE = True
except Exception as e:  # Broad catch to avoid breaking the main API on optional features
    A2A_AVAILABLE = False
    logger.info(f"A2A support disabled at import time: {e}")


class SimplePushNotificationSender(PushNotificationSender):
    """Simple push notification sender."""

    async def send_notification(self, task: Task) -> None:
        """Log notifications for debugging."""
        logger.debug(f"Push notification for task {task.id}: {task.status.state}")


class A2AModule:
    """A2A server module that runs alongside the main API."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8000, external_url: str = None, redis_client=None):
        """Initialize the A2A server."""
        if not A2A_AVAILABLE:
            raise ImportError("A2A dependencies not installed")

        self.host = host
        self.port = port
        self.external_url = external_url or f"http://{host}:{port}/"
        self.redis_client = redis_client
        self.server = None
        self.thread = None
        self._app = None  # Cache for FastAPI sub-application

    def _lazy_imports(self):
        """Import heavy A2A bindings lazily to reduce startup memory/fragility."""
        # Import inside method to avoid module-level import failures/OOM
        from .protocol_bindings.a2a_server.agent import KubentlyAgent  # noqa: F401
        from .protocol_bindings.a2a_server.agent_executor import KubentlyAgentExecutor

        return KubentlyAgentExecutor

    def get_agent_card(self) -> AgentCard:
        """Create the agent card for Kubently."""
        # Lazy import to access SUPPORTED_CONTENT_TYPES only when needed
        from .protocol_bindings.a2a_server.agent import KubentlyAgent

        capabilities = AgentCapabilities(streaming=True, pushNotifications=False)

        skill = AgentSkill(
            id="kubernetes-debug",
            name="Kubernetes Debugging",
            description=(
                "Execute read-only kubectl commands across registered clusters. "
                "Troubleshoot pods, services, deployments, and other resources."
            ),
            tags=["kubernetes", "k8s", "debugging", "kubectl", "troubleshooting"],
            examples=[
                "Show me all failing pods",
                "Get logs for nginx deployment",
                "Debug crashlooping pod",
                "List services in namespace",
            ],
        )

        return AgentCard(
            name="Kubently Kubernetes Debugger",
            description="AI agent for debugging Kubernetes clusters through kubectl commands",
            url=self.external_url,
            version="1.0.0",
            defaultInputModes=KubentlyAgent.SUPPORTED_CONTENT_TYPES,
            defaultOutputModes=KubentlyAgent.SUPPORTED_CONTENT_TYPES,
            capabilities=capabilities,
            skills=[skill],
        )

    def get_mount_config(self) -> tuple[str, "FastAPI"]:
        """
        Get the mount configuration for integrating A2A into the main API.

        This method encapsulates all knowledge about how A2A should be mounted,
        keeping the orchestration layer (main.py) from knowing implementation details.

        Returns:
            Tuple of (mount_path, fastapi_app) ready to use with app.mount()

        Example:
            >>> a2a_server = create_a2a_server(...)
            >>> path, app = a2a_server.get_mount_config()
            >>> main_app.mount(path, app)
        """
        return ("/a2a", self.get_app())

    def get_app(self):
        """Get FastAPI sub-application for mounting under main API."""
        if self._app is None:
            # Lazily import heavy executor only when constructing the app
            KubentlyAgentExecutor = self._lazy_imports()

            # Create request handler
            agent_executor = KubentlyAgentExecutor(redis_client=self.redis_client)
            # Don't initialize here - let it initialize in the correct event loop
            # asyncio.run(agent_executor.initialize())
            
            # Log executor creation
            logger.info(f"Created KubentlyAgentExecutor: {agent_executor}")
            
            # Use DefaultRequestHandler for now
            request_handler = DefaultRequestHandler(
                agent_executor=agent_executor,
                task_store=InMemoryTaskStore(),
                push_sender=SimplePushNotificationSender(),
            )
            
            logger.info(f"Created DefaultRequestHandler with executor: {request_handler}")

            # Create A2A application
            a2a_app = A2AStarletteApplication(
                agent_card=self.get_agent_card(), http_handler=request_handler
            )

            # Build and cache the FastAPI app
            self._app = a2a_app.build()
            
            # Add authentication middleware using the reusable middleware module
            if self.redis_client:
                from kubently.modules.auth import AuthModule
                from kubently.modules.middleware import create_api_key_middleware
                
                # Create auth module and middleware
                auth_module = AuthModule(self.redis_client)
                auth_middleware = create_api_key_middleware(
                    auth_module=auth_module,
                    skip_paths={"/": ["GET"]},  # Skip auth for agent card endpoint
                    error_format="jsonrpc"  # Use JSON-RPC error format for A2A
                )
                
                # Register middleware with the app
                @self._app.middleware("http")
                async def authentication_middleware(request, call_next):
                    return await auth_middleware(request, call_next)
                
                logger.info("A2A FastAPI sub-application created with authentication middleware")
            else:
                logger.warning("A2A app created without authentication (no Redis client)")

        return self._app

    def run_server(self):
        """Run the A2A server in a separate thread."""
        # Create new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Get the FastAPI app (creates it if needed)
        app = self.get_app()

        # Configure uvicorn
        config = uvicorn.Config(
            app,
            host=self.host,
            port=self.port,
            log_level="info",
            access_log=False,  # Reduce noise in logs
        )

        self.server = uvicorn.Server(config)

        logger.info(f"Starting A2A server on {self.host}:{self.port}")
        loop.run_until_complete(self.server.serve())

    def start(self):
        """Start the A2A server in background."""
        if self.thread is None or not self.thread.is_alive():
            self.thread = Thread(target=self.run_server, daemon=True)
            self.thread.start()
            logger.info("A2A server started in background")

    def stop(self):
        """Stop the A2A server."""
        if self.server:
            self.server.should_exit = True
            logger.info("A2A server stopped")


# Module interface
def create_a2a_server(
    host: str = "0.0.0.0", 
    port: int = 8000, 
    external_url: str = None,
    redis_client=None
) -> Optional["A2AModule"]:
    """Create A2A server if dependencies are available."""
    if not A2A_AVAILABLE:
        logger.warning("A2A dependencies not installed")
        return None

    try:
        return A2AModule(host, port, external_url, redis_client)
    except Exception as e:
        logger.error(f"Failed to create A2A server: {e}")
        return None
