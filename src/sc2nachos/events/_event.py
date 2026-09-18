"""What an api tells its handlers about."""

from dataclasses import dataclass
from typing import final

from sc2nachos.match import Result


@dataclass(frozen=True, slots=True)
class Event:
    """Something a game has come to, as of the step a bot learns of it."""

    step: int
    """The step of the observation it comes with."""


@final
@dataclass(frozen=True, slots=True)
class GameStartEvent(Event):
    """A game has started, and everything the api answers comes from it."""


@final
@dataclass(frozen=True, slots=True)
class TurnStartEvent(Event):
    """A turn is starting, before anything else of it."""


@final
@dataclass(frozen=True, slots=True)
class TurnEvent(Event):
    """A bot's turn, once a new observation has been taken in."""


@final
@dataclass(frozen=True, slots=True)
class GameEndEvent(Event):
    """A game has ended. The observation it ended on gets no turn of its own."""

    result: Result
    """How it ended for this player."""
