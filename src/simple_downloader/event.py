from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Awaitable, Callable
from uuid import UUID

from simple_downloader.process import DownloadProgress


class EventBus:
    def __init__(self):
        self.__subs: dict[
            type, list[Callable[[EventType], Awaitable[None]]]
        ] = defaultdict(list)

    def subscribe(
        self,
        event_type: type[EventType],
        handler: Callable[[EventType], Awaitable[None]],
    ) -> None:
        self.__subs.setdefault(event_type, []).append(handler)

    async def publish(self, event: EventType):
        print(f"Publish: {event}")
        for handler in self.__subs.get(type(event), []):
            try:
                await handler(event)
            except Exception as e:
                pass
                # logging.error(f"Handler failed for {type(event).__name__}: {e}")


class EventType:
    """Event type"""

@dataclass(frozen=True)
class DownloadProgressEvent(EventType):
    id: UUID
    progress: DownloadProgress
