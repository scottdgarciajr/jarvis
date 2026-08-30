from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

LOG_SCHEMA_VERSION = 1


def jarvis_version() -> str:
    try:
        return version("jarvis-home")
    except PackageNotFoundError:
        return "unknown"


def source_revision() -> dict[str, Any]:
    root = Path(__file__).resolve()
    for parent in root.parents:
        if (parent / ".git").exists():
            try:
                commit = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=parent,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                status = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=parent,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
                return {"git_revision": commit, "git_dirty": bool(status.strip())}
            except Exception:
                break
    return {"git_revision": None, "git_dirty": None}


class DebugLog:
    def __init__(self, path: Path | None, enabled: bool = True) -> None:
        self.path = path
        self.enabled = enabled
        self._version = jarvis_version()
        self._source = source_revision()

    def write(self, event: str, **data: Any) -> None:
        if not self.enabled or self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "log_schema_version": LOG_SCHEMA_VERSION,
            "jarvis_version": self._version,
            **self._source,
            **data,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
