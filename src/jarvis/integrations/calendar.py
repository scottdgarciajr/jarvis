from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class CalendarEvent:
    title: str
    starts_at: datetime
    ends_at: datetime | None = None


class CalendarProvider(Protocol):
    async def events_between(self, start: datetime, end: datetime) -> list[CalendarEvent]: ...


class CanvasCalendarProvider:
    """Future adapter point. Deliberately performs no authentication or network access."""
    async def events_between(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        raise NotImplementedError("Canvas is intentionally a future integration; implement its authenticated API adapter here.")
