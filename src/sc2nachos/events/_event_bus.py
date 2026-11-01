"""Handing events to the handlers subscribed to them."""

from __future__ import annotations

import dataclasses
import inspect
import weakref
from collections.abc import Hashable, Iterable, Iterator, Sequence
from time import perf_counter
from types import FunctionType, MappingProxyType, MethodType
from typing import TYPE_CHECKING, Any, final

from loguru import logger

from sc2nachos._checks import check_game_time
from sc2nachos.events._done import Done
from sc2nachos.events._event import Event, ParameterizedEvent
from sc2nachos.events._event_filter import EventFilter
from sc2nachos.events._event_priority import EventPriority
from sc2nachos.events._event_subscriber import EventSubscriber
from sc2nachos.events._event_subscriptions import _EventSubscriptions
from sc2nachos.events._handler import _Handler
from sc2nachos.events._handler_timing import HandlerTiming
from sc2nachos.game_time import GameTime

if TYPE_CHECKING:
    from collections.abc import Mapping


@final
class _EventBus:
    """The handlers subscribed to events, and handing events to them. `Api` holds one, which a bot reaches through
    `api.on`, `api.subscribe`, `api.unsubscribe`, `api.emit` and `api.handler_timings`, whose docstrings hold the
    contracts."""

    __slots__ = ("_marks", "_subscriptions", "_time", "_timings")

    def __init__(self, *, time_handlers: bool = False) -> None:
        """Time every handler call if `time_handlers`."""
        self._subscriptions = _EventSubscriptions()
        # What each method marked in a class body is to be subscribed to, and with which options, until `subscribe`
        # binds it to an instance. Nothing in a mark refers to the method, so a class is collected with its methods.
        self._marks: weakref.WeakKeyDictionary[FunctionType, list[tuple[EventFilter[Any], Mapping[str, Any]]]] = (
            weakref.WeakKeyDictionary()
        )
        # The current time, given to an event emitted without one, or `None` between games.
        self._time: GameTime | None = None
        self._timings: dict[type[Event], dict[str, HandlerTiming]] | None = {} if time_handlers else None

    def __repr__(self) -> str:
        by_type = self._subscriptions.by_type.items()
        counts = ", ".join(f"{kind.__name__}: {len(handlers)}" for kind, handlers in by_type)
        return f"_EventBus({counts})"

    def on(
        self,
        event_type: type[Event] | EventFilter[Any],
        /,
        *,
        priority: EventPriority = EventPriority.MEDIUM,
        min_interval: GameTime | None = None,
        at_time: GameTime | None = None,
        once: bool = False,
        catch_exceptions: bool = False,
    ) -> EventSubscriber[Any]:
        """The decorator `api.on` returns, once its arguments are checked."""
        selects = event_type if isinstance(event_type, EventFilter) else EventFilter(event_type)
        selected = selects.event_type
        if not (isinstance(selected, type) and issubclass(selected, Event)):
            raise TypeError(f"a handler subscribes to an event type, not {selected!r}")
        if selects.keys is None and issubclass(selected, ParameterizedEvent):
            name = selected.__name__
            raise TypeError(f"a handler subscribes to {name} through `of`, as in `{name}.of(...)`")
        if at_time is not None:
            check_game_time(at_time, "at_time")
        if min_interval is not None:
            check_game_time(min_interval, "min_interval")
            if min_interval.steps <= 0:
                raise ValueError(f"min_interval is above zero, not {min_interval!r}")
            if at_time is not None:
                raise ValueError("a handler runs at a least interval or at one time, not both")
            if once:
                raise ValueError("a handler runs at a least interval or once, not both")

        options = {
            "priority": priority,
            "min_interval": min_interval,
            "at_time": at_time,
            "once": once,
            "catch_exceptions": catch_exceptions,
        }
        return EventSubscriber(self, selects, options)

    def subscribe(self, instance: object) -> None:
        """Subscribe every method of `instance` marked with `on`, as `api.subscribe` says."""
        if not (marked := self._marked_methods(type(instance))):
            raise ValueError(f"{type(instance).__name__} has no method marked with `on`")
        marks = {(function, selects) for function, selects, _ in marked}
        if any(
            handler.instance is instance and (handler.function, handler.selects) in marks
            for handler in self._subscriptions
        ):
            raise ValueError(f"{instance!r} is subscribed already")
        for function, selects, options in marked:
            self._subscriptions.add(_Handler(function, selects, instance=instance, **options))

    def unsubscribe(self, target: object) -> None:
        """Unsubscribe `target`, as `api.unsubscribe` says."""
        if isinstance(target, MethodType):
            function, instance = target.__func__, target.__self__
            removed = self._subscriptions.remove(
                lambda handler: handler.function is function and handler.instance is instance
            )
        elif isinstance(target, FunctionType):
            removed = self._subscriptions.remove(
                lambda handler: handler.function is target and handler.instance is None
            )
        else:
            removed = self._subscriptions.remove(lambda handler: handler.instance is target)
        if not removed:
            raise ValueError(f"nothing of {target!r} is subscribed")

    @property
    def timings(self) -> Mapping[type[Event], Mapping[str, HandlerTiming]]:
        """How long each handler has taken, as `api.handler_timings` says."""
        if self._timings is None:
            raise RuntimeError("handlers are timed only for an api made with time_handlers=True")
        return MappingProxyType({kind: MappingProxyType(timings) for kind, timings in self._timings.items()})

    def _add(
        self, function: FunctionType, selects: EventFilter[Any], options: Mapping[str, Any], *, instance: object | None
    ) -> None:
        """Subscribe `function`, called on `instance` if it is not `None`, to what `selects` selects, with `options`,
        the rest of what `on` was given; or mark it for `subscribe` if it is a method yet to be bound."""
        if instance is None and _is_method(function):
            self._marks.setdefault(function, []).append((selects, options))
        else:
            self._subscriptions.add(_Handler(function, selects, instance=instance, **options))

    def _marked_methods(self, cls: type) -> list[tuple[FunctionType, EventFilter[Any], Mapping[str, Any]]]:
        """Each marked method an instance of `cls` has, with what it is to be subscribed to and its options: the
        methods its attributes resolve to, so an unmarked override hides a marked method."""
        found: list[tuple[FunctionType, EventFilter[Any], Mapping[str, Any]]] = []
        seen: set[str] = set()
        for klass in cls.__mro__:
            for name, value in vars(klass).items():
                if name in seen:
                    continue
                seen.add(name)
                if isinstance(value, FunctionType):
                    found.extend((value, selects, options) for selects, options in self._marks.get(value, ()))
                elif isinstance(value, classmethod | staticmethod) and value.__func__ in self._marks:
                    kind = type(value).__name__
                    raise TypeError(f"{name} of {cls.__name__} is marked with `on` under a {kind}")
        return found

    def _set_time(self, time: GameTime | None) -> None:
        """Set the time given to events emitted without one: the game's, or `None` once it has ended."""
        self._time = time

    def emit(self, event: Event) -> None:
        """Hand `event` to its handlers, as `api.emit` says."""
        if event.time.steps < 0:
            if self._time is None:
                raise ValueError(f"{event!r} has no time, and no game is being played to give it one")
            event = dataclasses.replace(event, time=self._time)
        subscriptions = self._subscriptions
        if (handlers := subscriptions.resolved.get(type(event))) is None:
            handlers = subscriptions.resolve(type(event))
        if handlers:
            self._run(handlers, event, type(event) in subscriptions.selecting)

    def _hand_out(self, events: Sequence[Event]) -> None:
        """Hand out one turn's events, priority first: every handler of the highest priority is handed each event it
        selects, in the order of `events`, before any handler of the next priority."""
        # Each group carries whether one of its handlers selects; a handler subscribing mid-turn must not change it.
        passes: dict[EventPriority, list[tuple[Event, tuple[_Handler, ...], bool]]] = {}
        for event in events:
            for priority, handlers, selecting in self._subscriptions.grouped(type(event)):
                passes.setdefault(priority, []).append((event, handlers, selecting))
        for priority in sorted(passes, reverse=True):
            for event, handlers, selecting in passes[priority]:
                self._run(handlers, event, selecting)

    def _run(self, handlers: tuple[_Handler, ...], event: Event, selecting: bool) -> None:
        """Hand `event` to each of `handlers` that selects it and is due to run. `selecting` says one of them selects by
        key or predicate, so each is checked."""
        todo: Iterable[_Handler] = _selecting_handlers(handlers, event) if selecting else handlers
        timings = None if self._timings is None else self._timings.setdefault(type(event), {})
        event_steps = event.time.steps
        for handler in todo:
            if handler.done:
                continue
            if (min_interval := handler.min_interval) is not None:
                last_steps = handler.last_steps
                if last_steps is not None and event_steps - last_steps < min_interval.steps:
                    continue
            elif (at_time := handler.at_time) is not None and event_steps < at_time.steps:
                continue
            handler.last_steps = event_steps
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


def _selecting_handlers(handlers: tuple[_Handler, ...], event: Event) -> Iterator[_Handler]:
    """The handlers in `handlers` that select `event`, plus those done, which `_run` skips. Each is checked as its turn
    comes, after the ones before it have run. Kept out of `_run`, where a closure would slow every read of the
    event."""
    key = event._key()
    return (handler for handler in handlers if handler.done or _selects(handler, event, key))


def _selects(handler: _Handler, event: Event, key: Hashable) -> bool:
    """Whether `handler` selects `event`, whose key is `key`: by its keys first, then its predicate. A predicate that
    raises is logged and counted as failing if the handler catches exceptions."""
    selects = handler.selects
    if (keys := selects.keys) is not None and key not in keys:
        return False
    if (predicate := selects.predicate) is None:
        return True
    try:
        return bool(predicate(event))
    except Exception:
        if not handler.catch_exceptions:
            raise
        logger.exception("{} raised selecting {}", handler.name, event)
        return False


def _is_method(function: FunctionType) -> bool:
    """Whether `function` is a method, to be bound to an instance: defined in a class body, which its qualified name
    says by a class's name before its own, where a module-level function has nothing and a nested one has `<locals>`;
    and taking at least two positional arguments without defaults, the instance and the event. A function a decorator
    wrapped with `functools.wraps` is judged by the function it wraps."""
    parts = function.__qualname__.rsplit(".", 2)
    if len(parts) < 2 or parts[-2] == "<locals>":
        return False
    parameters = inspect.signature(function).parameters.values()
    return sum(parameter.kind in _POSITIONAL and parameter.default is parameter.empty for parameter in parameters) >= 2


_POSITIONAL = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)


def _timed(timings: dict[str, HandlerTiming], handler: _Handler, event: Event) -> object:
    """Call `handler` with `event`, and add how long it took to the timing under its name."""
    start = perf_counter()
    result = handler.function(event) if handler.instance is None else handler.function(handler.instance, event)
    seconds = perf_counter() - start
    if (timing := timings.get(handler.name)) is None:
        timings[handler.name] = HandlerTiming(1, seconds, seconds)
    else:
        timing.calls += 1
        timing.total_seconds += seconds
        if seconds > timing.max_seconds:
            timing.max_seconds = seconds
    return result
