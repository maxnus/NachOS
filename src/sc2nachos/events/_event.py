"""What an api tells its handlers about."""

from dataclasses import dataclass
from typing import Any, final

from sc2nachos.ids import UnitTypeId, UpgradeId
from sc2nachos.match import Result
from sc2nachos.state import Action, Alert
from sc2nachos.units import Alliance, OwnUnit, Unit


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
    """A bot's turn, once a new observation has been taken in and what it reports has happened has been handed on."""


@final
@dataclass(frozen=True, slots=True)
class GameEndEvent(Event):
    """A game has ended. The observation it ended on gets no turn of its own."""

    result: Result
    """How it ended for this player."""


# What an observation reports has happened since the one before, handed on in this order between `TurnStartEvent` and
# `TurnEvent`. What happened out of sight is reported when it is next seen.


@final
@dataclass(frozen=True, slots=True)
class OwnUnitCreatedEvent(Event):
    """A unit of this player's is first seen: one the game started with, or one trained, hatched, placed, or starting
    to warp in."""

    unit: OwnUnit[Any]


@final
@dataclass(frozen=True, slots=True)
class EnemyUnitFirstSeenEvent(Event):
    """A unit of the enemy's is seen for the first time, in sight or in the fog. One in sight also enters sight."""

    unit: Unit[Any]


@final
@dataclass(frozen=True, slots=True)
class UnitTypeChangedEvent(Event):
    """A unit has changed type: it burrowed, sieged, lowered, became a cocoon, or finished a morph."""

    unit: Unit[Any]
    previous_type: UnitTypeId
    """The type it was."""


@final
@dataclass(frozen=True, slots=True)
class UnitAllianceChangedEvent(Event):
    """A unit has changed sides to or from this player's: a neural parasite took it over or let it go."""

    unit: Unit[Any]
    previous_alliance: Alliance
    """The side it was on."""


@final
@dataclass(frozen=True, slots=True)
class OwnConstructionStartedEvent(Event):
    """A structure of this player's, an add-on included, is first seen unfinished."""

    unit: OwnUnit[Any]


@final
@dataclass(frozen=True, slots=True)
class OwnConstructionFinishedEvent(Event):
    """A structure of this player's first seen unfinished has finished."""

    unit: OwnUnit[Any]


@final
@dataclass(frozen=True, slots=True)
class OwnWarpInFinishedEvent(Event):
    """A unit of this player's first seen warping in has finished warping in."""

    unit: OwnUnit[Any]


@final
@dataclass(frozen=True, slots=True)
class OwnUpgradeFinishedEvent(Event):
    """This player has finished researching an upgrade. Several of one observation come in the order of their ids."""

    upgrade: UpgradeId


@final
@dataclass(frozen=True, slots=True)
class UnitDamagedEvent(Event):
    """A unit in vision now and in the observation before has lost health or shields, and kept its type."""

    unit: Unit[Any]
    damage: float
    """The health and shields it lost since the observation before, less what it regained in between."""


@final
@dataclass(frozen=True, slots=True)
class UnitEnergyLostEvent(Event):
    """A unit in vision now and in the observation before has less energy, and kept its type: it cast a spell, or
    lost energy to a feedback or an EMP."""

    unit: Unit[Any]
    energy_lost: float
    """The energy it lost since the observation before, less what it regenerated in between."""


@final
@dataclass(frozen=True, slots=True)
class EnemyUnitEnteredSightEvent(Event):
    """A unit of the enemy's has come into sight: seen for the first time, back in the observation, or back from the
    fog. A unit that cloaks where it stands is still in sight."""

    unit: Unit[Any]


@final
@dataclass(frozen=True, slots=True)
class EnemyUnitLeftSightEvent(Event):
    """A unit of the enemy's in sight in the observation before is not now, and is not dead."""

    unit: Unit[Any]


@final
@dataclass(frozen=True, slots=True)
class UnitDiedEvent(Event):
    """The game has reported a unit dead: killed, cancelled, an egg that hatched, a drone whose structure finished
    or died, or a MULE that expired (in game)."""

    unit: Unit[Any]


@final
@dataclass(frozen=True, slots=True)
class UnitFoundDeadEvent(Event):
    """A unit is dead that the game did not report: a structure remembered in the fog whose spot came into vision
    without it (in game)."""

    unit: Unit[Any]


@final
@dataclass(frozen=True, slots=True)
class OwnActionEvent(Event):
    """This player did something, as the game carried it out."""

    action: Action
    """What it did, whose `step` is when the game carried it out."""


@final
@dataclass(frozen=True, slots=True)
class ChatEvent(Event):
    """A player sent a message to the game's chat, this player included (in game)."""

    player_id: int
    """The id of the player who sent it."""
    text: str


@final
@dataclass(frozen=True, slots=True)
class AlertEvent(Event):
    """The game has alerted this player. Several of one observation come in the order the game raised them."""

    alert: Alert
    """What it alerted to, whose docstring says when the game raises it."""
