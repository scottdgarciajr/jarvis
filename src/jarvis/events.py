from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Event:
    type: str
    state: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def wire(self) -> dict[str, Any]:
        return asdict(self)


class EventBus:
    """Fan-out event bus. Clients render events; tools never manipulate the UI."""
    def __init__(self) -> None:
        self._listeners: set[asyncio.Queue[Event]] = set()

    def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._listeners.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        self._listeners.discard(queue)

    async def publish(self, event: Event) -> None:
        for queue in tuple(self._listeners):
            await queue.put(event)
