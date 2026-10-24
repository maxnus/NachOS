"""One handler, subscribed to one event type, or to some of its events."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

if TYPE_CHECKING:
    from types import FunctionType

    from sc2nachos.events._event_filter import EventFilter
    from sc2nachos.events._event_priority import EventPriority
    from sc2nachos.game_time import GameTime


@final
class _Handler:
    """A function handling the events a filter selects, called alone or on the instance it is a method of, with how
    often it runs and what it has done in the current game."""

    __slots__ = (
        "at_time",
        "catch_exceptions",
        "done",
        "function",
        "instance",
        "last_steps",
        "min_interval",
        "name",
        "once",
        "order",
        "priority",
        "selects",
    )

    def __init__(
        self,
        function: FunctionType,
        selects: EventFilter[Any],
        *,
        priority: EventPriority,
        min_interval: GameTime | None,
        at_time: GameTime | None,
        once: bool,
        catch_exceptions: bool,
        instance: object | None = None,
    ) -> None:
        self.function = function
        self.name = f"{function.__module__}.{function.__qualname__}"
        """The function's module and qualified name, shared by every instance of a class."""
        self.selects = selects
        """The events it is handed: those of a type, or what `only`, `of` and `where` select of them."""
        self.priority = priority
        self.min_interval = min_interval
        self.at_time = at_time
        self.once = once or at_time is not None
        self.catch_exceptions = catch_exceptions
        self.instance = instance
        # Its place in subscription order across the whole bus, which orders one priority's handlers across event types.
        self.order = 0
        # What it has done this game; the next game resets it. An unsubscribed handler is `done` too, since an event
        # being handed out may still reach it.
        self.last_steps: float | None = None
        self.done = False

    def __repr__(self) -> str:
        details = [self.name, *self.selects._details(), f"priority={self.priority.name}"]
        if self.min_interval is not None:
            details.append(f"min_interval={self.min_interval!r}")
        if self.at_time is not None:
            details.append(f"at_time={self.at_time!r}")
        elif self.once:
            details.append("once=True")
        if self.catch_exceptions:
            details.append("catch_exceptions=True")
        return f"_Handler({', '.join(details)})"
