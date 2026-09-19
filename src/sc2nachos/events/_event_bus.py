"""Handing what a game comes to to the handlers subscribed to it."""

from __future__ import annotations

import inspect
import weakref
from bisect import bisect_right
from collections.abc import Callable, Hashable, Iterator
from time import perf_counter
from types import FunctionType, MappingProxyType, MethodType
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, final, overload

from loguru import logger

from sc2nachos.events._done import Done
from sc2nachos.events._event import Event, ParameterizedEvent
from sc2nachos.events._event_filter import EventFilter
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
    `Done`.

    An event is handed to the handlers of its class and of every class it derives from, in the order of their
    priorities, highest first, and those of one priority in the order they subscribed. So a handler of `Event` is
    handed every event, and has NachOS make every type of event it would otherwise not, the comparison of every unit
    with the observation before included.
    """

    __slots__ = (
        "_handlers",
        "_keyed",
        "_marks",
        "_methods_of",
        "_resolved",
        "_selecting",
        "_subscriptions",
        "_timings",
    )

    def __init__(self, *, time_handlers: bool = False) -> None:
        """Time every handler's calls only if `time_handlers`, which costs some 200 ns a call."""
        # Each event type's handlers in the order they run, replaced whole on every change, so that one subscribed
        # while an event is being handed out waits for the next.
        self._handlers: dict[type[Event], tuple[_Handler, ...]] = {}
        # The handlers of each event class asked for since they last changed, its bases' included, in the order they
        # run, and the classes among those one of whose handlers selects by key or predicate.
        self._resolved: dict[type[Event], tuple[_Handler, ...]] = {}
        self._selecting: set[type[Event]] = set()
        # How many handlers have subscribed, which numbers each to order the handlers of one priority across classes,
        # and how many subscribed now select by key.
        self._subscriptions = 0
        self._keyed = 0
        # The handlers of methods marked in class bodies, of no instance yet, which `subscribe` binds to one.
        self._marks: weakref.WeakKeyDictionary[FunctionType, list[_Handler]] = weakref.WeakKeyDictionary()
        self._methods_of: weakref.WeakKeyDictionary[type, tuple[_Handler, ...]] = weakref.WeakKeyDictionary()
        self._timings: dict[type[Event], dict[str, HandlerTimings]] | None = {} if time_handlers else None

    def __repr__(self) -> str:
        counts = ", ".join(f"{kind.__name__}: {len(handlers)}" for kind, handlers in self._handlers.items())
        return f"EventBus({counts})"

    # A type checker refuses a parameterized event subscribed to bare, since nothing is callable on `None`.
    @overload
    def on[E: ParameterizedEvent](  # pyright: ignore[reportOverlappingOverload]
        self,
        event_type: type[E],
        /,
        *,
        priority: EventPriority = ...,
        every_steps: int | None = ...,
        at_step: int | None = ...,
        once: bool = ...,
        catch_exceptions: bool = ...,
    ) -> None: ...

    @overload
    def on[E: Event](
        self,
        event_type: type[E] | EventFilter[E],
        /,
        *,
        priority: EventPriority = ...,
        every_steps: int | None = ...,
        at_step: int | None = ...,
        once: bool = ...,
        catch_exceptions: bool = ...,
    ) -> _Decorator[E]: ...

    def on(
        self,
        event_type: type[Event] | EventFilter[Any],
        /,
        *,
        priority: EventPriority = EventPriority.MEDIUM,
        every_steps: int | None = None,
        at_step: int | None = None,
        once: bool = False,
        catch_exceptions: bool = False,
    ) -> _Decorator[Any] | None:
        """Subscribe the decorated function to `event_type`, or mark the decorated method for `subscribe`.

        `event_type` is an event type, whose events and those of its subclasses the handler is handed, or what `only`,
        `of` or `where` selects of them. A type that has `of` is subscribed to only through it, and a type checker
        refuses it bare.

        A function is subscribed at once, wherever it is defined, and so is a method already bound to its instance. A
        method in a class body is marked instead, and is subscribed for an instance passed to `subscribe`.

        `priority` says where among the event's handlers it runs. It runs on every event but the ones `every_steps`,
        `at_step` or `once` hold it back from: `every_steps` waits until that many steps have passed since it last ran,
        `at_step` runs it once, on the first event at or after that step, and `once` on the first event. These count
        only the events selected. An exception it raises ends the game, unless `catch_exceptions`, which logs it and
        goes on.

        The handler must take an event of `event_type`, which a type checker checks. One decorated for several event
        types takes an `Event`.
        """
        keys: frozenset[Hashable] | None = None
        predicate: Callable[[Any], bool] | None = None
        if isinstance(event_type, EventFilter):
            event_type, keys, predicate = event_type.event_type, event_type.keys, event_type.predicate
        if not (isinstance(event_type, type) and issubclass(event_type, Event)):
            raise TypeError(f"a handler subscribes to an event type, not {event_type!r}")
        if keys is None and issubclass(event_type, ParameterizedEvent):
            name = event_type.__name__
            raise TypeError(f"a handler subscribes to {name} through `of`, as in `{name}.of(...)`")
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
                keys=keys,
                predicate=predicate,
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
        handler.order = self._subscriptions
        self._subscriptions += 1
        self._keyed += handler.keys is not None
        handlers = list(self._handlers.get(handler.event_type, ()))
        handlers.insert(bisect_right(handlers, -handler.priority, key=lambda other: -other.priority), handler)
        self._handlers[handler.event_type] = tuple(handlers)
        self._forget_resolved()

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
                    self._keyed -= handler.keys is not None
                else:
                    kept.append(handler)
            if not kept:
                del self._handlers[event_type]
            elif len(kept) < len(handlers):
                self._handlers[event_type] = tuple(kept)
        if removed:
            self._forget_resolved()
        return removed

    def _forget_resolved(self) -> None:
        """Forget which handlers each event class is handed to, since they have changed."""
        self._resolved.clear()
        self._selecting.clear()

    def _resolve(self, event_type: type[Event]) -> tuple[_Handler, ...]:
        """The handlers the events of `event_type` are handed to, those of its bases included, in the order they run,
        noting whether any of them selects by key or predicate."""
        found = [handler for cls in event_type.__mro__ for handler in self._handlers.get(cls, ())]
        found.sort(key=lambda handler: (-handler.priority, handler.order))
        if any(handler.keys is not None or handler.predicate is not None for handler in found):
            self._selecting.add(event_type)
        resolved = self._resolved[event_type] = tuple(found)
        return resolved

    def _has_handlers(self, event_type: type[Event]) -> bool:
        """Whether a handler not done takes every event of `event_type` that its predicate passes: one subscribed to
        it or to a base of it, without `only` or `of`."""
        if (handlers := self._resolved.get(event_type)) is None:
            handlers = self._resolve(event_type)
        return bool(handlers) and any(not handler.done and handler.keys is None for handler in handlers)

    def _wanted_keys(self, event_type: type[Event]) -> frozenset[Hashable]:
        """The keys of `event_type` a handler not done selects through `only` or `of`."""
        if not self._keyed:
            return _NO_KEYS
        if (handlers := self._resolved.get(event_type)) is None:
            handlers = self._resolve(event_type)
        if event_type not in self._selecting:
            return _NO_KEYS
        wanted: set[Hashable] = set()
        for handler in handlers:
            if not handler.done and (keys := handler.keys) is not None:
                wanted.update(keys)
        return frozenset(wanted)

    def _start_game(self) -> None:
        """Forget what every handler has done, and how long it took."""
        for handlers in self._handlers.values():
            for handler in handlers:
                handler.last_step = None
                handler.done = False
        if self._timings is not None:
            self._timings = {}

    def emit(self, event: Event) -> None:
        """Hand `event` to every handler of its type, or of a type it derives from, that selects it and is due to run,
        each done with it by the time this returns. A handler may emit an event itself."""
        if (handlers := self._resolved.get(type(event))) is None:
            handlers = self._resolve(type(event))
        if not handlers:
            return
        if type(event) in self._selecting:
            handlers = _selecting_handlers(handlers, event)
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


_NO_KEYS: frozenset[Hashable] = frozenset()


def _selecting_handlers(handlers: tuple[_Handler, ...], event: Event) -> Iterator[_Handler]:
    """The handlers of `handlers` that select `event`, and those done, which `emit` passes over. Each selects it as
    its turn comes, after those before it have run. Kept out of `emit`, where the closure would slow every read of
    its event."""
    key = event._key()
    return (handler for handler in handlers if handler.done or _selects(handler, event, key))


def _selects(handler: _Handler, event: Event, key: Hashable) -> bool:
    """Whether `handler` selects `event`, whose key is `key`: by its keys, then by its predicate, which is logged and
    taken as not passing if it raises and the handler catches exceptions."""
    if (keys := handler.keys) is not None and key not in keys:
        return False
    if (predicate := handler.predicate) is None:
        return True
    try:
        return bool(predicate(event))
    except Exception:
        if not handler.catch_exceptions:
            raise
        logger.exception("{} raised selecting {}", handler.name, event)
        return False


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
