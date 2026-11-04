"""The handlers subscribed to an event bus, and which of them each event class goes to."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Hashable, Iterator, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, final

from sc2nachos.events._event_priority import EventPriority

if TYPE_CHECKING:
    from sc2nachos.events._event import Event
    from sc2nachos.events._handler import _Handler

# The priorities a handler subscribed while NachOS builds the extensions runs at, and no other handler.
_EXTENSION_PRIORITIES = frozenset({EventPriority.BEFORE, EventPriority.AFTER})


@final
class _EventSubscriptions:
    """The handlers subscribed to an event bus, and which of them each event class is handed to. The latter is worked
    out on first ask and cached until the handlers change."""

    __slots__ = (
        "_by_priority",
        "_count",
        "_handlers",
        "_keyed",
        "_wanted",
        "resolved",
        "selecting",
        "subscribing_extensions",
    )

    def __init__(self) -> None:
        # Each event type's handlers in run order, replaced whole on every change, so a handler subscribed while an
        # event is being handed out waits for the next.
        self._handlers: dict[type[Event], tuple[_Handler, ...]] = {}
        self.resolved: dict[type[Event], tuple[_Handler, ...]] = {}
        """The handlers of each event class asked for so far, its bases' included, in run order. `_EventBus.emit` reads
        it directly; a method call would slow it."""
        self.selecting: set[type[Event]] = set()
        """The resolved classes with a handler that selects by key or predicate."""
        # The same handlers grouped by priority, highest first, each group with whether one of it selects; and the keys
        # handlers select for each class, with the handlers that select them.
        self._by_priority: dict[type[Event], tuple[tuple[EventPriority, tuple[_Handler, ...], bool], ...]] = {}
        self._wanted: dict[type[Event], tuple[frozenset[Hashable], tuple[_Handler, ...]]] = {}
        # How many handlers have ever subscribed, which numbers each one to order one priority's handlers across
        # classes, and how many currently subscribed select by key.
        self._count = 0
        self._keyed = 0
        self.subscribing_extensions = False
        """Whether NachOS is building the extensions, whose handlers alone run at `EventPriority.BEFORE` and `AFTER`."""

    def __iter__(self) -> Iterator[_Handler]:
        """Every handler subscribed."""
        return (handler for handlers in self._handlers.values() for handler in handlers)

    @property
    def by_type(self) -> Mapping[type[Event], tuple[_Handler, ...]]:
        """The handlers subscribed to each event type, in the order they run."""
        return MappingProxyType(self._handlers)

    @property
    def any_keyed(self) -> bool:
        """Whether any subscribed handler selects by key."""
        return self._keyed > 0

    def check_priority(self, priority: EventPriority) -> None:
        """Raise `ValueError` unless a handler subscribed now may run at `priority`: one of the extensions' while NachOS
        builds the extensions, and one of the bot's otherwise."""
        if self.subscribing_extensions:
            if priority not in _EXTENSION_PRIORITIES:
                raise ValueError(f"an extension's handler runs at BEFORE or AFTER, not {priority!s}")
        elif priority in _EXTENSION_PRIORITIES:
            raise ValueError(f"{priority!s} is for the handlers subscribed while NachOS builds the extensions")

    def add(self, handler: _Handler) -> None:
        """Subscribe `handler`, after those of its priority already subscribed. Raises `ValueError` for a priority it
        may not run at, as `check_priority` says."""
        self.check_priority(handler.priority)
        handler.order = self._count
        self._count += 1
        self._keyed += handler.selects.keys is not None
        event_type = handler.selects.event_type
        handlers = list(self._handlers.get(event_type, ()))
        handlers.insert(bisect_right(handlers, _run_order(handler), key=_run_order), handler)
        self._handlers[event_type] = tuple(handlers)
        self._forget()

    def remove(self, removing: Callable[[_Handler], bool]) -> bool:
        """Drop the handlers `removing` picks. Returns whether there were any."""
        removed = False
        for event_type, handlers in list(self._handlers.items()):
            kept = []
            for handler in handlers:
                if removing(handler):
                    # So an event being handed out skips it too.
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

    def resolve(self, event_type: type[Event]) -> tuple[_Handler, ...]:
        """The handlers events of `event_type` are handed to, its bases' included, in run order. Notes whether any of
        them selects by key or predicate."""
        found = [handler for cls in event_type.__mro__ for handler in self._handlers.get(cls, ())]
        found.sort(key=_run_order)
        if any(_selects(handler) for handler in found):
            self.selecting.add(event_type)
        resolved = self.resolved[event_type] = tuple(found)
        return resolved

    def grouped(self, event_type: type[Event]) -> tuple[tuple[EventPriority, tuple[_Handler, ...], bool], ...]:
        """The handlers events of `event_type` are handed to, grouped by priority, highest first, each group with
        whether one of it selects by key or predicate."""
        if (grouped := self._by_priority.get(event_type)) is None:
            if (handlers := self.resolved.get(event_type)) is None:
                handlers = self.resolve(event_type)
            groups: dict[EventPriority, list[_Handler]] = {}
            for handler in handlers:
                groups.setdefault(handler.priority, []).append(handler)
            grouped = self._by_priority[event_type] = tuple(
                (priority, tuple(group), any(_selects(handler) for handler in group))
                for priority, group in groups.items()
            )
        return grouped

    def wants_every(self, event_type: type[Event]) -> bool:
        """Whether a handler not yet done takes every event of `event_type` its predicate passes: one subscribed to it
        or a base of it without `only` or `of`."""
        if (handlers := self.resolved.get(event_type)) is None:
            handlers = self.resolve(event_type)
        return bool(handlers) and any(not handler.done and handler.selects.keys is None for handler in handlers)

    def wanted_keys(self, event_type: type[Event]) -> frozenset[Hashable]:
        """The keys of `event_type` that handlers not yet done select through `only` or `of`. The same set is returned
        until one of those handlers is done or the handlers change; a unit type group makes a set of hundreds."""
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
        """Forget which handlers each event class is handed to, after the handlers change."""
        self.resolved.clear()
        self.selecting.clear()
        self._by_priority.clear()
        self._wanted.clear()


_NO_KEYS: frozenset[Hashable] = frozenset()


def _run_order(handler: _Handler) -> tuple[int, int]:
    """Where `handler` runs among the handlers of an event: by priority, highest first, then in subscription order."""
    return -handler.priority, handler.order


def _selects(handler: _Handler) -> bool:
    """Whether `handler` selects some of the events of its type, by key or predicate."""
    return handler.selects.keys is not None or handler.selects.predicate is not None
