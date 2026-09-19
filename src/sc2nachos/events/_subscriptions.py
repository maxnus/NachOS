"""The handlers subscribed to an event bus, and which of them each event class goes to."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Hashable, Iterator, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from sc2nachos.events._event import Event
    from sc2nachos.events._event_priority import EventPriority
    from sc2nachos.events._handler import _Handler


@final
class _Subscriptions:
    """The handlers subscribed to an event bus, and which of them the events of each class are handed to: worked out
    on the first ask since the handlers last changed, and kept until they change again."""

    __slots__ = ("_by_priority", "_count", "_handlers", "_keyed", "_wanted", "resolved", "selecting")

    def __init__(self) -> None:
        # Each event type's handlers in the order they run, replaced whole on every change, so that one subscribed
        # while an event is being handed out waits for the next.
        self._handlers: dict[type[Event], tuple[_Handler, ...]] = {}
        self.resolved: dict[type[Event], tuple[_Handler, ...]] = {}
        """The handlers of each event class asked for, those of its bases included, in the order they run. Read by
        `EventBus.emit` directly, which a method call would slow."""
        self.selecting: set[type[Event]] = set()
        """The classes among those one of whose handlers selects by key or predicate."""
        # The same handlers grouped by priority, highest first, and the keys they select with those selecting them.
        self._by_priority: dict[type[Event], tuple[tuple[EventPriority, tuple[_Handler, ...]], ...]] = {}
        self._wanted: dict[type[Event], tuple[frozenset[Hashable], tuple[_Handler, ...]]] = {}
        # How many handlers have subscribed, which numbers each to order the handlers of one priority across classes,
        # and how many subscribed now select by key.
        self._count = 0
        self._keyed = 0

    def __iter__(self) -> Iterator[_Handler]:
        """Every handler subscribed."""
        return (handler for handlers in self._handlers.values() for handler in handlers)

    @property
    def by_type(self) -> Mapping[type[Event], tuple[_Handler, ...]]:
        """The handlers subscribed to each event type, in the order they run."""
        return MappingProxyType(self._handlers)

    @property
    def any_keyed(self) -> bool:
        """Whether a handler subscribed selects by key."""
        return self._keyed > 0

    def add(self, handler: _Handler) -> None:
        """Subscribe `handler`, after those of its priority already subscribed."""
        handler.order = self._count
        self._count += 1
        self._keyed += handler.selects.keys is not None
        event_type = handler.selects.event_type
        handlers = list(self._handlers.get(event_type, ()))
        handlers.insert(bisect_right(handlers, -handler.priority, key=lambda other: -other.priority), handler)
        self._handlers[event_type] = tuple(handlers)
        self._forget()

    def remove(self, removing: Callable[[_Handler], bool]) -> bool:
        """Drop the handlers `removing` picks, and say whether there were any."""
        removed = False
        for event_type, handlers in list(self._handlers.items()):
            kept = []
            for handler in handlers:
                if removing(handler):
                    # So that an event being handed out skips it too. It is in no list any more, so no new game
                    # starts it afresh.
                    handler.done = removed = True
                    self._keyed -= handler.selects.keys is not None
                else:
                    kept.append(handler)
            if not kept:
                del self._handlers[event_type]
            elif len(kept) < len(handlers):
                self._handlers[event_type] = tuple(kept)
        if removed:
            self._forget()
        return removed

    def start_game(self) -> None:
        """Forget what every handler has done."""
        for handler in self:
            handler.last_step = None
            handler.done = False
        self._wanted.clear()

    def resolve(self, event_type: type[Event]) -> tuple[_Handler, ...]:
        """The handlers the events of `event_type` are handed to, those of its bases included, in the order they run,
        noting whether any of them selects by key or predicate."""
        found = [handler for cls in event_type.__mro__ for handler in self._handlers.get(cls, ())]
        found.sort(key=lambda handler: (-handler.priority, handler.order))
        if any(handler.selects.keys is not None or handler.selects.predicate is not None for handler in found):
            self.selecting.add(event_type)
        resolved = self.resolved[event_type] = tuple(found)
        return resolved

    def grouped(self, event_type: type[Event]) -> tuple[tuple[EventPriority, tuple[_Handler, ...]], ...]:
        """The handlers the events of `event_type` are handed to, grouped by priority, highest first."""
        if (grouped := self._by_priority.get(event_type)) is None:
            if (handlers := self.resolved.get(event_type)) is None:
                handlers = self.resolve(event_type)
            groups: dict[EventPriority, list[_Handler]] = {}
            for handler in handlers:
                groups.setdefault(handler.priority, []).append(handler)
            grouped = self._by_priority[event_type] = tuple(
                (priority, tuple(group)) for priority, group in groups.items()
            )
        return grouped

    def wants_every(self, event_type: type[Event]) -> bool:
        """Whether a handler not done takes every event of `event_type` that its predicate passes: one subscribed to
        it or to a base of it, without `only` or `of`."""
        if (handlers := self.resolved.get(event_type)) is None:
            handlers = self.resolve(event_type)
        return bool(handlers) and any(not handler.done and handler.selects.keys is None for handler in handlers)

    def wanted_keys(self, event_type: type[Event]) -> frozenset[Hashable]:
        """The keys of `event_type` a handler not done selects through `only` or `of`: the same set until one of those
        handlers is done or the handlers change, since a unit type group makes a set of hundreds."""
        if not self._keyed:
            return _NO_KEYS
        if (wanted := self._wanted.get(event_type)) is not None and not any(handler.done for handler in wanted[1]):
            return wanted[0]
        if (handlers := self.resolved.get(event_type)) is None:
            handlers = self.resolve(event_type)
        if event_type not in self.selecting:
            return _NO_KEYS
        selecting = tuple(handler for handler in handlers if not handler.done and handler.selects.keys is not None)
        keys = frozenset().union(*(handler.selects.keys for handler in selecting if handler.selects.keys is not None))
        self._wanted[event_type] = (keys, selecting)
        return keys

    def _forget(self) -> None:
        """Forget which handlers each event class is handed to, since they have changed."""
        self.resolved.clear()
        self.selecting.clear()
        self._by_priority.clear()
        self._wanted.clear()


_NO_KEYS: frozenset[Hashable] = frozenset()
