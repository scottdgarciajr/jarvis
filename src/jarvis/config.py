from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    jarvis_bind_host: str = "127.0.0.1"
    jarvis_port: int = 8000
    jarvis_https: bool = False
    jarvis_ssl_certfile: Path = Path("data/jarvis.local.crt")
    jarvis_ssl_keyfile: Path = Path("data/jarvis.local.key")
    jarvis_api_token: str = "change-this-to-a-long-random-secret"
    jarvis_allowed_origins: str = "http://localhost:8000"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2"
    ollama_timeout_seconds: float = 90
    jarvis_database_path: Path = Path("data/jarvis.sqlite3")
    jarvis_debug_log_path: Path = Path("data/jarvis-debug.jsonl")
    jarvis_debug_logging: bool = True
    jarvis_camera_index: int = 0
    jarvis_lotus_auto_connect: bool = True
    lotus_lamp_devices: str = ""
    lotus_lamp_labels: str = "{}"
    lotus_lamp_scenes: str = "{}"

    @property
    def allowed_origins(self) -> list[str]:
        return [x.strip() for x in self.jarvis_allowed_origins.split(",") if x.strip()]

    @property
    def lamp_devices(self) -> dict[str, str]:
        items = re.split(r"[\n,]+", self.lotus_lamp_devices)
        return dict(item.strip().split("=", 1) for item in items if "=" in item)

    @property
    def lamp_labels(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in json.loads(self.lotus_lamp_labels or "{}").items()}

    @property
    def lamp_scenes(self) -> dict[str, int]:
        return {str(k): int(v) for k, v in json.loads(self.lotus_lamp_scenes).items()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
