from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from a2a.helpers import new_text_part
from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)


def update_task_with_agent_response(task: Task, agent_response: dict[str, Any]) -> None:
    """Updates the provided task with the agent response."""
    task.status.timestamp.FromDatetime(datetime.now(UTC))
    parts: list[Part] = [new_text_part(agent_response["content"])]
    if agent_response["require_user_input"]:
        task.status.state = TaskState.TASK_STATE_INPUT_REQUIRED
        message = Message(
            message_id=str(uuid4()),
            role=Role.ROLE_AGENT,
            parts=parts,
        )
        task.status.message.CopyFrom(message)
        task.history.append(message)
    else:
        task.status.state = TaskState.TASK_STATE_COMPLETED
        # Protobuf has no null for a submessage: "no message" is the field being
        # unset, which is what ClearField does (the old code assigned None).
        task.status.ClearField("message")
        task.artifacts.append(Artifact(parts=parts, artifact_id=str(uuid4())))


def process_streaming_agent_response(
    task: Task,
    agent_response: dict[str, Any],
) -> tuple[TaskArtifactUpdateEvent | None, TaskStatusUpdateEvent]:
    """Processes the streaming agent responses and returns TaskArtifactUpdateEvent and TaskStatusUpdateEvent."""
    is_task_complete = agent_response["is_task_complete"]
    require_user_input = agent_response["require_user_input"]
    parts: list[Part] = [new_text_part(agent_response["content"])]

    artifact = None
    message = None

    # responses from this agent can be working/completed/input-required
    if not is_task_complete and not require_user_input:
        task_state = TaskState.TASK_STATE_WORKING
        message = Message(role=Role.ROLE_AGENT, parts=parts, message_id=str(uuid4()))
    elif require_user_input:
        task_state = TaskState.TASK_STATE_INPUT_REQUIRED
        message = Message(role=Role.ROLE_AGENT, parts=parts, message_id=str(uuid4()))
    else:
        task_state = TaskState.TASK_STATE_COMPLETED
        artifact = Artifact(parts=parts, artifact_id=str(uuid4()))

    task_artifact_update_event = None

    if artifact:
        task_artifact_update_event = TaskArtifactUpdateEvent(
            task_id=task.id,
            context_id=task.context_id,
            artifact=artifact,
            append=False,
            last_chunk=True,
        )

    # a2a-sdk 1.x removed TaskStatusUpdateEvent.final: the stream ends on a
    # terminal TaskState (completed / failed / input-required), and the v0.3
    # compatibility layer re-derives `final` from the state for older clients.
    task_status_event = TaskStatusUpdateEvent(
        task_id=task.id,
        context_id=task.context_id,
        status=TaskStatus(
            state=task_state,
            message=message,
        ),
    )
    task_status_event.status.timestamp.FromDatetime(datetime.now(UTC))

    return task_artifact_update_event, task_status_event
