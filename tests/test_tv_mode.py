import asyncio

import pytest

from jarvis.config import Settings
from jarvis.debug_log import DebugLog
from jarvis.events import Event, EventBus
from jarvis.main import LotusAutoConnector, ServerTvMode, TvModeRequest, tool_preempts_server_tv


class FakeLamp:
    def __init__(self):
        self.controllers = {
            "25": {
                "name": "MELK-OF21 25",
                "label": "TV lights",
                "address": "BE:16:54:00:29:25",
            }
        }
        self.last_errors = {}
        self.colors = []
        self.checks = 0

    def targets_for(self, _target=None):
        return ["25"]

    def connection_message(self):
        return "I'm having connection issues with TV lights."

    async def stop_effect(self):
        pass

    async def set_rgb(self, red, green, blue, **_kwargs):
        self.colors.append((red, green, blue))
        return ["25"]

    async def check_targets(self, _targets=None):
        self.checks += 1
        return ["25"]


@pytest.mark.asyncio
async def test_server_tv_mode_runs_until_stopped(monkeypatch, tmp_path):
    lamp = FakeLamp()
    events = EventBus()
    debug_log = DebugLog(tmp_path / "debug.jsonl")
    settings = Settings(jarvis_database_path=tmp_path / "db.sqlite3")
    monkeypatch.setattr("jarvis.main.capture_server_camera_rgb", lambda *_args: (120, 80, 40))

    tv_mode = ServerTvMode(lamp, settings, events, debug_log)
    await tv_mode.start(TvModeRequest(delay_ms=100, smoothing=0, threshold=0))
    await asyncio.sleep(0.16)
    status = tv_mode.status()
    await tv_mode.stop()

    assert status["running"] is True
    assert lamp.colors
    assert tv_mode.status()["running"] is False


@pytest.mark.asyncio
async def test_server_tv_mode_stops_when_effect_takes_over(monkeypatch, tmp_path):
    lamp = FakeLamp()
    events = EventBus()
    debug_log = DebugLog(tmp_path / "debug.jsonl")
    settings = Settings(jarvis_database_path=tmp_path / "db.sqlite3")
    monkeypatch.setattr("jarvis.main.capture_server_camera_rgb", lambda *_args: (120, 80, 40))

    tv_mode = ServerTvMode(lamp, settings, events, debug_log)
    await tv_mode.start(TvModeRequest(delay_ms=100, smoothing=0, threshold=0))
    assert tv_mode.status()["running"] is True

    await tv_mode.handle_visual_state_event(
        Event("visual_state", "lights", {"effect": "candlelight", "color": "#481C07"})
    )

    assert tv_mode.status()["running"] is False


def test_lotus_tools_preempt_server_tv():
    assert tool_preempts_server_tv("lotus_set_effect", {"effect": "ocean"}) is True
    assert tool_preempts_server_tv("lotus_set_color", {"color": "purple"}) is True
    assert tool_preempts_server_tv("lotus_music_sync", {"enabled": True}) is True
    assert tool_preempts_server_tv("lotus_camera_sync", {"enabled": True, "source": "server"}) is False
    assert tool_preempts_server_tv("lotus_camera_sync", {"enabled": False}) is True
    assert tool_preempts_server_tv("lotus_list_effects", {}) is False


@pytest.mark.asyncio
async def test_lotus_auto_connector_retries_until_reachable(monkeypatch, tmp_path):
    class SlowLamp(FakeLamp):
        async def check_targets(self, _targets=None):
            self.checks += 1
            if self.checks == 1:
                self.last_errors = {"25": "not ready"}
                return []
            self.last_errors = {}
            return ["25"]

    lamp = SlowLamp()
    settings = Settings(jarvis_database_path=tmp_path / "db.sqlite3")
    connector = LotusAutoConnector(lamp, settings, DebugLog(tmp_path / "debug.jsonl"))
    monkeypatch.setattr("jarvis.main.persist_lamp_settings", lambda *_args: None)

    await asyncio.wait_for(connector._run(asyncio.Event()), timeout=4)

    assert lamp.checks == 2
