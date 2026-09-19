"""One handler, subscribed to one event type, or to some of its events."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

if TYPE_CHECKING:
    from types import FunctionType

    from sc2nachos.events._event_filter import EventFilter
    from sc2nachos.events._event_priority import EventPriority


@final
class _Handler:
    """A function handling the events some filter selects, called alone or with the instance it is a method of: how
    often it asked to be called, and what it has done in the game being played."""

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
        # When it subscribed among every handler of the bus, which orders the handlers of one priority across types.
        self.order = 0
        # What it has done this game, which the next one starts afresh. `done` also for one unsubscribed, which
        # an event being handed out may still reach.
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
        """Its function's module and qualified name, which every instance of a class shares."""
        return f"{self.function.__module__}.{self.function.__qualname__}"

    def bound_to(self, instance: object) -> _Handler:
        """This handler of a method marked in a class body, for `instance`, and with nothing done yet."""
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
