"""The decorator `api.on` returns, which subscribes a handler."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from types import FunctionType, MethodType
from typing import TYPE_CHECKING, Any, final, overload

from sc2nachos.events._event import Event
from sc2nachos.events._event_filter import EventFilter

if TYPE_CHECKING:
    from sc2nachos.events._event_bus import _EventBus


@final
class EventSubscriber[E: Event]:
    """The decorator `api.on` returns, for a handler of `E`: a function taking one, or a method taking one after
    `self`. It subscribes the function it decorates to the events it selects, or marks the method it decorates for
    `api.subscribe`, with the options `on` was given. `where` narrows what it selects.

    A handler taking any `Event` keeps its own type, so it can be decorated for several events. One taking a narrower
    type is typed as a callable of the event it is decorated for.
    """

    __slots__ = ("_bus", "_options", "_selects")

    def __init__(self, bus: _EventBus, selects: EventFilter[Any], options: dict[str, Any]) -> None:
        """A decorator subscribing to `bus` what `selects` selects, with `options`, the rest of what `on` was given,
        which only `on` checks."""
        self._bus = bus
        self._selects = selects
        # The rest of what `on` was given: the priority, the cadence and whether to catch exceptions.
        self._options = options

    # A function taking any `Event` keeps its own type, so it can be decorated for several event types.
    @overload
    def __call__[F: Callable[[Event], object]](self, handler: F, /) -> F: ...

    # A method taking any `Event` after `self`, likewise.
    @overload
    def __call__[F: Callable[[Any, Event], object]](self, handler: F, /) -> F: ...

    # A function taking the event it is decorated for, or a base of it, typed as a callable of that event; one taking
    # another event matches no overload, so a type checker refuses it.
    @overload
    def __call__[H: Event, R](self: EventSubscriber[H], handler: Callable[[H], R], /) -> Callable[[H], R]: ...

    # A method taking that event after `self`, likewise.
    @overload
    def __call__[S, H: Event, R](self: EventSubscriber[H], handler: Callable[[S, H], R], /) -> Callable[[S, H], R]: ...

    def __call__(self, handler: Any, /) -> Any:
        function: object = handler
        instance = None
        if isinstance(function, MethodType):
            function, instance = function.__func__, function.__self__
        if not isinstance(function, FunctionType):
            raise TypeError(f"a handler is a function or a method, not {handler!r}")
        if inspect.iscoroutinefunction(function):
            raise TypeError(f"{function.__qualname__} is a coroutine function, and handlers are called synchronously")
        self._bus._add(function, self._selects, self._options, instance=instance)
        return handler

    def where(self, predicate: Callable[[E], bool], /) -> EventSubscriber[E]:
        """This decorator, narrowed to the events `predicate` also passes. `once` and the like count only those."""
        selects = self._selects
        if (first := selects.predicate) is None:
            both = predicate
        else:

            def both(event: Any, /) -> bool:
                return first(event) and predicate(event)

        narrowed = EventFilter(selects.event_type, keys=selects.keys, predicate=both)
        return EventSubscriber(self._bus, narrowed, self._options)
