"""One handler, subscribed to one event type, or to some of its events."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

from sc2nachos.events._event_filter import _described

if TYPE_CHECKING:
    from collections.abc import Callable, Hashable
    from types import FunctionType

    from sc2nachos.events._event import Event
    from sc2nachos.events._event_priority import EventPriority


@final
class _Handler:
    """A function handling one event type, or the events of it some keys or a predicate select, called alone or with
    the instance it is a method of: how often it asked to be called, and what it has done in the game being played."""

    __slots__ = (
        "at_step",
        "catch_exceptions",
        "done",
        "event_type",
        "every_steps",
        "function",
        "instance",
        "keys",
        "last_step",
        "name",
        "once",
        "order",
        "predicate",
        "priority",
    )

    def __init__(
        self,
        function: FunctionType,
        event_type: type[Event],
        *,
        keys: frozenset[Hashable] | None = None,
        predicate: Callable[[Any], bool] | None = None,
        priority: EventPriority,
        every_steps: int | None,
        at_step: int | None,
        once: bool,
        catch_exceptions: bool,
        instance: object | None = None,
    ) -> None:
        self.function = function
        self.event_type = event_type
        self.keys = keys
        self.predicate = predicate
        self.priority = priority
        self.every_steps = every_steps
        self.at_step = at_step
        self.once = once or at_step is not None
        self.catch_exceptions = catch_exceptions
        self.instance = instance
        self.name = f"{function.__module__}.{function.__qualname__}"
        # When it subscribed among every handler of the bus, which orders the handlers of one priority across types.
        self.order = 0
        # What it has done this game, which the next one starts afresh. `done` also for one unsubscribed, which
        # an event being handed out may still reach.
        self.last_step: int | None = None
        self.done = False

    def __repr__(self) -> str:
        details = [self.name, self.event_type.__name__, f"priority={self.priority.name}"]
        if self.keys is not None:
            details.append(f"keys={_described(self.keys)}")
        if self.predicate is not None:
            details.append(f"predicate={getattr(self.predicate, '__qualname__', self.predicate)}")
        if self.every_steps is not None:
            details.append(f"every_steps={self.every_steps}")
        if self.at_step is not None:
            details.append(f"at_step={self.at_step}")
        elif self.once:
            details.append("once=True")
        if self.catch_exceptions:
            details.append("catch_exceptions=True")
        return f"_Handler({', '.join(details)})"

    def bound_to(self, instance: object) -> _Handler:
        """This handler of a method marked in a class body, for `instance`, and with nothing done yet."""
        return _Handler(
            self.function,
            self.event_type,
            keys=self.keys,
            predicate=self.predicate,
            priority=self.priority,
            every_steps=self.every_steps,
            at_step=self.at_step,
            once=self.once,
            catch_exceptions=self.catch_exceptions,
            instance=instance,
        )
