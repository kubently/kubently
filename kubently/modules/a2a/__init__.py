"""
A2A Module - Black Box Interface

Purpose: Enable agent-to-agent communication for multi-agent systems
Interface: A2A protocol server on port 8000
Hidden: Protocol handling, tool mapping, LLM integration

Can be disabled or replaced with different protocol implementations.
Runs in same process but maintains complete separation.
"""

from __future__ import annotations

import asyncio
import logging
import os
from threading import Thread
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.applications import Starlette as StarletteApp

logger = logging.getLogger(__name__)

# Environment switch that decides whether this deployment expects A2A. Whether a
# module imported is NOT that decision (issue #97).
A2A_ENABLED_ENV = "KUBENTLY_A2A"

# The agent card path a2a-sdk 0.2.x served. The current spec (and the 1.x
# default, `a2a.utils.constants.AGENT_CARD_WELL_KNOWN_PATH`) is
# `/.well-known/agent-card.json`, but the card is a public contract: clients
# pinned to the old path must not stop discovering Kubently, so both are served.
LEGACY_AGENT_CARD_PATH = "/.well-known/agent.json"

_OFF_VALUES = {"off", "false", "0", "no"}


class A2AUnavailableError(RuntimeError):
    """A2A is expected but its SDK cannot be imported.

    Raised instead of degrading, so an incompatible ``a2a-sdk`` cannot start the
    API with the whole A2A protocol surface missing behind one log line.
    """


def a2a_enabled() -> bool:
    """Whether this deployment expects the A2A protocol surface.

    Gated on an explicit setting, the same shape as the other optional toolsets
    (``KUBENTLY_CLOUD_TOOLS=off``), not on whether an import happened to
    succeed. Defaults to on: ``kubently/main.py`` mounts A2A at ``/a2a/`` and
    treats its absence as a startup failure, so "enabled" is the normal state
    and opting out has to be deliberate.
    """
    return os.getenv(A2A_ENABLED_ENV, "on").strip().lower() not in _OFF_VALUES


# The ImportError itself, kept rather than discarded: it is the only description
# of *why* A2A is unavailable, and it is chained onto A2AUnavailableError below.
A2A_IMPORT_ERROR: BaseException | None = None

# Try to import only the lightweight A2A server primitives at module import time.
# Heavy dependencies (LangChain, LLMs, etc.) will be imported lazily inside get_app().
try:
    import uvicorn
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
    from a2a.server.tasks import InMemoryTaskStore, PushNotificationSender
    from a2a.types import (
        AgentCapabilities,
        AgentCard,
        AgentInterface,
        AgentSkill,
        Task,
        TaskArtifactUpdateEvent,
        TaskStatusUpdateEvent,
    )
    from a2a.utils.constants import (
        AGENT_CARD_WELL_KNOWN_PATH,
        PROTOCOL_VERSION_0_3,
        PROTOCOL_VERSION_CURRENT,
        TransportProtocol,
    )
    from starlette.applications import Starlette

    A2A_AVAILABLE = True
except Exception as e:  # Broad catch: the log level and fatality are decided below.
    A2A_AVAILABLE = False
    A2A_IMPORT_ERROR = e
    if a2a_enabled():
        # Expected but broken: ERROR with the traceback, and create_a2a_server()
        # will refuse to hand back a half-built API.
        logger.error(
            "A2A is enabled (%s is not 'off') but the a2a-sdk could not be imported: %s",
            A2A_ENABLED_ENV,
            e,
            exc_info=True,
        )
    else:
        logger.info("A2A is disabled via %s=off; SDK import skipped: %s", A2A_ENABLED_ENV, e)


if A2A_AVAILABLE:

    class SimplePushNotificationSender(PushNotificationSender):
        """Simple push notification sender."""

        async def send_notification(
            self, task_id: str, event: Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent
        ) -> None:
            """Log notifications for debugging.

            a2a-sdk 1.x passes the task id alongside the event rather than a
            whole ``Task``, because artifact updates are pushable too (#76).
            """
            logger.debug(f"Push notification for task {task_id}: {type(event).__name__}")


class A2AModule:
    """A2A server module that runs alongside the main API."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8000,
        external_url: str | None = None,
        redis_client=None,
    ):
        """Initialize the A2A server."""
        if not A2A_AVAILABLE:
            raise A2AUnavailableError(
                f"a2a-sdk is not importable: {A2A_IMPORT_ERROR!r}"
            ) from A2A_IMPORT_ERROR

        self.host = host
        self.port = port
        if not external_url:
            # `host` is a bind address: 0.0.0.0 means "every interface", and a
            # card advertising it is worse than no card, because the client
            # believes discovery succeeded and then cannot dial it.
            logger.warning(
                "A2A_EXTERNAL_URL is not set - the agent card will advertise a "
                "locally-derived URL that remote clients cannot reach. Set it to "
                "the externally reachable A2A endpoint (including the /a2a/ path)."
            )
            reachable_host = "localhost" if host in ("0.0.0.0", "::", "") else host
            external_url = f"http://{reachable_host}:{port}/a2a/"
        self.external_url = external_url
        self.redis_client = redis_client
        self.server = None
        self.thread = None
        self._app = None  # Cache for the A2A ASGI sub-application

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

        capabilities = AgentCapabilities(streaming=True, push_notifications=False)

        # Skills come from the same configuration gating that registers tools, so
        # the card cannot advertise a toolset this deployment does not have (or
        # hide one it does).
        from .skills import build_skills

        skills = [
            AgentSkill(**skill) for skill in build_skills(has_redis=self.redis_client is not None)
        ]

        return AgentCard(
            name="Kubently Kubernetes Debugger",
            description=(
                "AI agent for investigating Kubernetes clusters: read-only kubectl "
                "across a registered fleet, pod and Loki log search, Prometheus "
                "metrics, change correlation, cloud telemetry, past-incident recall "
                "and GitOps fix proposals. The `skills` list is what this deployment "
                "actually has enabled."
            ),
            # 1.x replaced the card's single `url` + `protocolVersion` pair with a
            # list of interfaces. Both are advertised because both are served:
            # get_app() enables the SDK's v0.3 compatibility on the same endpoint,
            # so `message/stream` and `SendStreamingMessage` both work. Listing
            # 0.3 also keeps the legacy top-level `url`/`protocolVersion`/
            # `preferredTransport` fields in the published JSON (the SDK derives
            # them from the first 0.3-compatible interface), which is what every
            # existing client — the Kubently CLI included — reads.
            supported_interfaces=[
                AgentInterface(
                    url=self.external_url,
                    protocol_binding=TransportProtocol.JSONRPC,
                    protocol_version=PROTOCOL_VERSION_CURRENT,
                ),
                AgentInterface(
                    url=self.external_url,
                    protocol_binding=TransportProtocol.JSONRPC,
                    protocol_version=PROTOCOL_VERSION_0_3,
                ),
            ],
            version="1.0.0",
            default_input_modes=KubentlyAgent.SUPPORTED_CONTENT_TYPES,
            default_output_modes=KubentlyAgent.SUPPORTED_CONTENT_TYPES,
            capabilities=capabilities,
            skills=skills,
        )

    def get_mount_config(self) -> tuple[str, StarletteApp]:
        """
        Get the mount configuration for integrating A2A into the main API.

        This method encapsulates all knowledge about how A2A should be mounted,
        keeping the orchestration layer (main.py) from knowing implementation details.

        Returns:
            Tuple of (mount_path, asgi_app) ready to use with app.mount()

        Example:
            >>> a2a_server = create_a2a_server(...)
            >>> path, app = a2a_server.get_mount_config()
            >>> main_app.mount(path, app)
        """
        return ("/a2a", self.get_app())

    def get_app(self):
        """Get the A2A ASGI sub-application for mounting under the main API."""
        if self._app is None:
            # Lazily import heavy executor only when constructing the app
            KubentlyAgentExecutor = self._lazy_imports()

            # Create request handler
            agent_executor = KubentlyAgentExecutor(redis_client=self.redis_client)
            # Don't initialize here - let it initialize in the correct event loop
            # asyncio.run(agent_executor.initialize())

            # Log executor creation
            logger.info(f"Created KubentlyAgentExecutor: {agent_executor}")

            agent_card = self.get_agent_card()

            # Use DefaultRequestHandler for now
            request_handler = DefaultRequestHandler(
                agent_executor=agent_executor,
                task_store=InMemoryTaskStore(),
                agent_card=agent_card,
                push_sender=SimplePushNotificationSender(),
            )

            logger.info(f"Created DefaultRequestHandler with executor: {request_handler}")

            # Build and cache the A2A ASGI app.
            #
            # a2a-sdk 1.x deleted A2AStarletteApplication; the routes are now
            # assembled directly (#76). The composition below is what that class
            # used to do, with two deliberate choices:
            #
            # * enable_v0_3_compat=True — 1.x renamed the JSON-RPC methods
            #   (`message/stream` -> `SendStreamingMessage`). The Kubently CLI and
            #   every other deployed A2A client speak the 0.3 names, so the
            #   endpoint serves both. Without it those clients get -32601 Method
            #   not found from a server that otherwise looks perfectly healthy.
            # * the card is served at both well-known paths — the 1.x default
            #   `/.well-known/agent-card.json` (the current A2A spec) and the
            #   `/.well-known/agent.json` that 0.2.x served and existing clients,
            #   docs and probes still request.
            self._app = Starlette(
                routes=[
                    *create_agent_card_routes(agent_card, card_url=AGENT_CARD_WELL_KNOWN_PATH),
                    *create_agent_card_routes(agent_card, card_url=LEGACY_AGENT_CARD_PATH),
                    *create_jsonrpc_routes(request_handler, rpc_url="/", enable_v0_3_compat=True),
                ]
            )

            # NOTE: Auth is intentionally NOT added here. add_middleware() on this built
            # sub-app does not reliably run once it is mounted under the main app (Starlette
            # builds the middleware stack lazily), which previously left /a2a/ unauthenticated.
            # API-key auth is enforced at the mount point in kubently/main.py via an explicit
            # ASGI wrapper (add_api_key_auth). If A2A is ever run standalone via run_server(),
            # wrap get_app() the same way there.

        return self._app

    def run_server(self):
        """Run the A2A server in a separate thread."""
        # Create new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Get the A2A app (creates it if needed)
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
    host: str = "0.0.0.0", port: int = 8000, external_url: str | None = None, redis_client=None
) -> A2AModule | None:
    """Create the A2A server, or return None only when A2A is explicitly disabled.

    Returning None used to mean "something went wrong somewhere" — a missing SDK,
    a bad card, a broken constructor — all flattened into one log line, which is
    how an incompatible ``a2a-sdk`` could bring the API up with no A2A at all
    (issue #97). Now None means exactly one thing: ``KUBENTLY_A2A=off``.

    Raises:
        A2AUnavailableError: A2A is expected but the SDK is missing/incompatible.
        Exception: anything the A2A module raises while building — propagated
            with its traceback rather than swallowed.
    """
    if not a2a_enabled():
        logger.warning(
            "A2A is disabled via %s=off; the /a2a/ protocol surface will not be served",
            A2A_ENABLED_ENV,
        )
        return None

    if not A2A_AVAILABLE:
        raise A2AUnavailableError(
            f"A2A is enabled ({A2A_ENABLED_ENV} is not 'off') but the a2a-sdk could not be "
            f"imported: {A2A_IMPORT_ERROR!r}. Install the 'a2a' extra "
            f'(pip install -e ".[a2a]"), or set {A2A_ENABLED_ENV}=off to run deliberately '
            f"without the A2A protocol surface."
        ) from A2A_IMPORT_ERROR

    return A2AModule(host, port, external_url, redis_client)
