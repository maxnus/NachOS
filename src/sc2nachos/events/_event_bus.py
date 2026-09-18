"""Handing what a game comes to to the handlers subscribed to it."""

from __future__ import annotations

import inspect
import weakref
from bisect import bisect_right
from collections.abc import Callable
from time import perf_counter
from types import FunctionType, MappingProxyType, MethodType
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, final, overload

from loguru import logger

from sc2nachos.events._done import Done
from sc2nachos.events._event import Event
from sc2nachos.events._event_priority import EventPriority
from sc2nachos.events._handler import _Handler
from sc2nachos.events._handler_timings import HandlerTimings

if TYPE_CHECKING:
    from collections.abc import Mapping


# Covariant, so that a decorator for one event type is one for any type its handler takes: that is how a handler's
# parameter is checked, since a type variable's bound cannot name another.
_E_co = TypeVar("_E_co", bound=Event, covariant=True)


class _Decorator(Protocol[_E_co]):
    """What `on` answers: a decorator of a handler of `_E_co`, a function taking one or a method taking one after
    `self`.

    A handler taking any `Event` keeps its own type, so it can be decorated for several events. One taking a narrower
    type is typed as the callable it is, of the event it is decorated for.
    """

    @overload
    def __call__[F: Callable[[Event], object]](self, handler: F, /) -> F: ...

    @overload
    def __call__[F: Callable[[Any, Event], object]](self, handler: F, /) -> F: ...

    @overload
    def __call__[H: Event, R](self: _Decorator[H], handler: Callable[[H], R], /) -> Callable[[H], R]: ...

    @overload
    def __call__[S, H: Event, R](self: _Decorator[H], handler: Callable[[S, H], R], /) -> Callable[[S, H], R]: ...


@final
class EventBus:
    """What an api tells its handlers about, and who they are. `api.event` is one.

    A function stays subscribed for the life of the api, and an instance until it is passed to `unsubscribe`. Both are
    held strongly, so a handler runs whether or not anything else keeps it. What a handler has done counts for one game
    and starts afresh with the next: when it last ran, whether it ran `once` or `at_step`, and whether it returned
    `Done`. The handlers of an event run in the order of their priorities, highest first, and those of one priority in
    the order they subscribed.
    """

    __slots__ = ("_handlers", "_marks", "_methods_of", "_timings")

    def __init__(self, *, time_handlers: bool = False) -> None:
        """Time every handler's calls only if `time_handlers`, which costs some 200 ns a call."""
        # Each event type's handlers in the order they run, replaced whole on every change, so that one subscribed
        # while an event is being handed out waits for the next.
        self._handlers: dict[type[Event], tuple[_Handler, ...]] = {}
        # The handlers of methods marked in class bodies, of no instance yet, which `subscribe` binds to one.
        self._marks: weakref.WeakKeyDictionary[FunctionType, list[_Handler]] = weakref.WeakKeyDictionary()
        self._methods_of: weakref.WeakKeyDictionary[type, tuple[_Handler, ...]] = weakref.WeakKeyDictionary()
        self._timings: dict[type[Event], dict[str, HandlerTimings]] | None = {} if time_handlers else None

    def __repr__(self) -> str:
        counts = ", ".join(f"{kind.__name__}: {len(handlers)}" for kind, handlers in self._handlers.items())
        return f"EventBus({counts})"

    def on[E: Event](
        self,
        event_type: type[E],
        /,
        *,
        priority: EventPriority = EventPriority.MEDIUM,
        every_steps: int | None = None,
        at_step: int | None = None,
        once: bool = False,
        catch_exceptions: bool = False,
    ) -> _Decorator[E]:
        """Subscribe the decorated function to `event_type`, or mark the decorated method for `subscribe`.

        A function is subscribed at once, wherever it is defined, and so is a method already bound to its instance. A
        method in a class body is marked instead, and is subscribed for an instance passed to `subscribe`.

        `priority` says where among the event's handlers it runs. It runs on every event but the ones `every_steps`,
        `at_step` or `once` hold it back from: `every_steps` waits until that many steps have passed since it last ran,
        `at_step` runs it once, on the first event at or after that step, and `once` on the first event. An exception it
        raises ends the game, unless `catch_exceptions`, which logs it and goes on.

        The handler must take an `event_type`, which a type checker checks. One decorated for several event types
        takes an `Event`.
        """
        if not (isinstance(event_type, type) and issubclass(event_type, Event)):
            raise TypeError(f"a handler subscribes to an event type, not {event_type!r}")
        if every_steps is not None:
            if every_steps < 1:
                raise ValueError(f"every_steps is at least 1, not {every_steps}")
            if at_step is not None:
                raise ValueError("a handler runs every so many steps or at one step, not both")

        def decorate[F: Callable[..., object]](handler: F, /) -> F:
            function: object = handler
            instance = None
            if isinstance(function, MethodType):
                function, instance = function.__func__, function.__self__
            if not isinstance(function, FunctionType):
                raise TypeError(f"a handler is a function or a method, not {handler!r}")
            if inspect.iscoroutinefunction(function):
                raise TypeError(
                    f"{function.__qualname__} is a coroutine function, and handlers are called synchronously"
                )
            subscription = _Handler(
                function,
                event_type,
                priority=priority,
                every_steps=every_steps,
                at_step=at_step,
                once=once,
                catch_exceptions=catch_exceptions,
                instance=instance,
            )
            if instance is None and _in_class_body(function):
                self._marks.setdefault(function, []).append(subscription)
            else:
                self._subscribe(subscription)
            return handler

        return decorate

    def subscribe(self, instance: object) -> None:
        """Subscribe every method of `instance` marked with `on`, until it is passed to `unsubscribe`.

        An override that is not marked leaves the method it overrides unsubscribed. Raises `ValueError` where
        `instance` has no marked method, or is subscribed already.
        """
        if not (marked := self._marked_methods(type(instance))):
            raise ValueError(f"{type(instance).__name__} has no method marked with `on`")
        if any(handler.instance is instance for handlers in self._handlers.values() for handler in handlers):
            raise ValueError(f"{instance!r} is subscribed already")
        for handler in marked:
            self._subscribe(handler.bound_to(instance))

    def unsubscribe(self, target: object) -> None:
        """Call `target` no more: a function, a method bound to its instance, or every method of an instance.

        Raises `ValueError` where nothing of it is subscribed.
        """
        if isinstance(target, MethodType):
            function, instance = target.__func__, target.__self__
            removed = self._remove(lambda handler: handler.function is function and handler.instance is instance)
        elif isinstance(target, FunctionType):
            removed = self._remove(lambda handler: handler.function is target and handler.instance is None)
        else:
            removed = self._remove(lambda handler: handler.instance is target)
        if not removed:
            raise ValueError(f"nothing of {target!r} is subscribed")

    @property
    def timings(self) -> Mapping[type[Event], Mapping[str, HandlerTimings]]:
        """How long each handler has taken in the game being played, or the one played last, by event and by handler.

        A handler is named by its module and qualified name, so every instance of a class counts under one. Raises
        `RuntimeError` unless the api was made with `time_handlers=True`.
        """
        if self._timings is None:
            raise RuntimeError("handlers are timed only for an api made with time_handlers=True")
        return MappingProxyType({kind: MappingProxyType(timings) for kind, timings in self._timings.items()})

    def _subscribe(self, handler: _Handler) -> None:
        handlers = list(self._handlers.get(handler.event_type, ()))
        handlers.insert(bisect_right(handlers, -handler.priority, key=lambda other: -other.priority), handler)
        self._handlers[handler.event_type] = tuple(handlers)

    def _marked_methods(self, cls: type) -> tuple[_Handler, ...]:
        """The handlers of the marked methods an instance of `cls` has: those its attributes resolve to, so an override
        that is not marked hides one that is."""
        if (marked := self._methods_of.get(cls)) is None:
            found: list[_Handler] = []
            seen: set[str] = set()
            for klass in cls.__mro__:
                for name, value in vars(klass).items():
                    if name in seen:
                        continue
                    seen.add(name)
                    if isinstance(value, FunctionType):
                        found.extend(self._marks.get(value, ()))
            marked = self._methods_of[cls] = tuple(found)
        return marked

    def _remove(self, removing: Callable[[_Handler], bool]) -> bool:
        """Drop the handlers `removing` picks, and say whether there were any."""
        removed = False
        for event_type, handlers in list(self._handlers.items()):
            kept = []
            for handler in handlers:
                if removing(handler):
                    # So that an event being handed out skips it too. It is in no list any more, so no new game
                    # starts it afresh.
                    handler.done = removed = True
                else:
                    kept.append(handler)
            if not kept:
                del self._handlers[event_type]
            elif len(kept) < len(handlers):
                self._handlers[event_type] = tuple(kept)
        return removed

    def _has_handlers(self, event_type: type[Event]) -> bool:
        """Whether anything is subscribed to `event_type`, so that an event nobody handles is never made."""
        return event_type in self._handlers

    def _start_game(self) -> None:
        """Forget what every handler has done, and how long it took."""
        for handlers in self._handlers.values():
            for handler in handlers:
                handler.last_step = None
                handler.done = False
        if self._timings is not None:
            self._timings = {}

    def _emit(self, event: Event) -> None:
        """Hand `event` to every handler subscribed to its type that is due to run."""
        if (handlers := self._handlers.get(type(event))) is None:
            return
        timings = None if self._timings is None else self._timings.setdefault(type(event), {})
        step = event.step
        for handler in handlers:
            if handler.done:
                continue
            if (every := handler.every_steps) is not None:
                if (last := handler.last_step) is not None and step - last < every:
                    continue
            elif (at := handler.at_step) is not None and step < at:
                continue
            handler.last_step = step
            if handler.once:
                handler.done = True
            try:
                if timings is not None:
                    result = _timed(timings, handler, event)
                elif (instance := handler.instance) is None:
                    result = handler.function(event)
                else:
                    result = handler.function(instance, event)
            except Exception:
                if not handler.catch_exceptions:
                    raise
                logger.exception("{} raised on {}", handler.name, event)
                continue
            if result is Done:
                handler.done = True


def _in_class_body(function: FunctionType) -> bool:
    """Whether `function` was defined in a class body, which the compiler writes into its qualified name: a class's
    name comes before its own there, where a function has nothing, or `<locals>` for one defined in another."""
    parts = function.__qualname__.rsplit(".", 2)
    return len(parts) > 1 and parts[-2] != "<locals>"


def _timed(timings: dict[str, HandlerTimings], handler: _Handler, event: Event) -> object:
    """Call `handler` with `event`, and count how long it took under its name."""
    start = perf_counter()
    result = handler.function(event) if handler.instance is None else handler.function(handler.instance, event)
    seconds = perf_counter() - start
    if (timing := timings.get(handler.name)) is None:
        timings[handler.name] = HandlerTimings(1, seconds, seconds)
    else:
        timing.calls += 1
        timing.total_seconds += seconds
        if seconds > timing.max_seconds:
            timing.max_seconds = seconds
    return result
