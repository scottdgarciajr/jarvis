from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel

from jarvis.events import Event


class Permission(StrEnum):
    SAFE = "safe"
    DEVICE_CONTROL = "device_control"


@dataclass
class ToolResult:
    content: str
    data: dict[str, Any] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)


class Tool(Protocol):
    name: str
    description: str
    permission: Permission
    input_model: type[BaseModel]
    async def execute(self, arguments: dict[str, Any]) -> ToolResult: ...


def ollama_schema(tool: Tool) -> dict[str, Any]:
    return {"type": "function", "function": {"name": tool.name, "description": tool.description, "parameters": tool.input_model.model_json_schema()}}
