"""Chat and timeline schemas for Copilot-driven sessions."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    user = "user"
    assistant = "assistant"


class ChatTurnCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


class ChatMessage(BaseModel):
    id: str = Field(alias="_id")
    session_id: str
    role: MessageRole
    content: str
    created_at: datetime
    metadata: dict = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class SessionEvent(BaseModel):
    id: str = Field(alias="_id")
    session_id: str
    type: str
    summary: str
    created_at: datetime
    data: dict = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class ChatTurnResult(BaseModel):
    assistant_message: ChatMessage
    session_status: str
