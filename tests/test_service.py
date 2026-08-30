import pytest
from jarvis.events import EventBus
from jarvis.memory import MemoryStore
from jarvis.service import AssistantService
from jarvis.tools.memory import RememberTool
from jarvis.tools.registry import ToolRegistry

class FakeOllama:
    async def chat(self,messages,tools):
        if not any(m["role"]=="tool" for m in messages): return {"content":"", "tool_calls":[{"function":{"name":"remember","arguments":{"text":"I like jazz"}}}]}
        return {"content":"I'll remember that."}

class InvalidToolOllama:
    async def chat(self,messages,tools):
        if not any(m["role"]=="tool" for m in messages): return {"content":"", "tool_calls":[{"function":{"name":"remember","arguments":"{}"}}]}
        return {"content":"What would you like me to remember?"}

class TextToolCallOllama:
    async def chat(self,messages,tools):
        if not any(m["role"]=="tool" for m in messages): return {"content":'{"name":"remember","parameters":{"text":"I like blues"}}'}
        return {"content":"I'll remember that."}

class JunkToolJsonOllama:
    def __init__(self): self.calls = 0
    async def chat(self,messages,tools):
        self.calls += 1
        if self.calls == 1: return {"content":'{"name":"lotus_set_text_color","parameters":{"color":"white"}}'}
        return {"content":"A B C."}

@pytest.mark.asyncio
async def test_conversation_executes_allowlisted_tool(tmp_path):
    memory=MemoryStore(tmp_path/"a.db"); tools=ToolRegistry(); tools.register(RememberTool(memory))
    service=AssistantService(FakeOllama(),memory,tools,EventBus())
    reply,_=await service.handle("Remember I like jazz")
    assert reply == "I'll remember that."
    assert service.last_debug_trace["outcome"] == "reply"
    assert service.last_debug_trace["tool_calls"][0]["name"] == "remember"
    assert service.last_debug_trace["reply"] == "I'll remember that."
    assert memory.search_memories("jazz")[0].text == "I like jazz"

@pytest.mark.asyncio
async def test_invalid_model_tool_arguments_do_not_fail_conversation(tmp_path):
    memory=MemoryStore(tmp_path/"a.db"); tools=ToolRegistry(); tools.register(RememberTool(memory))
    service=AssistantService(InvalidToolOllama(), memory, tools, EventBus())
    reply,_=await service.handle("Remember something")
    assert reply == "What would you like me to remember?"
    assert service.last_debug_trace["tool_calls"][0]["error"] == "ValidationError"

@pytest.mark.asyncio
async def test_text_tool_call_is_executed_instead_of_spoken(tmp_path):
    memory=MemoryStore(tmp_path/"a.db"); tools=ToolRegistry(); tools.register(RememberTool(memory))
    service=AssistantService(TextToolCallOllama(),memory,tools,EventBus())
    reply,_=await service.handle("Remember I like blues")
    assert reply == "I'll remember that."
    assert memory.search_memories("blues")[0].text == "I like blues"

@pytest.mark.asyncio
async def test_unknown_tool_json_is_not_spoken(tmp_path):
    memory=MemoryStore(tmp_path/"a.db"); tools=ToolRegistry(); tools.register(RememberTool(memory))
    service=AssistantService(JunkToolJsonOllama(),memory,tools,EventBus())
    reply,_=await service.handle("Say the ABCs")
    assert reply == "A B C."
