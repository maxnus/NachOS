"""One handler, subscribed to one event type, or to some of its events."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

if TYPE_CHECKING:
    from types import FunctionType

    from sc2nachos.events._event_filter import EventFilter
    from sc2nachos.events._event_priority import EventPriority


@final
class _Handler:
    """A function handling the events a filter selects, called alone or on the instance it is a method of, with how
    often it runs and what it has done in the current game."""

    __slots__ = (
        "at_step",
        "catch_exceptions",
        "done",
        "every_steps",
        "function",
        "instance",
        "last_step",
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
        every_steps: int | None,
        at_step: int | None,
        once: bool,
        catch_exceptions: bool,
        instance: object | None = None,
    ) -> None:
        self.function = function
        self.selects = selects
        """The events it is handed: those of a type, or what `only`, `of` and `where` select of them."""
        self.priority = priority
        self.every_steps = every_steps
        self.at_step = at_step
        self.once = once or at_step is not None
        self.catch_exceptions = catch_exceptions
        self.instance = instance
        # Its place in subscription order across the whole bus, which orders one priority's handlers across event types.
        self.order = 0
        # What it has done this game; the next game resets it. An unsubscribed handler is `done` too, since an event
        # being handed out may still reach it.
        self.last_step: int | None = None
        self.done = False

    def __repr__(self) -> str:
        details = [self.name, *self.selects._details(), f"priority={self.priority.name}"]
        if self.every_steps is not None:
            details.append(f"every_steps={self.every_steps}")
        if self.at_step is not None:
            details.append(f"at_step={self.at_step}")
        elif self.once:
            details.append("once=True")
        if self.catch_exceptions:
            details.append("catch_exceptions=True")
        return f"_Handler({', '.join(details)})"

    @property
    def name(self) -> str:
        """The function's module and qualified name, shared by every instance of a class."""
        return f"{self.function.__module__}.{self.function.__qualname__}"

    def bound_to(self, instance: object) -> _Handler:
        """A copy of this handler, of a method marked in a class body, bound to `instance` with nothing done yet."""
        return _Handler(
            self.function,
            self.selects,
            priority=self.priority,
            every_steps=self.every_steps,
            at_step=self.at_step,
            once=self.once,
            catch_exceptions=self.catch_exceptions,
            instance=instance,
        )
