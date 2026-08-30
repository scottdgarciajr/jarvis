from __future__ import annotations

import asyncio
import logging
import json
import re
from uuid import uuid4

from pydantic import ValidationError

from jarvis.events import Event, EventBus
from jarvis.memory import MemoryStore
from jarvis.ollama import OllamaClient, OllamaUnavailable
from jarvis.tools.base import Permission
from jarvis.tools.lotus_lamp import COLORS, EFFECTS, normalize_effect_name
from jarvis.tools.registry import ToolRegistry

log = logging.getLogger(__name__)
SYSTEM = """You are Jarvis, a self-hosted home voice assistant with a poised, capable, quietly witty manner inspired by the classic cinematic AI but not an imitation or quotation machine. Be concise and proactive; give the answer first, with restrained dry warmth when appropriate. Speak naturally aloud. Never use the word 'Jarvis' in a response, because it is the wake phrase. Use a tool only when needed. If the user says only a light effect name, start that effect. If the user asks to chain, rotate, or sequence effects, use the effect sequence tool. "TV mode" means start camera color matching from the server camera for all paired lights. Explicit memories are private and only saved when the user says remember. Never claim a device action succeeded unless a tool result says it did."""


def _decode_tool_like_content(content: str) -> dict | None:
    text = content.strip()
    if not (
        text.startswith("{")
        and '"name"' in text
        and ("parameters" in text or "arguments" in text)
    ):
        return None
    for suffix in ("", "}", "}}"):
        try:
            value = json.loads(text + suffix)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            return {
                "function": {
                    "name": value["name"],
                    "arguments": value.get("parameters", value.get("arguments", {})),
                }
            }
    return {"function": {"name": "", "arguments": {}}}


def _is_tool_like_content(content: str) -> bool:
    return _decode_tool_like_content(content) is not None


def _clean_command(text: str) -> str:
    value = text.strip().lower()
    value = re.sub(r"\b(?:hey\s+)?jarvis\b[,.!\s]*", "", value)
    value = re.sub(r"\b(?:please|can you|could you|would you|set|start|run|make it)\b", " ", value)
    return re.sub(r"\s+", " ", value).strip(" .,!?:;")


def _target_from_command(command: str) -> tuple[str, str | None]:
    target = None
    replacements = {
        r"\b(?:tv|television)\s+lights?\b": "tv lights",
        r"\b(?:loft|upstairs)\s+lights?\b": "loft lights",
        r"\bmelk\s*(?:of21\s*)?25\b": "tv lights",
        r"\bmelk\s*(?:of21\s*)?49\b": "loft lights",
    }
    for pattern, label in replacements.items():
        if re.search(pattern, command):
            target = label
            command = re.sub(pattern, " lights ", command)
    return re.sub(r"\s+", " ", command).strip(), target


def _direct_lotus_call(text: str) -> tuple[str, dict] | None:
    command, target = _target_from_command(_clean_command(text))
    args = {"target": target} if target else {}
    tv_args = {"enabled": True, "source": "server", **args}
    stop_tv_args = {"enabled": False, "source": "server", **args}

    movie_mode = re.search(
        r"\b(?:tv|television|movie|cinema|theater)\s+(?:mode|lighting|lights?)\b",
        command,
    ) or re.search(
        r"\b(?:match|sync|follow)\s+(?:the\s+)?(?:movie|screen|tv|television|camera)\b",
        command,
    )

    if re.search(r"\b(?:both|all)\s+lights?\b", command):
        tv_args.pop("target", None)
        stop_tv_args.pop("target", None)

    if re.search(r"\b(?:stop|turn\s+off|disable|end)\s+(?:tv|television|movie|cinema|theater|camera)\s+(?:mode|match|sync|lighting|lights?)\b", command):
        return ("lotus_camera_sync", stop_tv_args)

    if command in {"tv", "television", "movie", "cinema", "theater", "tv mode", "television mode", "movie mode", "cinema mode", "theater mode"} or movie_mode:
        return ("lotus_camera_sync", tv_args)

    if re.search(r"\b(?:turn|switch)\s+(?:the\s+|my\s+)?lights?\s+off\b", command) or command in {"lights off", "off lights"}:
        return ("lotus_turn_off", args)

    color_aliases = {
        "violet": "purple",
        "magenta": "pink",
        "aqua": "cyan",
        "teal": "cyan",
    }
    color_names = sorted([*COLORS.keys(), *color_aliases.keys()], key=len, reverse=True)
    color_match = re.search(r"\b(" + "|".join(re.escape(color) for color in color_names) + r")\b", command)
    if color_match and (
        command == color_match.group(1)
        or bool(target)
        or re.search(r"\b(?:lights?|lotus|lamp|color|turn|make|set|switch)\b", command)
    ):
        color = color_aliases.get(color_match.group(1), color_match.group(1))
        return ("lotus_set_color", {**args, "color": color})

    if re.search(r"\b(?:turn|switch)\s+(?:the\s+|my\s+)?lights?\s+on\b", command) or command in {"lights on", "on lights"}:
        return ("lotus_turn_on", args)

    parts = [
        part.strip()
        for part in re.split(r"\bthen\b|\bfollowed by\b|,|;", command)
        if part.strip()
    ]
    if len(parts) > 1:
        effects = [normalize_effect_name(part) for part in parts]
        if all(effect in EFFECTS for effect in effects):
            return ("lotus_sequence_effects", {"effects": effects})

    effect_name = normalize_effect_name(command)
    if effect_name in EFFECTS:
        return ("lotus_set_effect", {"effect": effect_name})

    return None


class AssistantService:
    def __init__(self, ollama: OllamaClient, memory: MemoryStore, tools: ToolRegistry, events: EventBus) -> None:
        self.ollama, self.memory, self.tools, self.events = ollama, memory, tools, events
        self.last_debug_trace: dict | None = None
        self._handle_lock = asyncio.Lock()

    async def handle(self, text: str, session_id: str | None = None) -> tuple[str, str]:
        async with self._handle_lock:
            return await self._handle(text, session_id)

    async def _handle(self, text: str, session_id: str | None = None) -> tuple[str, str]:
        session_id = session_id or str(uuid4())
        text = text.strip()
        if not text: raise ValueError("A request is required")
        trace = {
            "session_id": session_id,
            "input_text": text,
            "outcome": "started",
            "tool_calls": [],
            "direct_tool_call": None,
            "reply": None,
            "error": None,
        }
        self.last_debug_trace = trace
        self.memory.add_conversation(session_id, "user", text)
        await self.events.publish(Event("assistant_state", "thinking"))
        messages = [{"role": "system", "content": SYSTEM}, *self.memory.recent_conversation(session_id)]
        try:
            direct_call = _direct_lotus_call(text)
            if direct_call:
                name, arguments = direct_call
                trace["direct_tool_call"] = {"name": name, "arguments": arguments}
                result = await self.tools.call(name, arguments)
                for event in result.events:
                    await self.events.publish(event)
                reply = result.content
                trace["outcome"] = "direct_tool_reply"
                trace["reply"] = reply
                self.memory.add_conversation(session_id, "assistant", reply)
                await self.events.publish(Event("assistant_state", "speaking", {"text": reply}))
                return reply, session_id

            message = await self.ollama.chat(messages, self.tools.schemas())
            tool_calls = message.get("tool_calls", [])
            content_tool_call = _decode_tool_like_content(message.get("content") or "")
            if content_tool_call and self.tools.has(content_tool_call["function"]["name"]):
                tool_calls = [content_tool_call]
                message = {**message, "content": "", "tool_calls": tool_calls}
            elif content_tool_call:
                messages.append({"role": "assistant", "content": message.get("content") or ""})
                messages.append({"role": "user", "content": "That was a tool-call object, not spoken output. Answer my original request aloud in plain language."})
                message = await self.ollama.chat(messages, [])
                tool_calls = []
            if tool_calls:
                # Ollama returns a message body without duplicating its role;
                # restore it before carrying the tool-call exchange forward.
                messages.append({"role": "assistant", **message})
                tool_results = []
                direct_reply = False
                for call in tool_calls:
                    function = call["function"]
                    tool_trace = {
                        "name": function["name"],
                        "arguments": function.get("arguments", {}),
                        "result": None,
                        "error": None,
                    }
                    try:
                        result = await self.tools.call(function["name"], function.get("arguments", {}))
                        direct_reply = (
                            direct_reply
                            or function["name"].startswith("lotus_")
                            or self.tools.permission(function["name"]) == Permission.DEVICE_CONTROL
                        )
                    except (ValidationError, PermissionError, ValueError) as exc:
                        # A model can produce an incomplete tool call. Treat it
                        # as a tool result, then let the model respond naturally
                        # instead of failing the whole spoken conversation.
                        from jarvis.tools.base import ToolResult
                        result = ToolResult("The requested action was not run because its details were incomplete or invalid.")
                        tool_trace["error"] = type(exc).__name__
                    tool_trace["result"] = result.content
                    trace["tool_calls"].append(tool_trace)
                    tool_results.append(result.content)
                    for event in result.events: await self.events.publish(event)
                    messages.append({"role": "tool", "tool_name": function["name"], "content": result.content})
                if direct_reply:
                    message = {"content": " ".join(tool_results)}
                else:
                    message = await self.ollama.chat(messages, [])
            reply = message.get("content") or "Done."
            if _is_tool_like_content(reply):
                reply = "I got tangled in a tool call. Please say that once more."
            trace["outcome"] = "reply"
            trace["reply"] = reply
        except Exception as exc:
            log.warning("assistant request failed: %s", type(exc).__name__)
            trace["outcome"] = "error"
            trace["error"] = {"type": type(exc).__name__, "message": str(exc)}
            message = (
                "I could not reach my local brain."
                if isinstance(exc, OllamaUnavailable)
                else "I'm having connection issues."
            )
            await self.events.publish(Event("assistant_state", "error", {"message": message}))
            raise
        self.memory.add_conversation(session_id, "assistant", reply)
        await self.events.publish(Event("assistant_state", "speaking", {"text": reply}))
        return reply, session_id
