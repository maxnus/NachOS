"""One handler, subscribed to one event."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from types import FunctionType

    from sc2nachos.events._event import Event
    from sc2nachos.events._event_priority import EventPriority


@final
class _Handler:
    """A function handling one event type, called alone or with the instance it is a method of: how often it asked to
    be called, and what it has done in the game being played."""

    __slots__ = (
        "at_step",
        "catch_exceptions",
        "done",
        "event_type",
        "every_steps",
        "function",
        "instance",
        "last_step",
        "name",
        "once",
        "priority",
    )

    def __init__(
        self,
        function: FunctionType,
        event_type: type[Event],
        *,
        priority: EventPriority,
        every_steps: int | None,
        at_step: int | None,
        once: bool,
        catch_exceptions: bool,
        instance: object | None = None,
    ) -> None:
        self.function = function
        self.event_type = event_type
        self.priority = priority
        self.every_steps = every_steps
        self.at_step = at_step
        self.once = once or at_step is not None
        self.catch_exceptions = catch_exceptions
        self.instance = instance
        self.name = f"{function.__module__}.{function.__qualname__}"
        # What it has done this game, which the next one starts afresh. `done` also for one unsubscribed, which
        # an event being handed out may still reach.
        self.last_step: int | None = None
        self.done = False

    def __repr__(self) -> str:
        details = [self.name, self.event_type.__name__, f"priority={self.priority.name}"]
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
            priority=self.priority,
            every_steps=self.every_steps,
            at_step=self.at_step,
            once=self.once,
            catch_exceptions=self.catch_exceptions,
            instance=instance,
        )
