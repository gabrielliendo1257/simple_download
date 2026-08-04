from __future__ import annotations

import inspect
from collections import defaultdict
from typing import Any, Awaitable, Callable, TypeVar

EventT = TypeVar("EventT")


class EventType:
    """Event type"""


class EventBus:
    def __init__(self) -> None:
        self.__subs: dict[type, list[Callable[[Any], Any]]] = defaultdict(list)

    def subscribe(
        self,
        event_type: type[EventT],
        handler: Callable[[EventT], Any],
    ) -> None:
        self.__subs[event_type].append(handler)

    async def publish(self, event: Any) -> None:
        for handler in self.__subs.get(type(event), []):
            result = handler(event)
            if inspect.isawaitable(result):
                await result