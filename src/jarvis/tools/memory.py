from pydantic import BaseModel, Field

from jarvis.memory import MemoryStore
from jarvis.tools.base import Permission, ToolResult


class RememberInput(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class RecallInput(BaseModel):
    query: str = Field(min_length=1, max_length=500)


class RememberTool:
    name = "remember"
    description = "Save an explicit personal fact only when the user asks to remember it."
    permission = Permission.SAFE
    input_model = RememberInput
    def __init__(self, store: MemoryStore) -> None: self.store = store
    async def execute(self, arguments: dict) -> ToolResult:
        memory = self.store.add_memory(arguments["text"])
        return ToolResult("Memory saved.", {"memory_id": memory.id})


class RecallTool:
    name = "recall_memory"
    description = "Look up explicit facts the user previously asked Jarvis to remember."
    permission = Permission.SAFE
    input_model = RecallInput
    def __init__(self, store: MemoryStore) -> None: self.store = store
    async def execute(self, arguments: dict) -> ToolResult:
        memories = self.store.search_memories(arguments["query"])
        text = "\n".join(f"- {memory.text}" for memory in memories) or "No matching saved memories."
        return ToolResult(text, {"memories": [memory.__dict__ for memory in memories]})
