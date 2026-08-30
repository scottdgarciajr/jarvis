# Jarvis design

## Reference audit (17 August 2026)

Before implementation, this project was compared with the supplied copies and current published metadata:

- **OpenLily**: current `server/pyproject.toml` identifies version 0.11.0, MIT. It is a Pipecat voice-agent toolkit with pluggable brains and tool registry; its local Ollama path uses MLX Whisper/Kokoro and is explicitly Apple Silicon oriented. Jarvis adopts its separation of voice, brains, and tools, but does not embed Pipecat because a Surface RT needs a remote browser client, not a local Python audio pipeline.
- **LotusLampX**: the supplied project is an MIT Python 3.10+ BLE controller. Its CLI/protocol modules provide a useful raw GATT/protocol reference, including effects and scenes. Jarvis does not copy its protocol implementation.
- **Lotus Lamp Python**: current PyPI `lotus-lamp` is 1.1.0 (8 February 2026), MIT, Python 3.7+, with `LotusLamp`, `DeviceConfig`, discovery, RGB, power, brightness, speed, and animation methods. Jarvis’s optional PC-side adapter uses this higher-level public API. Its device/mode differences remain real hardware constraints.

No reference code was copied into Jarvis. Licenses and dependencies remain independent.

## Boundaries

```text
Surface browser ── authenticated HTTPS/HTTP + WebSocket ── Jarvis server (PC)
                                                          ├─ SQLite memory
                                                          ├─ Ollama private API
                                                          └─ optional BLE Lotus adapter
```

The PC is deliberately the only place where Python, models, BLE, and secrets exist. The Surface receives declarative state events and uses browser TTS where available. This makes other clients possible without changing the assistant core.

## Key contracts

`Tool` has a name, description, Pydantic input model, permission, and `execute()` result. The allowlisted registry validates input before execution. A `ToolResult` may contain ordinary LLM data and `Event` instances. Tools do not touch JavaScript or sockets.

`Event` is `{type, state, data}`. State examples include `assistant_state/listening`, `assistant_state/thinking`, `assistant_state/speaking`, and `visual_state/lights`. The event bus fans these out through `/ws`; presentation belongs solely to each client.

`MemoryStore` keeps explicit user memories, conversation history, and future retrieved documents in separate SQLite tables. Only the `remember` tool writes explicit memories.

## Future providers

`integrations/calendar.py` defines `CalendarProvider` and `CalendarEvent`. `CanvasCalendarProvider` is intentionally a non-functional stub: put Canvas OAuth/API code there later, then expose it through a calendar tool. Google or Outlook providers can implement the same protocol. The assistant core never imports a specific provider.

## Voice and Surface RT

The client is vanilla HTML/CSS/ES5-style JavaScript, SVG-free CSS shapes, fetch, WebSocket, and Speech Synthesis—no framework, WebGL, Node, or binary install. Speech recognition is enhancement-only because Internet Explorer/old Edge versions used by Windows RT do not reliably provide `SpeechRecognition`; manual text input remains the dependable Surface RT fallback. For true wake word/STT, run the optional PC-side voice pipeline (openWakeWord + faster-whisper or a compatible OpenLily/Pipecat pipeline) and send recognized text to `AssistantService.handle()`. The Surface does not and should not run it.

## Security model

Every REST mutating endpoint requires `Authorization: Bearer <JARVIS_API_TOKEN>`; WebSocket requires the same token during connection. The default bind is loopback. For a LAN bind, use a strong secret and a trusted network only. Ollama should stay on loopback. Jarvis has no shell/Python execution tool, records no tokens in logs, validates schemas, and rejects names missing from the tool registry.
