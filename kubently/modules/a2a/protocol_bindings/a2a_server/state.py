from enum import Enum
from typing import TypedDict

from pydantic import BaseModel, Field


class MsgType(Enum):
    human = "human"
    assistant = "assistant"


class Message(BaseModel):
    type: MsgType = Field(
        ...,
        description="indicates the originator of the message, a human or an assistant",
    )
    content: str = Field(..., description="the content of the message")


class ConfigSchema(TypedDict):
    to_upper: bool
    to_lower: bool


class InputState(BaseModel):
    messages: list[Message] | None = None


class OutputState(BaseModel):
    messages: list[Message] | None = None


class AgentState(BaseModel):
    input: InputState
    output: OutputState | None = None
