from __future__ import annotations

import asyncio
import json
import logging
import re
import traceback
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from jarvis.config import Settings, get_settings
from jarvis.debug_log import DebugLog
from jarvis.events import Event, EventBus
from jarvis.integrations.lotus import discover_lamps
from jarvis.memory import MemoryStore
from jarvis.ollama import OllamaClient
from jarvis.server_voice import ServerMic
from jarvis.service import AssistantService
from jarvis.tools.lotus_lamp import COLORS, CONTROLLERS, EFFECTS, HARDWARE_MODES, LotusController, LotusTools
from jarvis.tools.memory import RecallTool, RememberTool
from jarvis.tools.registry import ToolRegistry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

CAMERA_INDEX_CACHE: int | None = None
CAMERA_COLOR_STATE: dict[str, tuple[int, int, int]] = {}
CAMERA_CAPTURE = None


def is_windows_connection_reset(exc: BaseException | None) -> bool:
    return isinstance(exc, ConnectionResetError) and getattr(exc, "winerror", None) == 10054


def install_asyncio_noise_filter(debug_log: DebugLog):
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    def handle_exception(loop, context):
        exc = context.get("exception")
        if is_windows_connection_reset(exc):
            debug_log.write(
                "asyncio_connection_reset",
                source="asyncio",
                outcome="suppressed",
                error={"type": type(exc).__name__, "message": str(exc), "winerror": 10054},
            )
            return
        if previous_handler:
            previous_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(handle_exception)
    return previous_handler

class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=100)
class ChatResponse(BaseModel): reply: str; session_id: str
class MusicLightRequest(BaseModel):
    red: int = Field(ge=0, le=255)
    green: int = Field(ge=0, le=255)
    blue: int = Field(ge=0, le=255)
    energy: float = Field(ge=0, le=1)
    target: str | None = Field(default=None, max_length=60)


class CameraColorRequest(BaseModel):
    target: str | None = Field(default=None, max_length=60)
    profile: str = Field(default="cinema", max_length=20)
    smoothing: float = Field(default=0.42, ge=0, le=0.92)
    intensity: float = Field(default=1.15, ge=0.35, le=1.8)
    threshold: int = Field(default=16, ge=0, le=120)


class TvModeRequest(CameraColorRequest):
    enabled: bool = True
    delay_ms: int = Field(default=260, ge=100, le=5000)


class LotusPairRequest(BaseModel):
    controller: str | None = Field(default=None, max_length=30)
    name: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=3, max_length=100)
    label: str | None = Field(default=None, max_length=60)


class ToolCallRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    arguments: dict = Field(default_factory=dict)


class ToolCallResponse(BaseModel):
    content: str
    data: dict


class ClientLogRequest(BaseModel):
    event: str = Field(min_length=1, max_length=80)
    data: dict = Field(default_factory=dict)


def enhance_camera_frame_rgb(frame, cv2, profile: str, intensity: float) -> tuple[int, int, int]:
    height, width = frame.shape[:2]
    x0, x1 = int(width * 0.12), int(width * 0.88)
    y0, y1 = int(height * 0.10), int(height * 0.90)
    cropped = frame[y0:y1, x0:x1]
    small = cv2.resize(cropped, (24, 14), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV).astype("float32")

    profile = (profile or "cinema").lower()
    if profile == "vivid":
        saturation_boost, value_boost, floor = 1.45, 1.18, 34
    elif profile == "soft":
        saturation_boost, value_boost, floor = 1.08, 0.92, 18
    elif profile == "dark":
        saturation_boost, value_boost, floor = 1.25, 0.68, 8
    else:
        saturation_boost, value_boost, floor = 1.25, 1.02, 22

    hsv[:, :, 1] = (hsv[:, :, 1] * saturation_boost * intensity).clip(0, 235)
    hsv[:, :, 2] = (hsv[:, :, 2] * value_boost * intensity).clip(floor, 220)
    enhanced = cv2.cvtColor(hsv.astype("uint8"), cv2.COLOR_HSV2BGR)
    blue, green, red, _alpha = cv2.mean(enhanced)
    return int(red), int(green), int(blue)


def smooth_camera_rgb(
    target: str | None,
    red: int,
    green: int,
    blue: int,
    smoothing: float,
    threshold: int = 16,
) -> tuple[int, int, int]:
    key = target or "__all__"
    previous = CAMERA_COLOR_STATE.get(key)
    if previous is None:
        CAMERA_COLOR_STATE[key] = (red, green, blue)
        return red, green, blue
    amount = max(0.0, min(0.92, smoothing))
    mixed = tuple(
        int(previous[index] * amount + value * (1.0 - amount))
        for index, value in enumerate((red, green, blue))
    )
    total_delta = sum(
        abs(mixed[index] - previous[index])
        for index in range(3)
    )
    if total_delta < threshold:
        return previous
    max_step = max(18, int(70 * (1.0 - amount)))
    mixed = tuple(
        previous[index] + max(-max_step, min(max_step, mixed[index] - previous[index]))
        for index in range(3)
    )
    CAMERA_COLOR_STATE[key] = mixed
    return mixed


def capture_server_camera_rgb(
    camera_index: int = 0,
    profile: str = "cinema",
    intensity: float = 1.15,
) -> tuple[int, int, int]:
    global CAMERA_CAPTURE, CAMERA_INDEX_CACHE

    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError(
            "Server camera mode needs opencv-python installed on the Jarvis PC."
        ) from exc

    suppress_cv2_errors(cv2)

    if CAMERA_CAPTURE is not None:
        try:
            if CAMERA_CAPTURE.isOpened():
                ok, frame = CAMERA_CAPTURE.read()
                if ok and frame is not None:
                    return enhance_camera_frame_rgb(frame, cv2, profile, intensity)
        except Exception:
            pass
        try:
            CAMERA_CAPTURE.release()
        except Exception:
            pass
        CAMERA_CAPTURE = None

    indexes = []
    if CAMERA_INDEX_CACHE is not None:
        indexes.append(CAMERA_INDEX_CACHE)
    indexes.append(camera_index)
    indexes.extend(
        index
        for index in range(5)
        if index not in indexes
    )

    for index in indexes:
        for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF):
            camera = cv2.VideoCapture(index, backend)
            try:
                if not camera.isOpened():
                    continue
                ok, frame = camera.read()
                if not ok or frame is None:
                    continue
                CAMERA_INDEX_CACHE = index
                CAMERA_CAPTURE = camera
                return enhance_camera_frame_rgb(frame, cv2, profile, intensity)
            finally:
                if CAMERA_CAPTURE is not camera:
                    camera.release()

    raise RuntimeError(
        "The Jarvis PC camera could not be opened. Check Windows camera privacy settings or set JARVIS_CAMERA_INDEX."
    )


def suppress_cv2_errors(cv2) -> None:
    try:
        cv2.setLogLevel(0)
    except Exception:
        pass


def server_camera_status(camera_index: int = 0) -> dict:
    try:
        red, green, blue = capture_server_camera_rgb(camera_index)
        return {
            "available": True,
            "camera_index": CAMERA_INDEX_CACHE,
            "color": f"#{red:02X}{green:02X}{blue:02X}",
        }
    except RuntimeError as exc:
        return {
            "available": False,
            "camera_index": CAMERA_INDEX_CACHE,
            "error": str(exc),
        }

def release_server_camera() -> None:
    global CAMERA_CAPTURE
    if CAMERA_CAPTURE is not None:
        try:
            CAMERA_CAPTURE.release()
        except Exception:
            pass
    CAMERA_CAPTURE = None


def error_details(exc: Exception) -> dict:
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": traceback.format_exc(),
    }


def tool_preempts_server_tv(name: str, arguments: dict) -> bool:
    if not name.startswith("lotus_"):
        return False
    if name in {"lotus_list_effects"}:
        return False
    if name == "lotus_camera_sync":
        enabled = bool(arguments.get("enabled", True))
        source = (arguments.get("source") or "server").strip().lower()
        return not enabled or source == "local"
    return True


class ServerTvMode:
    def __init__(
        self,
        lamp: LotusController,
        settings: Settings,
        events: EventBus,
        debug_log: DebugLog,
    ) -> None:
        self.lamp = lamp
        self.settings = settings
        self.events = events
        self.debug_log = debug_log
        self.task: asyncio.Task | None = None
        self.stop_event: asyncio.Event | None = None
        self.options: TvModeRequest | None = None
        self.last_color: str | None = None
        self.last_error: str | None = None
        self.failure_count = 0

    def status(self) -> dict:
        return {
            "running": self.task is not None and not self.task.done(),
            "target": self.options.target if self.options else None,
            "profile": self.options.profile if self.options else None,
            "delay_ms": self.options.delay_ms if self.options else None,
            "last_color": self.last_color,
            "last_error": self.last_error,
            "failure_count": self.failure_count,
        }

    async def start(self, options: TvModeRequest) -> dict:
        await self.stop(publish=False)
        await self.lamp.stop_effect()
        self.options = options
        self.stop_event = asyncio.Event()
        self.last_error = None
        self.failure_count = 0
        self.task = asyncio.create_task(self._run(options, self.stop_event))
        await self.events.publish(
            Event(
                "camera_sync",
                "on",
                {
                    "enabled": True,
                    "source": "server",
                    "target": options.target,
                    "profile": options.profile,
                    "server_owned": True,
                },
            )
        )
        await self.events.publish(
            Event("assistant_state", "idle", {"message": "TV mode started from the Jarvis PC."})
        )
        self.debug_log.write(
            "server_tv_mode",
            source="server_tv_mode",
            outcome="started",
            options=options.model_dump(),
        )
        return self.status()

    async def stop(self, publish: bool = True) -> dict:
        stop_event = self.stop_event
        task = self.task
        if stop_event is not None:
            stop_event.set()
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.task = None
        self.stop_event = None
        self.options = None
        release_server_camera()
        if publish:
            await self.events.publish(
                Event(
                    "camera_sync",
                    "off",
                    {"enabled": False, "source": "server", "server_owned": True},
                )
            )
            await self.events.publish(Event("assistant_state", "idle", {"message": "TV mode stopped."}))
            self.debug_log.write("server_tv_mode", source="server_tv_mode", outcome="stopped")
        return self.status()

    async def handle_camera_sync_event(self, event: Event) -> None:
        data = event.data or {}
        if data.get("server_owned"):
            return
        source = (data.get("source") or "server").strip().lower()
        if source == "settings":
            source = "server"
        if event.state == "on" and source == "server":
            await self.start(TvModeRequest(enabled=True, target=data.get("target")))
        elif event.state == "off":
            await self.stop(publish=False)

    async def handle_visual_state_event(self, event: Event) -> None:
        if event.state != "lights":
            return
        data = event.data or {}
        if data.get("source") == "server_tv_mode":
            return
        if self.task is not None and not self.task.done():
            await self.stop(publish=False)
            self.debug_log.write(
                "server_tv_mode",
                source="server_tv_mode",
                outcome="preempted",
                reason="new_light_state",
                event_data=data,
            )

    async def _run(self, options: TvModeRequest, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                red, green, blue = capture_server_camera_rgb(
                    self.settings.jarvis_camera_index,
                    options.profile,
                    options.intensity,
                )
                red, green, blue = smooth_camera_rgb(
                    options.target,
                    red,
                    green,
                    blue,
                    options.smoothing,
                    options.threshold,
                )
                targets = self.lamp.targets_for(options.target)
                successful = await self.lamp.set_rgb(
                    red,
                    green,
                    blue,
                    stop_effect=False,
                    targets=targets,
                )
                if not successful:
                    await self.lamp.check_targets(targets)
                    raise RuntimeError(self.lamp.connection_message())
                self.failure_count = 0
                self.last_error = None
                self.last_color = f"#{red:02X}{green:02X}{blue:02X}"
                await self.events.publish(
                    Event(
                        "visual_state",
                        "lights",
                        {
                            "power": "on",
                            "color": self.last_color,
                            "source": "server_tv_mode",
                            "profile": options.profile,
                        },
                    )
                )
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=options.delay_ms / 1000)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.failure_count += 1
                self.last_error = str(exc)
                self.debug_log.write(
                    "server_tv_mode",
                    source="server_tv_mode",
                    outcome="error",
                    options=options.model_dump(),
                    failure_count=self.failure_count,
                    error=error_details(exc),
                    lamp_errors=dict(getattr(self.lamp, "last_errors", {})),
                )
                await self.events.publish(
                    Event("assistant_state", "error", {"message": str(exc) or "TV mode connection issue."})
                )
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=min(10.0, 1.5 + self.failure_count))


class LotusAutoConnector:
    def __init__(
        self,
        lamp: LotusController,
        settings: Settings,
        debug_log: DebugLog,
    ) -> None:
        self.lamp = lamp
        self.settings = settings
        self.debug_log = debug_log
        self.task: asyncio.Task | None = None
        self.stop_event: asyncio.Event | None = None

    def start(self) -> None:
        if not self.settings.jarvis_lotus_auto_connect:
            return
        if not getattr(self.lamp, "controllers", None):
            return
        self.stop_event = asyncio.Event()
        self.task = asyncio.create_task(self._run(self.stop_event))

    async def stop(self) -> None:
        if self.stop_event is not None:
            self.stop_event.set()
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.task = None
        self.stop_event = None

    async def _run(self, stop_event: asyncio.Event) -> None:
        attempt = 0
        while not stop_event.is_set():
            attempt += 1
            try:
                reachable = await self.lamp.check_targets()
                persist_lamp_settings(self.lamp, self.settings)
                missing = [
                    controller
                    for controller in self.lamp.controllers
                    if controller not in reachable
                ]
                self.debug_log.write(
                    "lotus_auto_connect",
                    source="lotus_auto_connect",
                    outcome="connected" if not missing else "partial",
                    attempt=attempt,
                    reachable=reachable,
                    missing=missing,
                    lamp_errors=dict(getattr(self.lamp, "last_errors", {})),
                )
                if not missing:
                    return
            except Exception as exc:
                self.debug_log.write(
                    "lotus_auto_connect",
                    source="lotus_auto_connect",
                    outcome="error",
                    attempt=attempt,
                    error=error_details(exc),
                    lamp_errors=dict(getattr(self.lamp, "last_errors", {})),
                )
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=min(30.0, 2.0 + attempt))

def token_guard(settings: Settings = Depends(get_settings), authorization: str | None = Header(default=None)) -> None:
    if authorization != f"Bearer {settings.jarvis_api_token}": raise HTTPException(401, "Invalid or missing client token")

def lotus_controller_id(name: str, address: str, existing: set[str] | None = None) -> str:
    existing = existing or set()
    match = re.search(r"\b(\d{1,3})\b\s*$", name or "")
    if match:
        return match.group(1)
    tail = re.sub(r"[^0-9A-Fa-f]", "", address)[-4:] or str(len(existing) + 1)
    candidate = str(int(tail, 16)) if re.fullmatch(r"[0-9A-Fa-f]+", tail) else tail
    while candidate in existing:
        candidate = f"{candidate}x"
    return candidate

def lotus_label(controller: str, name: str, labels: dict[str, str]) -> str:
    if controller in labels:
        return labels[controller]
    if controller in CONTROLLERS:
        return CONTROLLERS[controller].get("label", f"Lotus {controller}")
    return name or f"Lotus {controller}"

def build_lotus_controller(settings: Settings) -> LotusController:
    labels = settings.lamp_labels
    if not settings.lamp_devices:
        return LotusController()
    controllers = {
        key: {
            "name": CONTROLLERS.get(key, {}).get("name", f"Lotus controller {key}"),
            "label": lotus_label(key, CONTROLLERS.get(key, {}).get("name", f"Lotus controller {key}"), labels),
            "address": address,
        }
        for key, address in settings.lamp_devices.items()
    }
    return LotusController(controllers)

def lotus_targets(lamp: LotusController) -> list[dict[str, str]]:
    targets = [{"value": "", "label": "Both"}]
    targets.extend(
        {"value": info.get("label") or info.get("name") or controller, "label": info.get("label") or info.get("name") or controller}
        for controller, info in lamp.controllers.items()
    )
    return targets

def persist_env_value(key: str, value: str, env_path: Path = Path(".env")) -> None:
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    replacement = f"{key}={value}"
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = replacement
            break
    else:
        lines.append(replacement)
    env_path.write_text("\n".join(lines) + "\n")

def serialized_lamp_devices(lamp: LotusController) -> str:
    return ",".join(
        f"{controller}={info.get('address', '')}"
        for controller, info in lamp.controllers.items()
        if info.get("address")
    )

def serialized_lamp_labels(lamp: LotusController) -> str:
    labels = {
        controller: info.get("label", "")
        for controller, info in lamp.controllers.items()
        if info.get("label")
    }
    return json.dumps(labels, separators=(",", ":"))


def lamp_controller_status(
    lamp: LotusController,
    reachable: list[str] | None = None,
) -> list[dict]:
    reachable_set = set(reachable or [])
    return [
        {
            "controller": controller,
            "name": info.get("name", ""),
            "label": info.get("label", ""),
            "address": info.get("address", ""),
            "reachable": controller in reachable_set if reachable is not None else None,
            "error": lamp.last_errors.get(controller, ""),
        }
        for controller, info in lamp.controllers.items()
    ]


def persist_lamp_settings(lamp: LotusController, settings: Settings) -> None:
    devices_value = serialized_lamp_devices(lamp)
    labels_value = serialized_lamp_labels(lamp)
    settings.lotus_lamp_devices = devices_value
    settings.lotus_lamp_labels = labels_value
    persist_env_value("LOTUS_LAMP_DEVICES", devices_value)
    persist_env_value("LOTUS_LAMP_LABELS", labels_value)


async def reconnect_lotus_lamps(lamp: LotusController, settings: Settings) -> dict:
    await lamp.stop_effect()
    await lamp.disconnect_all()
    reachable: list[str] = []
    remaining = list(lamp.controllers)
    for attempt in range(2):
        if not remaining:
            break
        connected = await lamp.check_targets(remaining)
        reachable.extend(
            controller
            for controller in connected
            if controller not in reachable
        )
        remaining = [
            controller
            for controller in remaining
            if controller not in connected
        ]
        if remaining and attempt == 0:
            await asyncio.sleep(0.6)
    persist_lamp_settings(lamp, settings)
    ok = len(reachable) == len(lamp.controllers)
    return {
        "ok": ok,
        "message": (
            "All paired Lotus lamps reconnected."
            if ok
            else lamp.connection_message()
        ),
        "controllers": lamp_controller_status(lamp, reachable),
        "targets": lotus_targets(lamp),
    }


async def pair_lotus_lamp(lamp: LotusController, settings: Settings, request: LotusPairRequest) -> dict:
    controllers = {
        controller: dict(info)
        for controller, info in lamp.controllers.items()
    }
    controller = request.controller or lotus_controller_id(request.name, request.address, set(controllers))
    label = (request.label or "").strip() or lotus_label(controller, request.name, settings.lamp_labels)
    controllers[controller] = {
        "name": request.name.strip(),
        "label": label,
        "address": request.address.strip(),
    }
    await lamp.update_controllers(controllers)
    reachable = await lamp.check_targets([controller])
    persist_lamp_settings(lamp, settings)
    return {
        "controller": controller,
        "name": request.name.strip(),
        "label": label,
        "address": lamp.controllers[controller].get("address", request.address.strip()),
        "reachable": controller in reachable,
        "error": lamp.last_errors.get(controller, ""),
        "targets": lotus_targets(lamp),
    }

def create_app(settings: Settings | None = None, lamp=None) -> FastAPI:
    settings = settings or get_settings()
    events, memory, tools = EventBus(), MemoryStore(settings.jarvis_database_path), ToolRegistry()
    tools.register(RememberTool(memory)); tools.register(RecallTool(memory))
    lamp = lamp or build_lotus_controller(settings)
    for tool in LotusTools(lamp, settings.lamp_scenes).all(): tools.register(tool)
    debug_log = DebugLog(settings.jarvis_debug_log_path, settings.jarvis_debug_logging)
    service = AssistantService(OllamaClient(settings.ollama_base_url, settings.ollama_model, settings.ollama_timeout_seconds), memory, tools, events)
    server_mic = ServerMic(service, events, debug_log=debug_log)
    server_tv_mode = ServerTvMode(lamp, settings, events, debug_log)
    lotus_auto_connector = LotusAutoConnector(lamp, settings, debug_log)

    async def automation_events() -> None:
        queue = events.subscribe()
        try:
            while True:
                event = await queue.get()
                if event.type == "camera_sync":
                    await server_tv_mode.handle_camera_sync_event(event)
                elif event.type == "visual_state":
                    await server_tv_mode.handle_visual_state_event(event)
                elif event.type == "music_sync" and event.state == "on":
                    await server_tv_mode.stop(publish=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            debug_log.write(
                "automation_event_loop",
                source="automation_events",
                outcome="error",
                error=error_details(exc),
            )
            raise
        finally:
            events.unsubscribe(queue)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service, app.state.events, app.state.settings, app.state.lamp, app.state.server_mic, app.state.debug_log, app.state.tools, app.state.server_tv_mode = service, events, settings, lamp, server_mic, debug_log, tools, server_tv_mode
        loop = asyncio.get_running_loop()
        previous_exception_handler = install_asyncio_noise_filter(debug_log)
        lotus_auto_connector.start()
        automation_task = asyncio.create_task(automation_events())
        try:
            yield
        finally:
            loop.set_exception_handler(previous_exception_handler)
            automation_task.cancel()
            with suppress(asyncio.CancelledError):
                await automation_task
            await server_tv_mode.stop(publish=False)
            await lotus_auto_connector.stop()
            await server_mic.stop()
            release_server_camera()
    app = FastAPI(title="Jarvis", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])
    client_dir = Path(__file__).parent / "client"
    app.mount("/static", StaticFiles(directory=client_dir), name="static")
    @app.get("/")
    async def index(): return FileResponse(client_dir / "index.html")
    @app.get("/api/health")
    async def health(): return {"status": "ok", "ollama": await service.ollama.health(), "model": settings.ollama_model}
    @app.get("/api/tools", dependencies=[Depends(token_guard)])
    async def list_tools():
        return {
            "tools": app.state.tools.metadata(),
            "lights": {
                "targets": lotus_targets(app.state.lamp),
                "colors": [
                    {"name": name, "red": red, "green": green, "blue": blue}
                    for name, (red, green, blue) in COLORS.items()
                ],
                "effects": [
                    {"name": name, "label": effect.name, "description": effect.description}
                    for name, effect in EFFECTS.items()
                ],
                "hardware_modes": list(HARDWARE_MODES.keys()),
            },
        }
    @app.post("/api/tools/call", response_model=ToolCallResponse, dependencies=[Depends(token_guard)])
    async def call_tool(request: ToolCallRequest):
        try:
            if tool_preempts_server_tv(request.name, request.arguments):
                await app.state.server_tv_mode.stop(publish=False)
            result = await app.state.tools.call(request.name, request.arguments)
        except Exception as exc:
            debug_log.write(
                "api_tool_outcome",
                source="api_tool",
                tool_name=request.name,
                arguments=request.arguments,
                outcome="error",
                error=error_details(exc),
                lamp_errors=dict(getattr(app.state.lamp, "last_errors", {})),
            )
            raise HTTPException(400, str(exc)) from exc
        for event in result.events:
            await events.publish(event)
        debug_log.write(
            "api_tool_outcome",
            source="api_tool",
            tool_name=request.name,
            arguments=request.arguments,
            outcome="success",
            content=result.content,
            data=result.data,
        )
        return ToolCallResponse(content=result.content, data=result.data)
    @app.get("/api/lotus/pairing/status", dependencies=[Depends(token_guard)])
    async def lotus_pairing_status():
        return {
            "controllers": lamp_controller_status(app.state.lamp),
            "targets": lotus_targets(app.state.lamp),
        }
    @app.post("/api/lotus/pairing/scan", dependencies=[Depends(token_guard)])
    async def lotus_pairing_scan():
        try:
            devices = await discover_lamps()
        except RuntimeError as exc:
            debug_log.write(
                "lotus_pairing_scan",
                source="api_lotus_pairing",
                outcome="error",
                error=error_details(exc),
            )
            raise HTTPException(503, str(exc)) from exc
        paired_addresses = {
            str(info.get("address", "")).lower(): controller
            for controller, info in app.state.lamp.controllers.items()
        }
        return {
            "devices": [
                {
                    "name": device.get("name", "Unnamed"),
                    "address": device.get("address", ""),
                    "controller": paired_addresses.get(str(device.get("address", "")).lower()),
                    "paired": str(device.get("address", "")).lower() in paired_addresses,
                    "likely_lotus": bool(re.search(r"\b(?:melk|lotus|of21)\b", device.get("name", ""), re.I)),
                }
                for device in devices
            ]
        }
    @app.post("/api/lotus/pairing/pair", dependencies=[Depends(token_guard)])
    async def lotus_pairing_pair(request: LotusPairRequest):
        try:
            result = await pair_lotus_lamp(app.state.lamp, settings, request)
        except Exception as exc:
            debug_log.write(
                "lotus_pairing_pair",
                source="api_lotus_pairing",
                outcome="error",
                request=request.model_dump(),
                error=error_details(exc),
                lamp_errors=dict(getattr(app.state.lamp, "last_errors", {})),
            )
            raise HTTPException(400, str(exc)) from exc
        message = (
            f"Paired and connected {result['label']}"
            if result.get("reachable")
            else f"Saved {result['label']}, but I could not connect yet."
        )
        await events.publish(Event("assistant_state", "idle" if result.get("reachable") else "error", {"message": message}))
        return result

    @app.post("/api/lotus/pairing/reconnect", dependencies=[Depends(token_guard)])
    async def lotus_pairing_reconnect():
        try:
            result = await reconnect_lotus_lamps(app.state.lamp, settings)
        except Exception as exc:
            debug_log.write(
                "lotus_pairing_reconnect",
                source="api_lotus_pairing",
                outcome="error",
                error=error_details(exc),
                lamp_errors=dict(getattr(app.state.lamp, "last_errors", {})),
            )
            raise HTTPException(503, str(exc)) from exc
        await events.publish(Event("assistant_state", "idle" if result["ok"] else "error", {"message": result["message"]}))
        return result
    @app.get("/api/server/mic/status", dependencies=[Depends(token_guard)])
    async def server_mic_status(): return app.state.server_mic.status()
    @app.post("/api/server/mic/start", dependencies=[Depends(token_guard)])
    async def server_mic_start():
        try: return await app.state.server_mic.start()
        except RuntimeError as exc: raise HTTPException(503, str(exc)) from exc
    @app.post("/api/server/mic/stop", dependencies=[Depends(token_guard)])
    async def server_mic_stop(): return await app.state.server_mic.stop()
    @app.get("/api/server/camera/status", dependencies=[Depends(token_guard)])
    async def camera_status(): return server_camera_status(settings.jarvis_camera_index)
    @app.get("/api/lotus/tv-mode/status", dependencies=[Depends(token_guard)])
    async def lotus_tv_mode_status(): return app.state.server_tv_mode.status()
    @app.post("/api/lotus/tv-mode/start", dependencies=[Depends(token_guard)])
    async def lotus_tv_mode_start(request: TvModeRequest):
        request.enabled = True
        return await app.state.server_tv_mode.start(request)
    @app.post("/api/lotus/tv-mode/stop", dependencies=[Depends(token_guard)])
    async def lotus_tv_mode_stop():
        return await app.state.server_tv_mode.stop()
    @app.get("/api/local-pair")
    async def local_pair(request: Request):
        """Pair a browser on the PC without exposing a token to the LAN."""
        if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
            raise HTTPException(403, "Local pairing is available only from the Jarvis PC")
        return {"token": settings.jarvis_api_token}
    @app.get("/api/cert")
    async def certificate():
        if not settings.jarvis_ssl_certfile.exists():
            raise HTTPException(404, "Jarvis has not generated a certificate yet.")
        return FileResponse(
            settings.jarvis_ssl_certfile,
            media_type="application/x-x509-ca-cert",
            filename="jarvis.local.crt",
        )
    @app.post("/api/chat", response_model=ChatResponse, dependencies=[Depends(token_guard)])
    async def chat(request: ChatRequest):
        try:
            reply, session_id = await service.handle(request.text, request.session_id)
        except Exception as exc:
            debug_log.write(
                "api_chat_outcome",
                source="api_chat",
                request_text=request.text,
                outcome="error",
                error=error_details(exc),
                assistant_trace=service.last_debug_trace,
            )
            raise HTTPException(503, str(exc)) from exc
        debug_log.write(
            "api_chat_outcome",
            source="api_chat",
            request_text=request.text,
            outcome="reply",
            session_id=session_id,
            reply=reply,
            assistant_trace=service.last_debug_trace,
        )
        return ChatResponse(reply=reply, session_id=session_id)
    @app.post("/api/client/log", dependencies=[Depends(token_guard)])
    async def client_log(request: ClientLogRequest):
        debug_log.write(
            "client_event",
            source="browser_client",
            client_event=request.event,
            data=request.data,
        )
        return {"ok": True}
    @app.post("/api/lotus/music-color", dependencies=[Depends(token_guard)])
    async def lotus_music_color(request: MusicLightRequest):
        try:
            targets = app.state.lamp.targets_for(request.target)
            successful = await app.state.lamp.set_rgb(
                request.red,
                request.green,
                request.blue,
                stop_effect=False,
                targets=targets,
            )
            if not successful:
                raise RuntimeError(app.state.lamp.connection_message())
        except Exception as exc:
            debug_log.write(
                "lotus_music_color",
                source="api_lotus_color",
                outcome="error",
                request=request.model_dump(),
                error=error_details(exc),
                lamp_errors=dict(getattr(app.state.lamp, "last_errors", {})),
            )
            raise HTTPException(503, str(exc)) from exc
        color = f"#{request.red:02X}{request.green:02X}{request.blue:02X}"
        await events.publish(Event("visual_state", "lights", {"power": "on", "color": color, "energy": request.energy}))
        return {"ok": True}
    @app.post("/api/lotus/camera-color", dependencies=[Depends(token_guard)])
    async def lotus_camera_color(request: CameraColorRequest):
        try:
            red, green, blue = capture_server_camera_rgb(
                settings.jarvis_camera_index,
                request.profile,
                request.intensity,
            )
            red, green, blue = smooth_camera_rgb(
                request.target,
                red,
                green,
                blue,
                request.smoothing,
                request.threshold,
            )
        except RuntimeError as exc:
            debug_log.write(
                "lotus_camera_color",
                source="api_lotus_color",
                outcome="error",
                request=request.model_dump(),
                error=error_details(exc),
                lamp_errors=dict(getattr(app.state.lamp, "last_errors", {})),
            )
            raise HTTPException(503, str(exc)) from exc
        targets = app.state.lamp.targets_for(request.target)
        successful = await app.state.lamp.set_rgb(
            red,
            green,
            blue,
            stop_effect=False,
            targets=targets,
        )
        if not successful:
            message = (
                app.state.lamp.connection_message()
                if hasattr(app.state.lamp, "connection_message")
                else "I'm having connection issues with the lights."
            )
            debug_log.write(
                "lotus_camera_color",
                source="api_lotus_color",
                outcome="error",
                request=request.model_dump(),
                error={"type": "LightConnectionError", "message": message},
                lamp_errors=dict(getattr(app.state.lamp, "last_errors", {})),
            )
            raise HTTPException(503, message)
        color = f"#{red:02X}{green:02X}{blue:02X}"
        await events.publish(Event("visual_state", "lights", {"power": "on", "color": color, "source": "server_camera", "profile": request.profile}))
        return {"ok": True, "color": color, "profile": request.profile}
    @app.get("/api/lotus/discover", dependencies=[Depends(token_guard)])
    async def lotus_discover(): return await discover_lamps()
    @app.websocket("/ws")
    async def websocket(websocket: WebSocket):
        protocol = websocket.headers.get("sec-websocket-protocol")
        expected_protocol = f"jarvis.{settings.jarvis_api_token}"
        if protocol != expected_protocol:
            await websocket.close(code=1008); return
        await websocket.accept(subprotocol=expected_protocol); queue = events.subscribe()
        mic_status = app.state.server_mic.status()
        initial_state = "listening" if mic_status.get("awaiting_command") else "idle"
        initial_message = (
            "Yes?"
            if mic_status.get("awaiting_command")
            else "Server microphone ready"
            if mic_status.get("running")
            else "Ready"
        )
        await websocket.send_json(
            Event("assistant_state", initial_state, {"message": initial_message}).wire()
        )
        try:
            while True: await websocket.send_json((await queue.get()).wire())
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        finally: events.unsubscribe(queue)
    return app

app = create_app()
