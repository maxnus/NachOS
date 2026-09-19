"""Which events of one type a handler is handed."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from sc2nachos.events._event import Event


@final
class EventFilter[E: Event]:
    """The events of one type a handler is handed: those of some keys, those a predicate passes, or both.

    `only` and `of` on an event class make one, and `where` makes or narrows one; `on` takes it wherever it takes an
    event type. The events handed out are of the type itself.
    """

    __slots__ = ("event_type", "keys", "predicate")

    def __init__(
        self,
        event_type: type[E],
        /,
        *,
        keys: frozenset[Hashable] | None = None,
        predicate: Callable[[E], bool] | None = None,
    ) -> None:
        self.event_type = event_type
        """The type of the events it hands on."""
        self.keys = keys
        """The keys of the events it hands on, or `None` for any."""
        self.predicate = predicate
        """What an event must pass to be handed on, or `None` for nothing."""

    def __repr__(self) -> str:
        return f"EventFilter({', '.join(self._details())})"

    def _details(self) -> list[str]:
        """The type, keys and predicate, as a repr shows them: each of a few keys, or how many of more."""
        details = [self.event_type.__name__]
        if (keys := self.keys) is not None:
            details.append(f"keys={sorted(keys, key=repr)!r}" if len(keys) <= 4 else f"keys=<{len(keys)} keys>")
        if self.predicate is not None:
            details.append(f"predicate={getattr(self.predicate, '__qualname__', self.predicate)}")
        return details

    def where(self, predicate: Callable[[E], bool], /) -> EventFilter[E]:
        """These events, of those `predicate` passes too."""
        if (first := self.predicate) is None:
            both = predicate
        else:

            def both(event: E, /) -> bool:
                return first(event) and predicate(event)

        return EventFilter(self.event_type, keys=self.keys, predicate=both)
