import logging

import click
import httpx
import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, PushNotificationSender
from a2a.types import AgentAuthentication, AgentCapabilities, AgentCard, AgentSkill, Task
from dotenv import load_dotenv

from kubently.protocol_bindings.a2a_server.agent import KubentlyAgent
from kubently.protocol_bindings.a2a_server.agent_executor import KubentlyAgentExecutor

load_dotenv()


class SimplePushNotificationSender(PushNotificationSender):
    """Simple push notification sender that logs notifications."""

    def __init__(self, client):
        self.client = client

    async def send_notification(self, task: Task) -> None:
        """Sends a push notification containing the latest task state."""
        logging.info(f"Push notification for task {task.id}: {task.status.state}")
        # In a real implementation, this would send notifications to configured endpoints


@click.command()
@click.option("--host", "host", default="0.0.0.0")
@click.option("--port", "port", default=8000)
def main(host: str, port: int):
    # Setup logging
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    client = httpx.AsyncClient()
    request_handler = DefaultRequestHandler(
        agent_executor=KubentlyAgentExecutor(),
        task_store=InMemoryTaskStore(),
        push_sender=SimplePushNotificationSender(client),
    )

    server = A2AStarletteApplication(
        agent_card=get_agent_card(host, port), http_handler=request_handler
    )

    uvicorn.run(server.build(), host=host, port=port)


def get_agent_card(host: str, port: int):
    """Returns the Agent Card for Kubently Kubernetes Debugging Agent."""
    capabilities = AgentCapabilities(streaming=True, pushNotifications=False)
    skill = AgentSkill(
        id="kubernetes-debug",
        name="Kubernetes Debugging",
        description="Debug and inspect Kubernetes clusters through kubectl commands. Troubleshoot pods, services, deployments, and analyze logs.",
        tags=[
            "kubernetes",
            "k8s",
            "debugging",
            "troubleshooting",
            "kubectl",
            "logs",
            "pods",
            "deployments",
            "observability",
        ],
        examples=[
            "Show me all failing pods in the cluster",
            "Get logs for the nginx deployment",
            "List all services in namespace webapp",
            "Debug why my pod is crashlooping",
            "What's using the most CPU in the cluster?",
            "Show recent events for the database",
            "Check if the API service has healthy endpoints",
            "Why is my deployment not rolling out?",
        ],
    )
    return AgentCard(
        name="Kubently Kubernetes Debugger",
        description="AI agent specialized in debugging and inspecting Kubernetes clusters. Executes read-only kubectl commands to troubleshoot issues, analyze logs, and understand cluster state.",
        url=f"http://{host}:{port}/",
        version="1.0.0",
        defaultInputModes=KubentlyAgent.SUPPORTED_CONTENT_TYPES,
        defaultOutputModes=KubentlyAgent.SUPPORTED_CONTENT_TYPES,
        capabilities=capabilities,
        skills=[skill],
        authentication=AgentAuthentication(schemes=["public"]),
    )


if __name__ == "__main__":
    main()
