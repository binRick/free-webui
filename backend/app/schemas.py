from typing import Literal
from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage]
    temperature: float | None = None


class ModelInfo(BaseModel):
    id: str


class ModelList(BaseModel):
    data: list[ModelInfo]
