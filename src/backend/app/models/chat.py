"""Chat and timeline schemas for Copilot-driven sessions."""

import base64
import binascii
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

_MAX_INLINE_IMAGE_BYTES = 6 * 1024 * 1024
_ALLOWED_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


class MessageRole(str, Enum):
    user = "user"
    assistant = "assistant"


class ChatImageAttachment(BaseModel):
    type: str = Field(default="image")
    mime_type: str
    data_url: str = Field(min_length=32, max_length=9_000_000)
    display_name: str | None = Field(default=None, max_length=120)
    width: int | None = Field(default=None, ge=1, le=16_384)
    height: int | None = Field(default=None, ge=1, le=16_384)
    size_bytes: int | None = Field(default=None, ge=1, le=_MAX_INLINE_IMAGE_BYTES)

    @model_validator(mode="after")
    def validate_attachment(self):
        if self.type != "image":
            raise ValueError("Only image attachments are supported")

        mime_type = self.mime_type.strip().lower()
        if mime_type not in _ALLOWED_IMAGE_MIME_TYPES:
            raise ValueError("Unsupported image MIME type")

        data_url = self.data_url.strip()
        prefix = f"data:{mime_type};base64,"
        if not data_url.startswith(prefix):
            raise ValueError("Image attachment data URL must match the declared MIME type")

        encoded_payload = data_url[len(prefix) :]
        try:
            decoded_payload = base64.b64decode(encoded_payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Image attachment is not valid base64 data") from exc

        payload_size = len(decoded_payload)
        if payload_size > _MAX_INLINE_IMAGE_BYTES:
            raise ValueError("Image attachment exceeds the 6 MB inline upload limit")

        self.type = "image"
        self.mime_type = mime_type
        self.data_url = data_url
        if self.size_bytes is None:
            self.size_bytes = payload_size
        return self


class ChatTurnCreate(BaseModel):
    content: str = Field(default="", max_length=20000)
    attachments: list[ChatImageAttachment] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_turn(self):
        if not self.content.strip() and not self.attachments:
            raise ValueError("Provide a prompt or at least one image attachment")
        return self


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
    turn_id: str | None = None
    sequence: int | None = None
    data: dict = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class ChatTurnResult(BaseModel):
    assistant_message: ChatMessage
    session_status: str
