from __future__ import annotations

import json

from jarvis.tools.base import Tool, ToolResult, ollama_schema


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict]:
        return [ollama_schema(tool) for tool in self._tools.values()]

    def metadata(self) -> list[dict]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "permission": tool.permission,
                "parameters": tool.input_model.model_json_schema(),
            }
            for tool in self._tools.values()
        ]

    def has(self, name: str) -> bool:
        return name in self._tools

    def permission(self, name: str):
        if name not in self._tools:
            raise PermissionError(f"Tool {name!r} is not allowlisted")
        return self._tools[name].permission

    async def call(self, name: str, arguments: dict) -> ToolResult:
        if name not in self._tools:
            raise PermissionError(f"Tool {name!r} is not allowlisted")
        # Some Ollama model templates serialize function arguments as a JSON
        # string instead of an object. Both formats are valid on this boundary.
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object")
        tool = self._tools[name]
        validated = tool.input_model.model_validate(arguments)
        return await tool.execute(validated.model_dump())
