from __future__ import annotations

import asyncio
import logging
import random
from contextlib import suppress
from typing import Awaitable, Callable

from pydantic import BaseModel, Field

from jarvis.events import Event
from jarvis.tools.base import Permission, ToolResult

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    BleakClient = None
    BleakScanner = None


# ============================================================
# LOTUS LAMP CONFIGURATION
# ============================================================

CONTROLLERS = {
    "25": {
        "name": "MELK-OF21 25",
        "label": "TV lights",
        "address": "BE:16:54:00:29:25",
    },
    "49": {
        "name": "MELK-OF21 49",
        "label": "Loft lights",
        "address": "BE:16:54:00:58:49",
    },
}

FFF3 = "0000fff3-0000-1000-8000-00805f9b34fb"
log = logging.getLogger(__name__)


# ============================================================
# COLORS
# ============================================================

COLORS = {
    "red": (255, 0, 0),
    "orange": (255, 128, 0),
    "yellow": (255, 255, 0),
    "green": (0, 255, 0),
    "cyan": (0, 255, 255),
    "blue": (0, 0, 255),
    "purple": (128, 0, 255),
    "pink": (255, 0, 128),
    "white": (255, 255, 255),
}


# ============================================================
# PROTOCOL
# ============================================================

def static_rgb_packet(red: int, green: int, blue: int) -> bytes:
    """
    Confirmed LotusLamp X static RGB packet.

        7E 07 05 03 RR GG BB 10 EF
    """

    return bytes(
        [
            0x7E,
            0x07,
            0x05,
            0x03,
            int(red) & 0xFF,
            int(green) & 0xFF,
            int(blue) & 0xFF,
            0x10,
            0xEF,
        ]
    )


KNOWN_PACKETS = {
    "sunrise_sunset": "7E 07 03 FF 00 00 00 00 EF",
    "traveling_red": "7E 07 03 FF 00 00 FF 00 EF",
    "traveling_green": "7E 07 03 00 FF 00 00 00 EF",
    "white_return": "7E 07 0E 01 00 00 FF 00 EF",
    "dim": "7E 07 01 03 FF 00 00 10 EF",
    "brighten": "7E 07 01 FF 00 00 01 00 EF",
}


def packet_from_hex(value: str) -> bytes:
    return bytes.fromhex(value)


# ============================================================
# INPUT MODELS
# ============================================================

class Targeted(BaseModel):
    target: str | None = Field(
        default=None,
        max_length=60,
        description=(
            "Optional light target. Use 'tv lights' for MELK 25, "
            "'loft lights' for MELK 49, or omit for both."
        ),
    )


class Empty(Targeted):
    pass


class Rgb(Targeted):
    red: int = Field(ge=0, le=255)
    green: int = Field(ge=0, le=255)
    blue: int = Field(ge=0, le=255)


class Color(Targeted):
    color: str = Field(min_length=1, max_length=30)


class Brightness(Targeted):
    brightness: int = Field(ge=0, le=100)
    red: int | None = Field(default=None, ge=0, le=255)
    green: int | None = Field(default=None, ge=0, le=255)
    blue: int | None = Field(default=None, ge=0, le=255)


class Effect(Targeted):
    effect: str = Field(min_length=1, max_length=60)


class EffectSequence(Targeted):
    effects: list[str] = Field(min_length=1, max_length=12)
    seconds_each: int = Field(default=45, ge=5, le=3600)


class Speed(BaseModel):
    speed: int = Field(ge=0, le=100)


class HardwareMode(Targeted):
    mode: str = Field(min_length=1, max_length=60)


class MusicSync(BaseModel):
    enabled: bool = True


class CameraSync(Targeted):
    enabled: bool = True
    source: str | None = Field(
        default=None,
        max_length=20,
        description="Optional camera source: 'server' for PC camera or 'local' for browser camera.",
    )


# ============================================================
# EFFECT DEFINITIONS
# ============================================================

class LotusEffect:
    def __init__(
        self,
        name: str,
        description: str,
        runner: Callable[["LotusController", asyncio.Event], Awaitable[None]],
    ):
        self.name = name
        self.description = description
        self.runner = runner


# ============================================================
# CONTROLLER
# ============================================================

class LotusController:
    """
    Async LotusLamp X controller.

    This is intentionally independent of Tkinter.
    Both Jarvis and a future GUI can use this class.
    """

    def __init__(
        self,
        controllers: dict[str, dict[str, str]] | None = None,
    ) -> None:

        self.controllers = controllers or CONTROLLERS

        self.clients: dict[str, BleakClient | None] = {
            controller: None
            for controller in self.controllers
        }

        self.lock = asyncio.Lock()

        self.effect_task: asyncio.Task | None = None
        self.effect_stop: asyncio.Event | None = None
        self.effect_targets: list[str] | None = None
        self.last_errors: dict[str, str] = {}
        self.current_colors: dict[str, tuple[int, int, int]] = {
            controller: (255, 255, 255)
            for controller in self.controllers
        }

    def controller_label(self, controller: str) -> str:
        info = self.controllers.get(controller, {})
        return info.get("label") or info.get("name") or controller

    async def update_controllers(self, controllers: dict[str, dict[str, str]]) -> None:
        await self.stop_effect()
        await self.disconnect_all()
        self.controllers = controllers
        self.clients = {
            controller: None
            for controller in self.controllers
        }
        self.last_errors = {}
        self.current_colors = {
            controller: (255, 255, 255)
            for controller in self.controllers
        }

    def targets_for(self, target: str | None = None) -> list[str] | None:
        if target is None or not target.strip():
            return None

        value = (
            target.strip()
            .lower()
            .replace("-", " ")
            .replace("_", " ")
        )
        value = " ".join(value.split())

        aliases = {
            "all": None,
            "both": None,
            "lights": None,
            "tv": ["25"],
            "tv light": ["25"],
            "tv lights": ["25"],
            "melk 25": ["25"],
            "melk of21 25": ["25"],
            "25": ["25"],
            "loft": ["49"],
            "loft light": ["49"],
            "loft lights": ["49"],
            "melk 49": ["49"],
            "melk of21 49": ["49"],
            "49": ["49"],
        }

        if value in aliases:
            return aliases[value]

        for controller, info in self.controllers.items():
            name = " ".join(
                info.get("name", "").lower().replace("-", " ").replace("_", " ").split()
            )
            label = " ".join(
                info.get("label", "").lower().replace("-", " ").replace("_", " ").split()
            )
            if value == name or value in name or value == label or value in label:
                return [controller]

        raise ValueError(
            "Unknown light target. Available targets: TV lights, loft lights, or both."
        )

    def connection_message(self) -> str:
        if not self.last_errors:
            return "I'm having connection issues with the lights."
        names = ", ".join(
            self.controller_label(controller)
            for controller in self.last_errors
        )
        return f"I'm having connection issues with {names}."

    # --------------------------------------------------------
    # Discovery
    # --------------------------------------------------------

    async def discover(self, controller: str):

        if BleakScanner is None:
            raise RuntimeError(
                "BLE support is not installed. "
                "Install Jarvis with the Lotus dependency."
            )

        info = self.controllers[controller]

        target_name = info["name"].lower()
        target_address = info["address"].lower()

        devices = await BleakScanner.discover(
            timeout=5.0
        )

        # Prefer address.
        for device in devices:

            address = str(
                device.address
            ).lower()

            if address == target_address:
                return device

        # Fall back to name.
        for device in devices:

            name = (
                device.name or ""
            ).strip().lower()

            if name == target_name:
                return device

        return None

    # --------------------------------------------------------
    # Connect
    # --------------------------------------------------------

    async def connect(self, controller: str):

        if BleakClient is None:
            raise RuntimeError(
                "BLE support is not installed. "
                "Install Jarvis with the Lotus dependency."
            )

        existing = self.clients.get(controller)

        if existing is not None:

            try:
                if existing.is_connected:
                    return existing
            except Exception:
                pass

            with suppress(Exception):
                await existing.disconnect()

            self.clients[controller] = None

        info = self.controllers[controller]
        direct_error: Exception | None = None
        address = info.get("address", "").strip()

        if address:
            direct_client = None
            try:
                direct_client = BleakClient(address)
                await asyncio.wait_for(direct_client.connect(), timeout=6.0)

                if direct_client.is_connected:
                    self.clients[controller] = direct_client
                    self.last_errors.pop(controller, None)
                    return direct_client

                raise RuntimeError(
                    f"Failed to connect to Lotus controller {controller} by saved address."
                )

            except Exception as exc:
                direct_error = exc
                log.info(
                    "Lotus controller %s saved-address connection failed: %s",
                    controller,
                    exc,
                )

                if direct_client is not None:
                    with suppress(Exception):
                        if direct_client.is_connected:
                            await direct_client.disconnect()

        device = await self.discover(controller)

        if device is None:
            message = (
                f"Lotus controller {controller} "
                f"({self.controllers[controller]['name']}) "
                "was not found."
            )
            if direct_error is not None:
                message += f" Saved address connection also failed: {direct_error}"
            raise RuntimeError(
                message
            )

        discovered_address = str(getattr(device, "address", "") or "").strip()
        if discovered_address and discovered_address != info.get("address", ""):
            info["address"] = discovered_address

        client = BleakClient(device)

        await asyncio.wait_for(client.connect(), timeout=8.0)

        if not client.is_connected:
            raise RuntimeError(
                f"Failed to connect to Lotus controller {controller}."
            )

        self.clients[controller] = client
        self.last_errors.pop(controller, None)

        return client

    # --------------------------------------------------------
    # Disconnect
    # --------------------------------------------------------

    async def disconnect(self, controller: str):

        client = self.clients.get(controller)

        if client is None:
            return

        try:

            if client.is_connected:
                await client.disconnect()

        finally:
            self.clients[controller] = None

    async def disconnect_all(self):

        await asyncio.gather(
            *(
                self.disconnect(controller)
                for controller in self.controllers
            )
        )

    # --------------------------------------------------------
    # Write
    # --------------------------------------------------------

    async def write(
        self,
        controller: str,
        packet: bytes,
    ) -> bool:

        last_error: Exception | None = None

        for attempt in range(5):
            try:
                client = await self.connect(controller)
            except Exception as exc:
                last_error = exc
                self.clients[controller] = None
                log.info(
                    "Lotus controller %s connect attempt %s failed before write: %s",
                    controller,
                    attempt + 1,
                    exc,
                )
                await asyncio.sleep(min(1.0, 0.2 + attempt * 0.2))
                continue

            try:

                await client.write_gatt_char(
                    FFF3,
                    packet,
                    response=False,
                )

                self.last_errors.pop(controller, None)
                return True

            except Exception as exc:
                last_error = exc
                log.info(
                    "Lotus controller %s write attempt %s failed: %s",
                    controller,
                    attempt + 1,
                    exc,
                )

                with suppress(Exception):

                    if client.is_connected:
                        await client.disconnect()

                self.clients[controller] = None
                await asyncio.sleep(min(1.0, 0.2 + attempt * 0.2))

        if last_error is not None:
            self.last_errors[controller] = str(last_error)

        return False

    async def send(
        self,
        packet: bytes,
        targets: list[str] | None = None,
    ) -> list[str]:

        successful: list[str] = []
        selected = targets or list(self.controllers)
        self.last_errors = {}

        async with self.lock:

            for controller in selected:

                success = await self.write(
                    controller,
                    packet,
                )

                if success:
                    successful.append(controller)

        return successful

    async def check_targets(
        self,
        targets: list[str] | None = None,
    ) -> list[str]:

        successful: list[str] = []
        selected = targets or list(self.controllers)
        self.last_errors = {}

        async with self.lock:
            for controller in selected:
                try:
                    await self.connect(controller)
                    successful.append(controller)
                except Exception as exc:
                    self.last_errors[controller] = str(exc)

        return successful

    # ========================================================
    # EFFECT CONTROL
    # ========================================================

    async def stop_effect(self):

        if self.effect_stop is not None:
            self.effect_stop.set()

        task = self.effect_task

        if task is not None:

            task.cancel()

            with suppress(
                asyncio.CancelledError
            ):
                await task

        self.effect_task = None
        self.effect_stop = None
        self.effect_targets = None

    async def start_effect(
        self,
        effect_name: str,
        targets: list[str] | None = None,
    ):

        effect = EFFECTS.get(
            effect_name.lower()
        )

        if effect is None:

            available = ", ".join(
                sorted(EFFECTS)
            )

            raise ValueError(
                f"Unknown effect {effect_name!r}. "
                f"Available effects: {available}"
            )

        await self.stop_effect()

        reachable = await self.check_targets(targets)
        if not reachable:
            return []

        stop_event = asyncio.Event()

        self.effect_stop = stop_event
        self.effect_targets = targets

        async def runner():

            try:

                await effect.runner(
                    self,
                    stop_event,
                )

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                selected = self.effect_targets or list(self.controllers)
                for controller in selected:
                    self.last_errors[controller] = str(exc)

            finally:

                if self.effect_stop is stop_event:
                    self.effect_stop = None
                    self.effect_task = None
                    self.effect_targets = None

        self.effect_task = asyncio.create_task(
            runner()
        )

        return reachable

    async def start_sequence(
        self,
        effect_names: list[str],
        seconds_each: int,
        targets: list[str] | None = None,
    ):

        normalized = []
        for name in effect_names:
            effect_name = normalize_effect_name(name)
            if effect_name not in EFFECTS:
                raise ValueError(
                    f"Unknown effect {name!r}. Available effects: {', '.join(EFFECTS)}"
                )
            normalized.append(effect_name)

        await self.stop_effect()

        reachable = await self.check_targets(targets)
        if not reachable:
            return []

        stop_event = asyncio.Event()
        self.effect_stop = stop_event
        self.effect_targets = targets

        async def runner():
            try:
                while not stop_event.is_set():
                    for effect_name in normalized:
                        if stop_event.is_set():
                            return
                        task = asyncio.create_task(
                            EFFECTS[effect_name].runner(self, stop_event)
                        )
                        try:
                            await asyncio.wait_for(
                                stop_event.wait(),
                                timeout=seconds_each,
                            )
                        except asyncio.TimeoutError:
                            pass
                        finally:
                            task.cancel()
                            with suppress(asyncio.CancelledError):
                                await task
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                selected = self.effect_targets or list(self.controllers)
                for controller in selected:
                    self.last_errors[controller] = str(exc)
            finally:
                if self.effect_stop is stop_event:
                    self.effect_stop = None
                    self.effect_task = None
                    self.effect_targets = None

        self.effect_task = asyncio.create_task(runner())

        return reachable

    # ========================================================
    # RGB
    # ========================================================

    async def set_rgb(
        self,
        red: int,
        green: int,
        blue: int,
        stop_effect: bool = True,
        targets: list[str] | None = None,
    ) -> list[str]:

        if stop_effect:
            await self.stop_effect()

        packet = static_rgb_packet(
            red,
            green,
            blue,
        )

        selected = targets
        if selected is None and not stop_effect:
            selected = self.effect_targets

        successful = await self.send(packet, selected)
        for controller in successful:
            self.current_colors[controller] = (red, green, blue)
        return successful

    def current_rgb_for_target(
        self,
        targets: list[str] | None = None,
    ) -> tuple[int, int, int]:

        selected = targets or list(self.controllers)
        colors = [
            self.current_colors.get(controller, (255, 255, 255))
            for controller in selected
        ]
        if not colors:
            return (255, 255, 255)
        return tuple(
            round(sum(color[index] for color in colors) / len(colors))
            for index in range(3)
        )


# ============================================================
# EFFECT IMPLEMENTATIONS
# ============================================================

def blend_rgb(
    start: tuple[int, int, int],
    end: tuple[int, int, int],
    amount: float,
) -> tuple[int, int, int]:

    smooth = amount * amount * (3 - 2 * amount)

    return (
        int(start[0] + (end[0] - start[0]) * smooth),
        int(start[1] + (end[1] - start[1]) * smooth),
        int(start[2] + (end[2] - start[2]) * smooth),
    )


async def send_rgb_wait(
    lamp: LotusController,
    stop: asyncio.Event,
    color: tuple[int, int, int],
    delay: float,
) -> bool:

    if stop.is_set():
        return True

    await lamp.set_rgb(
        *color,
        stop_effect=False,
    )

    try:
        await asyncio.wait_for(
            stop.wait(),
            timeout=delay,
        )
        return True
    except asyncio.TimeoutError:
        return False


async def fade_palette(
    lamp: LotusController,
    stop: asyncio.Event,
    palette: list[tuple[int, int, int]],
    steps: int,
    delay: float,
) -> None:

    current = random.choice(palette)

    while not stop.is_set():
        target = random.choice(palette)

        for i in range(steps):
            if stop.is_set():
                return

            color = blend_rgb(
                current,
                target,
                i / (steps - 1),
            )

            if await send_rgb_wait(lamp, stop, color, delay):
                return

        current = target


async def effect_candlelight(
    lamp: LotusController,
    stop: asyncio.Event,
):

    palette = [
        (42, 14, 3),
        (54, 18, 4),
        (66, 24, 5),
        (78, 30, 7),
        (88, 38, 10),
        (58, 20, 4),
    ]

    current = random.choice(palette)

    while not stop.is_set():
        target = random.choice(palette)

        for i in range(30):
            color = blend_rgb(
                current,
                target,
                i / 29,
            )

            warmth = random.randint(-3, 4)
            color = (
                max(0, color[0] + warmth),
                max(0, color[1] + warmth // 2),
                max(0, color[2] - 1),
            )

            if await send_rgb_wait(
                lamp,
                stop,
                color,
                random.uniform(0.18, 0.34),
            ):
                return

        current = target


async def effect_date_night(
    lamp: LotusController,
    stop: asyncio.Event,
):

    await fade_palette(
        lamp,
        stop,
        [
            (52, 18, 78),
            (70, 26, 96),
            (86, 32, 110),
            (98, 38, 122),
            (112, 42, 116),
            (74, 22, 88),
        ],
        steps=120,
        delay=0.16,
    )


async def effect_reading(
    lamp: LotusController,
    stop: asyncio.Event,
):

    target = (
        190,
        154,
        92,
    )

    for i in range(45):
        brightness = (i + 1) / 45
        color = (
            int(target[0] * brightness),
            int(target[1] * brightness),
            int(target[2] * brightness),
        )

        if await send_rgb_wait(
            lamp,
            stop,
            color,
            0.06,
        ):
            return

    while not stop.is_set():
        if await send_rgb_wait(
            lamp,
            stop,
            target,
            4.0,
        ):
            return


async def effect_ember(
    lamp: LotusController,
    stop: asyncio.Event,
):

    await fade_palette(
        lamp,
        stop,
        [
            (42, 8, 1),
            (58, 12, 2),
            (74, 18, 3),
            (94, 28, 6),
            (118, 42, 10),
        ],
        steps=90,
        delay=0.20,
    )


async def effect_moonlight(
    lamp: LotusController,
    stop: asyncio.Event,
):

    await fade_palette(
        lamp,
        stop,
        [
            (28, 38, 68),
            (34, 48, 82),
            (44, 58, 92),
            (54, 64, 96),
            (38, 46, 76),
        ],
        steps=140,
        delay=0.18,
    )


async def effect_ocean(
    lamp: LotusController,
    stop: asyncio.Event,
):

    await fade_palette(
        lamp,
        stop,
        [
            (0, 28, 52),
            (0, 44, 70),
            (0, 62, 82),
            (10, 78, 92),
            (18, 96, 102),
        ],
        steps=110,
        delay=0.15,
    )


async def effect_aurora(
    lamp: LotusController,
    stop: asyncio.Event,
):

    await fade_palette(
        lamp,
        stop,
        [
            (18, 70, 54),
            (26, 92, 76),
            (42, 70, 112),
            (56, 42, 118),
            (22, 84, 96),
        ],
        steps=130,
        delay=0.14,
    )


async def effect_stormwatch(
    lamp: LotusController,
    stop: asyncio.Event,
):

    await fade_palette(
        lamp,
        stop,
        [
            (18, 20, 34),
            (28, 32, 48),
            (34, 38, 58),
            (42, 44, 64),
            (24, 30, 48),
        ],
        steps=120,
        delay=0.18,
    )


async def effect_sunrise(
    lamp: LotusController,
    stop: asyncio.Event,
):

    await fade_palette(
        lamp,
        stop,
        [
            (18, 4, 0),
            (54, 14, 4),
            (92, 34, 10),
            (132, 62, 22),
            (170, 96, 44),
            (190, 132, 76),
        ],
        steps=180,
        delay=0.18,
    )


async def effect_lagoon(
    lamp: LotusController,
    stop: asyncio.Event,
):

    await fade_palette(
        lamp,
        stop,
        [
            (0, 48, 44),
            (0, 72, 62),
            (10, 96, 76),
            (24, 116, 86),
            (4, 82, 92),
        ],
        steps=120,
        delay=0.16,
    )


async def effect_nebula(
    lamp: LotusController,
    stop: asyncio.Event,
):

    await fade_palette(
        lamp,
        stop,
        [
            (42, 10, 76),
            (64, 16, 104),
            (28, 32, 112),
            (18, 68, 100),
            (92, 28, 86),
        ],
        steps=130,
        delay=0.15,
    )


async def effect_fireplace(
    lamp: LotusController,
    stop: asyncio.Event,
):

    await fade_palette(
        lamp,
        stop,
        [
            (78, 16, 2),
            (112, 30, 4),
            (146, 52, 10),
            (170, 78, 22),
            (104, 24, 5),
        ],
        steps=52,
        delay=0.12,
    )


async def effect_soft_rain(
    lamp: LotusController,
    stop: asyncio.Event,
):

    await fade_palette(
        lamp,
        stop,
        [
            (18, 26, 42),
            (22, 38, 54),
            (28, 48, 62),
            (34, 54, 68),
            (20, 34, 50),
        ],
        steps=150,
        delay=0.16,
    )


EFFECTS = {
    "candlelight": LotusEffect(
        "Candlelight",
        "Dim amber candlelight with a slow calming flicker.",
        effect_candlelight,
    ),

    "date_night": LotusEffect(
        "Date Night",
        "Soft romantic violet and purple ambience.",
        effect_date_night,
    ),

    "reading": LotusEffect(
        "Reading",
        "Comfortable slightly warm white lighting for reading.",
        effect_reading,
    ),

    "ember": LotusEffect(
        "Ember",
        "Low orange-red coals with very slow breathing.",
        effect_ember,
    ),

    "moonlight": LotusEffect(
        "Moonlight",
        "Quiet cool blue ambience for night.",
        effect_moonlight,
    ),

    "ocean": LotusEffect(
        "Ocean",
        "Slow blue-green movement.",
        effect_ocean,
    ),

    "aurora": LotusEffect(
        "Aurora",
        "Slow green, teal, and violet drift.",
        effect_aurora,
    ),

    "stormwatch": LotusEffect(
        "Stormwatch",
        "Dim steel-blue ambience.",
        effect_stormwatch,
    ),

    "sunrise": LotusEffect(
        "Sunrise",
        "Slow amber wake-up glow.",
        effect_sunrise,
    ),

    "lagoon": LotusEffect(
        "Lagoon",
        "Deep teal and aquatic green drift.",
        effect_lagoon,
    ),

    "nebula": LotusEffect(
        "Nebula",
        "Purple, blue, and teal space ambience.",
        effect_nebula,
    ),

    "fireplace": LotusEffect(
        "Fireplace",
        "Warmer livelier firelight.",
        effect_fireplace,
    ),

    "soft_rain": LotusEffect(
        "Soft Rain",
        "Dim blue-gray rainy ambience.",
        effect_soft_rain,
    ),
}


EFFECT_COLORS = {
    "candlelight": (72, 28, 7),
    "date_night": (92, 34, 124),
    "reading": (190, 154, 92),
    "ember": (96, 22, 4),
    "moonlight": (42, 78, 150),
    "ocean": (28, 138, 154),
    "aurora": (58, 198, 142),
    "stormwatch": (46, 66, 94),
    "sunrise": (218, 116, 38),
    "lagoon": (22, 124, 116),
    "nebula": (86, 68, 170),
    "fireplace": (178, 72, 18),
    "soft_rain": (42, 70, 92),
}


EFFECT_ALIASES = {
    "candle_light": "candlelight",
    "candle_lights": "candlelight",
    "candelights": "candlelight",
    "candlelights": "candlelight",
    "canelight": "candlelight",
    "cane_light": "candlelight",
    "candle": "candlelight",
    "date": "date_night",
    "romantic": "date_night",
    "night": "moonlight",
    "moon_light": "moonlight",
    "storm": "stormwatch",
    "storm_watch": "stormwatch",
    "focus": "reading",
    "study": "reading",
    "rain": "soft_rain",
    "soft rain": "soft_rain",
    "fire": "fireplace",
    "space": "nebula",
    "wake": "sunrise",
    "tv_mode": "tv_mode",
}


def normalize_effect_name(name: str) -> str:
    value = (
        name.strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )
    return EFFECT_ALIASES.get(value, value)


def music_sync_off_event() -> Event:
    return Event(
        "music_sync",
        "off",
        {
            "enabled": False,
        },
    )


def hex_color(red: int, green: int, blue: int) -> str:
    return f"#{red:02X}{green:02X}{blue:02X}"


def effect_color(effect_name: str) -> str:
    red, green, blue = EFFECT_COLORS.get(effect_name, (255, 190, 70))
    return hex_color(red, green, blue)


def device_result(
    lamp,
    content: str,
    data: dict,
    events: list[Event] | None = None,
) -> ToolResult:
    events = events or []
    if "controllers" in data and not data["controllers"]:
        return ToolResult(
            lamp.connection_message()
            if hasattr(lamp, "connection_message")
            else "I'm having connection issues with the lights.",
            data,
            [
                music_sync_off_event(),
                Event(
                    "assistant_state",
                    "error",
                    {
                        "message": "Light connection issue",
                    },
                ),
            ],
        )
    if getattr(lamp, "last_errors", None):
        return ToolResult(
            f"{content} {lamp.connection_message()}",
            data,
            events,
        )
    return ToolResult(content, data, events)


# ============================================================
# HARDWARE MODES
# ============================================================

HARDWARE_MODES = {
    "sunrise_sunset": "7E 07 03 FF 00 00 00 00 EF",
    "traveling_red": "7E 07 03 FF 00 00 FF 00 EF",
    "traveling_green": "7E 07 03 00 FF 00 00 00 EF",
    "white_return": "7E 07 0E 01 00 00 FF 00 EF",
    "dim": "7E 07 01 03 FF 00 00 10 EF",
    "brighten": "7E 07 01 FF 00 00 01 00 EF",
}


# ============================================================
# JARVIS TOOLS
# ============================================================

class LotusTools:

    def __init__(
        self,
        lamp: LotusController,
        scenes: dict[str, int] | None = None,
    ) -> None:

        self.lamp = lamp
        self.scenes = scenes or {}

    def all(self):

        return [
            self.PowerOn(self.lamp),
            self.PowerOff(self.lamp),
            self.SetColor(self.lamp),
            self.SetRgb(self.lamp),
            self.SetBrightness(self.lamp),
            self.ListEffects(),
            self.SetEffect(self.lamp),
            self.SequenceEffects(self.lamp),
            self.MusicSync(self.lamp),
            self.CameraSync(self.lamp),
            self.StopEffect(self.lamp),
            self.SetHardwareMode(self.lamp),
        ]

    # ========================================================
    # POWER ON
    # ========================================================

    class PowerOn:

        name = "lotus_turn_on"

        description = (
            "Turn on the configured Lotus Lamp lights. "
            "Use this when the user asks to turn the lights on."
        )

        permission = Permission.DEVICE_CONTROL
        input_model = Empty

        def __init__(
            self,
            lamp: LotusController,
        ):
            self.lamp = lamp

        async def execute(self, args):

            await self.lamp.stop_effect()
            targets = self.lamp.targets_for(args.get("target"))

            # White is the safest general-purpose
            # "turn on" behavior.
            successful = await self.lamp.set_rgb(
                255,
                255,
                255,
                targets=targets,
            )

            return device_result(
                self.lamp,
                "Lights turned on.",
                {
                    "controllers": successful,
                },
                [
                    music_sync_off_event(),
                    Event(
                        "visual_state",
                        "lights",
                        {
                            "power": "on",
                            "color": "#FFFFFF",
                        },
                    )
                ],
            )

    # ========================================================
    # POWER OFF
    # ========================================================

    class PowerOff:

        name = "lotus_turn_off"

        description = (
            "Turn off the configured Lotus Lamp lights."
        )

        permission = Permission.DEVICE_CONTROL
        input_model = Empty

        def __init__(
            self,
            lamp: LotusController,
        ):
            self.lamp = lamp

        async def execute(self, args):

            await self.lamp.stop_effect()
            targets = self.lamp.targets_for(args.get("target"))

            successful = await self.lamp.set_rgb(
                0,
                0,
                0,
                targets=targets,
            )

            return device_result(
                self.lamp,
                "Lights turned off.",
                {
                    "controllers": successful,
                },
                [
                    music_sync_off_event(),
                    Event(
                        "visual_state",
                        "lights",
                        {
                            "power": "off",
                        },
                    )
                ],
            )

    # ========================================================
    # COLOR NAME
    # ========================================================

    class SetColor:

        name = "lotus_set_color"

        description = (
            "Set the Lotus Lamp to a named color. "
            "Available colors: red, orange, yellow, green, "
            "cyan, blue, purple, pink, white."
        )

        permission = Permission.DEVICE_CONTROL
        input_model = Color

        def __init__(
            self,
            lamp: LotusController,
        ):
            self.lamp = lamp

        async def execute(self, args):

            color = args["color"].strip().lower()

            if color not in COLORS:

                raise ValueError(
                    f"Unknown color {color!r}. "
                    f"Available colors: "
                    f"{', '.join(COLORS)}"
                )

            red, green, blue = COLORS[color]
            targets = self.lamp.targets_for(args.get("target"))

            successful = await self.lamp.set_rgb(
                red,
                green,
                blue,
                targets=targets,
            )

            hex_color = (
                f"#{red:02X}"
                f"{green:02X}"
                f"{blue:02X}"
            )

            return device_result(
                self.lamp,
                f"Lights set to {color}.",
                {
                    "color": color,
                    "controllers": successful,
                },
                [
                    music_sync_off_event(),
                    Event(
                        "visual_state",
                        "lights",
                        {
                            "power": "on",
                            "color": hex_color,
                        },
                    )
                ],
            )

    # ========================================================
    # RAW RGB
    # ========================================================

    class SetRgb:

        name = "lotus_set_rgb"

        description = (
            "Set the Lotus Lamp to an exact RGB color. "
            "Each channel must be between 0 and 255."
        )

        permission = Permission.DEVICE_CONTROL
        input_model = Rgb

        def __init__(
            self,
            lamp: LotusController,
        ):
            self.lamp = lamp

        async def execute(self, args):
            targets = self.lamp.targets_for(args.get("target"))

            successful = await self.lamp.set_rgb(
                args["red"],
                args["green"],
                args["blue"],
                targets=targets,
            )

            hex_color = (
                f"#{args['red']:02X}"
                f"{args['green']:02X}"
                f"{args['blue']:02X}"
            )

            return device_result(
                self.lamp,
                f"Lights set to {hex_color}.",
                {
                    **args,
                    "controllers": successful,
                },
                [
                    music_sync_off_event(),
                    Event(
                        "visual_state",
                        "lights",
                        {
                            "power": "on",
                            "color": hex_color,
                        },
                    )
                ],
            )

    # ========================================================
    # BRIGHTNESS
    # ========================================================

    class SetBrightness:

        name = "lotus_set_brightness"

        description = (
            "Set Lotus Lamp brightness from 0 to 100 percent. "
            "Use this when the user asks to make the lights "
            "brighter or dimmer."
        )

        permission = Permission.DEVICE_CONTROL
        input_model = Brightness

        def __init__(
            self,
            lamp: LotusController,
        ):
            self.lamp = lamp

        async def execute(self, args):

            # The original GUI has a brightness slider that
            # scales RGB values. We reproduce that behavior
            # by setting white at the requested brightness.
            level = args["brightness"]
            targets = self.lamp.targets_for(args.get("target"))
            if (
                args.get("red") is not None
                and args.get("green") is not None
                and args.get("blue") is not None
            ):
                red, green, blue = args["red"], args["green"], args["blue"]
            elif hasattr(self.lamp, "current_rgb_for_target"):
                red, green, blue = self.lamp.current_rgb_for_target(targets)
            else:
                red, green, blue = (255, 255, 255)
            scale = level / 100

            successful = await self.lamp.set_rgb(
                round(red * scale),
                round(green * scale),
                round(blue * scale),
                targets=targets,
            )
            color = hex_color(round(red * scale), round(green * scale), round(blue * scale))

            return device_result(
                self.lamp,
                f"Brightness set to {level} percent.",
                {
                    **args,
                    "color": color,
                    "controllers": successful,
                },
                [
                    music_sync_off_event(),
                    Event(
                        "visual_state",
                        "lights",
                        {
                            "power": "on" if level > 0 else "off",
                            "color": color,
                        },
                    ),
                ],
            )

    # ========================================================
    # LIST EFFECTS
    # ========================================================

    class ListEffects:

        name = "lotus_list_effects"

        description = (
            "List the available Lotus Lamp atmosphere effects. "
            "Use this when the user asks what light effects, "
            "lighting scenes, or lamp modes are available."
        )

        permission = Permission.SAFE
        input_model = Empty

        async def execute(self, _args):

            names = ", ".join(
                effect.name
                for effect in EFFECTS.values()
            )

            return ToolResult(
                f"Available light effects: {names}.",
                {
                    "effects": list(EFFECTS.keys()),
                },
            )

    # ========================================================
    # SOFTWARE EFFECT
    # ========================================================

    class SetEffect:

        name = "lotus_set_effect"

        description = (
            "Set a Lotus Lamp atmosphere effect. "
            "Available effects: "
            "candlelight, date_night, reading, ember, "
            "moonlight, ocean, aurora, stormwatch, sunrise, "
            "lagoon, nebula, fireplace, soft_rain. "
            "Use candlelight for dim calming amber. "
            "Use ember for very low orange-red coals. "
            "Use moonlight for quiet blue night ambience. "
            "Use ocean for slow blue-green movement. "
            "Use aurora for green, teal, and violet drift. "
            "Use stormwatch for dim steel-blue ambience. "
            "Use sunrise for slow amber wake-up light. "
            "Use lagoon for deep teal aquatic ambience. "
            "Use nebula for purple, blue, and teal ambience. "
            "Use fireplace for livelier warm firelight. "
            "Use soft_rain for dim blue-gray rainy ambience. "
            "Use reading for warm focus light."
        )

        permission = Permission.DEVICE_CONTROL
        input_model = Effect

        def __init__(
            self,
            lamp: LotusController,
        ):
            self.lamp = lamp

        async def execute(self, args):

            effect_name = (
                normalize_effect_name(
                    args["effect"]
                )
            )

            effect = EFFECTS.get(
                effect_name
            )

            if effect is None:

                raise ValueError(
                    f"Unknown effect {args['effect']!r}. "
                    f"Available effects: "
                    f"{', '.join(EFFECTS)}"
                )

            targets = self.lamp.targets_for(args.get("target"))

            successful = await self.lamp.start_effect(
                effect_name,
                targets=targets,
            )

            return device_result(
                self.lamp,
                f"{effect.name} effect started.",
                {
                    "effect": effect_name,
                    "controllers": successful,
                },
                [
                    music_sync_off_event(),
                    Event(
                        "visual_state",
                        "lights",
                        {
                            "power": "on",
                            "effect": effect_name,
                            "color": effect_color(effect_name),
                        },
                    )
                ],
            )

    # ========================================================
    # EFFECT SEQUENCE
    # ========================================================

    class SequenceEffects:

        name = "lotus_sequence_effects"

        description = (
            "Run multiple Lotus Lamp atmosphere effects in a repeating sequence. "
            "Use this when the user asks to chain, rotate, cycle, or sequence effects."
        )

        permission = Permission.DEVICE_CONTROL
        input_model = EffectSequence

        def __init__(
            self,
            lamp: LotusController,
        ):
            self.lamp = lamp

        async def execute(self, args):

            targets = self.lamp.targets_for(args.get("target"))
            effects = [
                normalize_effect_name(effect)
                for effect in args["effects"]
            ]

            successful = await self.lamp.start_sequence(
                effects,
                args["seconds_each"],
                targets=targets,
            )

            names = ", ".join(
                EFFECTS[effect].name
                for effect in effects
                if effect in EFFECTS
            )

            return device_result(
                self.lamp,
                f"Effect sequence started: {names}.",
                {
                    "effects": effects,
                    "seconds_each": args["seconds_each"],
                    "controllers": successful,
                },
                [
                    music_sync_off_event(),
                    Event(
                        "visual_state",
                        "lights",
                        {
                            "power": "on",
                            "effect_sequence": effects,
                            "color": effect_color(effects[0]) if effects else "#FFBE46",
                        },
                    )
                ],
            )

    # ========================================================
    # MUSIC SYNC
    # ========================================================

    class MusicSync:

        name = "lotus_music_sync"

        description = (
            "Start or stop syncing the Lotus Lamp colors to music "
            "heard by the browser microphone. Use this when the user "
            "asks to sync the lights to music."
        )

        permission = Permission.DEVICE_CONTROL
        input_model = MusicSync

        def __init__(
            self,
            lamp: LotusController,
        ):
            self.lamp = lamp

        async def execute(self, args):

            await self.lamp.stop_effect()

            enabled = args["enabled"]

            return ToolResult(
                "Music sync started. Play music near this browser."
                if enabled
                else "Music sync stopped.",
                {
                    "enabled": enabled,
                },
                [
                    Event(
                        "music_sync",
                        "on" if enabled else "off",
                        {
                            "enabled": enabled,
                        },
                    )
                ],
            )

    # ========================================================
    # CAMERA SYNC
    # ========================================================

    class CameraSync:

        name = "lotus_camera_sync"

        description = (
            "Start or stop matching the Lotus Lamp color to what a camera sees. "
            "Use source 'server' for the Jarvis PC camera and 'local' for the browser camera. "
            "Use this when the user says TV mode; TV mode should target all paired lights unless a target is explicit."
        )

        permission = Permission.DEVICE_CONTROL
        input_model = CameraSync

        def __init__(
            self,
            lamp: LotusController,
        ):
            self.lamp = lamp

        async def execute(self, args):

            await self.lamp.stop_effect()

            enabled = args["enabled"]
            source = (args.get("source") or "settings").strip().lower()
            target = args.get("target")

            return ToolResult(
                "Camera color match started."
                if enabled
                else "Camera color match stopped.",
                {
                    "enabled": enabled,
                    "source": source,
                    "target": target,
                },
                [
                    music_sync_off_event(),
                    Event(
                        "camera_sync",
                        "on" if enabled else "off",
                        {
                            "enabled": enabled,
                            "source": source,
                            "target": target,
                        },
                    )
                ],
            )

    # ========================================================
    # STOP EFFECT
    # ========================================================

    class StopEffect:

        name = "lotus_stop_effect"

        description = (
            "Stop the currently running Lotus Lamp software effect."
        )

        permission = Permission.DEVICE_CONTROL
        input_model = Empty

        def __init__(
            self,
            lamp: LotusController,
        ):
            self.lamp = lamp

        async def execute(self, _args):

            await self.lamp.stop_effect()

            return ToolResult(
                "Light effect stopped.",
                events=[
                    music_sync_off_event(),
                ],
            )

    # ========================================================
    # HARDWARE MODE
    # ========================================================

    class SetHardwareMode:

        name = "lotus_set_hardware_mode"

        description = (
            "Activate a known built-in Lotus Lamp hardware mode. "
            "Available modes: sunrise_sunset, traveling_red, "
            "traveling_green, white_return, dim, brighten."
        )

        permission = Permission.DEVICE_CONTROL
        input_model = HardwareMode

        def __init__(
            self,
            lamp: LotusController,
        ):
            self.lamp = lamp

        async def execute(self, args):

            mode = (
                args["mode"]
                .strip()
                .lower()
                .replace(" ", "_")
                .replace("-", "_")
                .replace("/", "_")
            )

            packet_hex = HARDWARE_MODES.get(
                mode
            )

            if packet_hex is None:

                raise ValueError(
                    f"Unknown hardware mode {args['mode']!r}. "
                    f"Available modes: "
                    f"{', '.join(HARDWARE_MODES)}"
                )

            await self.lamp.stop_effect()
            targets = self.lamp.targets_for(args.get("target"))

            successful = await self.lamp.send(
                packet_from_hex(
                    packet_hex
                ),
                targets,
            )

            return device_result(
                self.lamp,
                f"Hardware mode {mode} activated.",
                {
                    "mode": mode,
                    "controllers": successful,
                },
                [
                    music_sync_off_event(),
                ],
            )
