from __future__ import annotations

import asyncio
from typing import Protocol


class LampClient(Protocol):
    async def power_on(self) -> None: ...
    async def power_off(self) -> None: ...
    async def set_rgb(self, red: int, green: int, blue: int, **kwargs) -> None: ...
    async def set_brightness(self, level: int) -> None: ...
    async def set_animation(self, mode: int) -> None: ...
    async def set_speed(self, level: int) -> None: ...


class LotusLampAdapter:
    """Optional PC-side adapter for lotus-lamp. The thin client never sees BLE."""
    def __init__(self, address: str) -> None:
        self.address = address
        self._lamp = None

    async def _connected(self):
        if self._lamp is None:
            try:
                from lotus_lamp import DeviceConfig, LotusLamp
            except ImportError as exc:
                raise RuntimeError("Lotus support is optional. Install with: pip install -e '.[lotus]'") from exc
            self._lamp = LotusLamp(device_config=DeviceConfig(name="Jarvis Lamp", address=self.address))
            await self._lamp.connect()
        return self._lamp

    async def power_on(self) -> None: await (await self._connected()).power_on()
    async def power_off(self) -> None: await (await self._connected()).power_off()
    async def set_rgb(self, red: int, green: int, blue: int, **_kwargs) -> None: await (await self._connected()).set_rgb(red, green, blue)
    async def set_brightness(self, level: int) -> None: await (await self._connected()).set_brightness(level)
    async def set_animation(self, mode: int) -> None: await (await self._connected()).set_animation(mode)
    async def set_speed(self, level: int) -> None: await (await self._connected()).set_speed(level)


class MockLamp:
    """Useful for development and tests; records commands without BLE hardware."""
    def __init__(self) -> None:
        self.commands: list[tuple] = []
        self.current = (255, 255, 255)
    def targets_for(self, _target=None): return None
    def connection_message(self): return "I'm having connection issues with the lights."
    def current_rgb_for_target(self, _targets=None): return self.current
    async def power_on(self): self.commands.append(("on",))
    async def power_off(self): self.commands.append(("off",))
    async def stop_effect(self): self.commands.append(("stop_effect",))
    async def start_effect(self, effect_name, **_kwargs):
        self.commands.append(("effect", effect_name))
        return ["mock"]
    async def set_rgb(self, red, green, blue, **_kwargs):
        self.commands.append(("rgb", red, green, blue))
        self.current = (red, green, blue)
        return ["mock"]
    async def set_brightness(self, level): self.commands.append(("brightness", level))
    async def set_animation(self, mode): self.commands.append(("animation", mode))
    async def set_speed(self, level): self.commands.append(("speed", level))


async def discover_lamps(timeout: float = 8) -> list[dict[str, str]]:
    try:
        try:
            from bleak.backends.winrt.util import uninitialize_sta
            uninitialize_sta()
        except Exception:
            pass
        from bleak import BleakScanner
    except ImportError as exc:
        raise RuntimeError("Install lotus-lamp to scan Bluetooth devices.") from exc
    try:
        devices = await BleakScanner.discover(timeout=timeout)
    except Exception as exc:
        raise RuntimeError(
            "Bluetooth scan failed. Make sure Bluetooth is on, close any other app that is scanning BLE, then try Scan again."
        ) from exc
    return [{"name": device.name or "Unnamed", "address": device.address} for device in devices]
