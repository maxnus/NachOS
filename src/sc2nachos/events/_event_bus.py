"""Handing what a game comes to to the handlers subscribed to it."""

from __future__ import annotations

import weakref
from collections.abc import Callable, Hashable, Iterable, Iterator, Sequence
from time import perf_counter
from types import FunctionType, MappingProxyType, MethodType
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, final, overload

from loguru import logger

from sc2nachos.events._done import Done
from sc2nachos.events._event import Event, ParameterizedEvent
from sc2nachos.events._event_filter import EventFilter
from sc2nachos.events._event_priority import EventPriority
from sc2nachos.events._event_subscriber import _EventSubscriber
from sc2nachos.events._event_subscriptions import _EventSubscriptions
from sc2nachos.events._handler import _Handler
from sc2nachos.events._handler_timing import HandlerTiming

if TYPE_CHECKING:
    from collections.abc import Mapping


# Covariant, so that a decorator for one event type is one for any type its handler takes: that is how a handler's
# parameter is checked, since a type variable's bound cannot name another.
_E_co = TypeVar("_E_co", bound=Event, covariant=True)


class _EventDecorator(Protocol[_E_co]):
    """What `on` answers: a decorator of a handler of `_E_co`, a function taking one or a method taking one after
    `self`, which `where` narrows.

    A handler taking any `Event` keeps its own type, so it can be decorated for several events. One taking a narrower
    type is typed as the callable it is, of the event it is decorated for.
    """

    def where(self, predicate: Callable[[_E_co], bool], /) -> _EventDecorator[_E_co]:
        """This decorator, handing on only the events `predicate` passes too. What a handler counts, such as `once`,
        counts only those."""
        ...

    @overload
    def __call__[F: Callable[[Event], object]](self, handler: F, /) -> F: ...

    @overload
    def __call__[F: Callable[[Any, Event], object]](self, handler: F, /) -> F: ...

    @overload
    def __call__[H: Event, R](self: _EventDecorator[H], handler: Callable[[H], R], /) -> Callable[[H], R]: ...

    @overload
    def __call__[S, H: Event, R](self: _EventDecorator[H], handler: Callable[[S, H], R], /) -> Callable[[S, H], R]: ...


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
    with the observation before included. The events of a turn go out priority first: every handler of one priority
    is handed each event of the turn it selects, in the turn's order, before any handler of the next.
    """

    __slots__ = ("_marks", "_methods_of", "_step", "_subscriptions", "_timings")

    def __init__(self, *, time_handlers: bool = False) -> None:
        """Time every handler's calls only if `time_handlers`, which costs some 200 ns a call."""
        self._subscriptions = _EventSubscriptions()
        # The handlers of methods marked in class bodies, of no instance yet, which `subscribe` binds to one.
        self._marks: weakref.WeakKeyDictionary[FunctionType, list[_Handler]] = weakref.WeakKeyDictionary()
        self._methods_of: weakref.WeakKeyDictionary[type, tuple[_Handler, ...]] = weakref.WeakKeyDictionary()
        # The step of the game being played, which an event emitted without one is given, or `None` between games.
        self._step: int | None = None
        self._timings: dict[type[Event], dict[str, HandlerTiming]] | None = {} if time_handlers else None

    def __repr__(self) -> str:
        by_type = self._subscriptions.by_type.items()
        counts = ", ".join(f"{kind.__name__}: {len(handlers)}" for kind, handlers in by_type)
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
    ) -> _EventDecorator[E]: ...

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
    ) -> Any:
        """Subscribe the decorated function to `event_type`, or mark the decorated method for `subscribe`.

        `event_type` is an event type, whose events and those of its subclasses the handler is handed, or what `only`
        or `of` selects of them, and `where` on the decorator this answers narrows them further. A type that has `of` is
        subscribed to only through it, and a type checker refuses it bare.

        A function is subscribed at once, wherever it is defined, and so is a method already bound to its instance. A
        method in a class body is marked instead, and is subscribed for an instance passed to `subscribe`.

        `priority` says where among the event's handlers it runs, and among a turn's, which goes out priority first. It
        runs on every event but the ones `every_steps`, `at_step` or `once` hold it back from: `every_steps` waits until
        that many steps have passed since it last ran, `at_step` runs it once, on the first event at or after that step,
        and `once` on the first event. These count only the events selected. An exception it raises ends the game,
        unless `catch_exceptions`, which logs it and goes on.

        The handler must take an event of `event_type`, which a type checker checks. One decorated for several event
        types takes an `Event`.
        """
        selects = event_type if isinstance(event_type, EventFilter) else EventFilter(event_type)
        selected = selects.event_type
        if not (isinstance(selected, type) and issubclass(selected, Event)):
            raise TypeError(f"a handler subscribes to an event type, not {selected!r}")
        if selects.keys is None and issubclass(selected, ParameterizedEvent):
            name = selected.__name__
            raise TypeError(f"a handler subscribes to {name} through `of`, as in `{name}.of(...)`")
        if every_steps is not None:
            if every_steps < 1:
                raise ValueError(f"every_steps is at least 1, not {every_steps}")
            if at_step is not None:
                raise ValueError("a handler runs every so many steps or at one step, not both")

        options = {
            "priority": priority,
            "every_steps": every_steps,
            "at_step": at_step,
            "once": once,
            "catch_exceptions": catch_exceptions,
        }
        return _EventSubscriber(self, selects, options)

    def subscribe(self, instance: object) -> None:
        """Subscribe every method of `instance` marked with `on`, until it is passed to `unsubscribe`.

        An override that is not marked leaves the method it overrides unsubscribed. Raises `ValueError` where
        `instance` has no marked method, or is subscribed already.
        """
        if not (marked := self._marked_methods(type(instance))):
            raise ValueError(f"{type(instance).__name__} has no method marked with `on`")
        if any(handler.instance is instance for handler in self._subscriptions):
            raise ValueError(f"{instance!r} is subscribed already")
        for handler in marked:
            self._subscriptions.add(handler.bound_to(instance))

    def unsubscribe(self, target: object) -> None:
        """Call `target` no more: a function, a method bound to its instance, or every method of an instance.

        Raises `ValueError` where nothing of it is subscribed.
        """
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
        """How long each handler has taken in the game being played, or the one played last, by event and by handler.

        A handler is named by its module and qualified name, so every instance of a class counts under one. Raises
        `RuntimeError` unless the api was made with `time_handlers=True`.
        """
        if self._timings is None:
            raise RuntimeError("handlers are timed only for an api made with time_handlers=True")
        return MappingProxyType({kind: MappingProxyType(timings) for kind, timings in self._timings.items()})

    def _add(self, handler: _Handler) -> None:
        """Subscribe `handler`, or mark it for `subscribe` if its function is a method in a class body."""
        if handler.instance is None and _in_class_body(handler.function):
            self._marks.setdefault(handler.function, []).append(handler)
        else:
            self._subscriptions.add(handler)

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

    def _start_game(self) -> None:
        """Forget what every handler has done, and how long it took."""
        self._subscriptions.start_game()
        if self._timings is not None:
            self._timings = {}

    def _set_step(self, step: int | None) -> None:
        """Give the events emitted without a step from now on `step`, the game's, or none once it has ended."""
        self._step = step

    def emit(self, event: Event) -> None:
        """Hand `event` to every handler of its type, or of a type it derives from, that selects it and is due to run,
        each done with it by the time this returns. A handler may emit an event itself.

        An event made without a step is given the step of the game being played. Raises `ValueError` for one when no
        game is.
        """
        if event.step < 0:
            if self._step is None:
                raise ValueError(f"{event!r} has no step, and no game is being played to give it one")
            object.__setattr__(event, "step", self._step)
        subscriptions = self._subscriptions
        if (handlers := subscriptions.resolved.get(type(event))) is None:
            handlers = subscriptions.resolve(type(event))
        if handlers:
            self._run(handlers, event, type(event) in subscriptions.selecting)

    def _hand_out(self, events: Sequence[Event]) -> None:
        """Hand out the events of one turn, each with its step, priority first: every handler of the highest priority
        is handed each event it selects, in the order of `events`, before any handler of the next priority is."""
        # Each group keeps whether one of it selects, which a handler subscribing during the turn must not change.
        passes: dict[EventPriority, list[tuple[Event, tuple[_Handler, ...], bool]]] = {}
        for event in events:
            for priority, handlers, selecting in self._subscriptions.grouped(type(event)):
                passes.setdefault(priority, []).append((event, handlers, selecting))
        for priority in sorted(passes, reverse=True):
            for event, handlers, selecting in passes[priority]:
                self._run(handlers, event, selecting)

    def _run(self, handlers: tuple[_Handler, ...], event: Event, selecting: bool) -> None:
        """Hand `event` to each of `handlers` that selects it and is due to run, checking what each selects if
        `selecting`, since one of them selects by key or predicate."""
        todo: Iterable[_Handler] = _selecting_handlers(handlers, event) if selecting else handlers
        timings = None if self._timings is None else self._timings.setdefault(type(event), {})
        step = event.step
        for handler in todo:
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


def _selecting_handlers(handlers: tuple[_Handler, ...], event: Event) -> Iterator[_Handler]:
    """The handlers of `handlers` that select `event`, and those done, which `_run` passes over. Each selects it as
    its turn comes, after those before it have run. Kept out of `_run`, where the closure would slow every read of
    its event."""
    key = event._key()
    return (handler for handler in handlers if handler.done or _selects(handler, event, key))


def _selects(handler: _Handler, event: Event, key: Hashable) -> bool:
    """Whether `handler` selects `event`, whose key is `key`: by its keys, then by its predicate, which is logged and
    taken as not passing if it raises and the handler catches exceptions."""
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


def _in_class_body(function: FunctionType) -> bool:
    """Whether `function` was defined in a class body, which the compiler writes into its qualified name: a class's
    name comes before its own there, where a function has nothing, or `<locals>` for one defined in another."""
    parts = function.__qualname__.rsplit(".", 2)
    return len(parts) > 1 and parts[-2] != "<locals>"


def _timed(timings: dict[str, HandlerTiming], handler: _Handler, event: Event) -> object:
    """Call `handler` with `event`, and count how long it took under its name."""
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
