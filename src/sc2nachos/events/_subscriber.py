"""What `EventBus.on` answers: the decorator that subscribes a handler."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from types import FunctionType, MethodType
from typing import TYPE_CHECKING, Any, final

from sc2nachos.events._event_filter import EventFilter
from sc2nachos.events._handler import _Handler

if TYPE_CHECKING:
    from sc2nachos.events._event_bus import EventBus


@final
class _Subscriber:
    """A decorator that subscribes the function it decorates to the events it selects, or marks the method it decorates
    for `EventBus.subscribe`, with how often to call it. `where` narrows what it selects."""

    __slots__ = ("_bus", "_options", "_selects")

    def __init__(self, bus: EventBus, selects: EventFilter[Any], options: dict[str, Any]) -> None:
        self._bus = bus
        self._selects = selects
        # What `on` was given beside what it selects: the priority, the cadence and whether to catch exceptions.
        self._options = options

    def __call__[F: Callable[..., object]](self, handler: F, /) -> F:
        function: object = handler
        instance = None
        if isinstance(function, MethodType):
            function, instance = function.__func__, function.__self__
        if not isinstance(function, FunctionType):
            raise TypeError(f"a handler is a function or a method, not {handler!r}")
        if inspect.iscoroutinefunction(function):
            raise TypeError(f"{function.__qualname__} is a coroutine function, and handlers are called synchronously")
        self._bus._add(_Handler(function, self._selects, instance=instance, **self._options))
        return handler

    def where(self, predicate: Callable[[Any], bool], /) -> _Subscriber:
        """This decorator, handing on only the events `predicate` passes too."""
        selects = self._selects
        if (first := selects.predicate) is None:
            both = predicate
        else:

            def both(event: Any, /) -> bool:
                return first(event) and predicate(event)

        narrowed = EventFilter(selects.event_type, keys=selects.keys, predicate=both)
        return _Subscriber(self._bus, narrowed, self._options)
