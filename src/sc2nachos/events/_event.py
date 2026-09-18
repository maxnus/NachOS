"""What an api tells its handlers about."""

from dataclasses import dataclass
from typing import Any, final

from sc2nachos.ids import BuffId, UnitTypeId, UpgradeId
from sc2nachos.match import Result
from sc2nachos.state import Action, Alert
from sc2nachos.units import Alliance, CloakState, OwnUnit, Unit


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
    """A bot's turn, after the events of what the new observation reports."""


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
    """A unit of the enemy's is seen for the first time, in sight or in the fog. One first seen in sight also gets
    `EnemyUnitEnteredSightEvent`."""

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
    """A structure of this player's is first seen unfinished: an add-on and a creep tumor included, but not an
    auto-turret, which is first seen finished (in game)."""

    unit: OwnUnit[Any]


@final
@dataclass(frozen=True, slots=True)
class OwnConstructionFinishedEvent(Event):
    """A structure of this player's that got `OwnConstructionStartedEvent` has finished."""

    unit: OwnUnit[Any]


@final
@dataclass(frozen=True, slots=True)
class OwnWarpInFinishedEvent(Event):
    """A unit of this player's has finished warping in."""

    unit: OwnUnit[Any]


@final
@dataclass(frozen=True, slots=True)
class OwnUpgradeFinishedEvent(Event):
    """This player has finished researching an upgrade. Several of one observation come in the order of their ids."""

    upgrade: UpgradeId


@final
@dataclass(frozen=True, slots=True)
class OwnUnitDamagedEvent(Event):
    """A unit of this player's has lost health or shields since the observation before, and kept its type."""

    unit: OwnUnit[Any]
    damage: float
    """The health and shields it lost since the observation before, less what it regained in between."""


@final
@dataclass(frozen=True, slots=True)
class EnemyUnitDamagedEvent(Event):
    """A unit of the enemy's in vision now and in the observation before has lost health or shields, and kept its
    type."""

    unit: Unit[Any]
    damage: float
    """The health and shields it lost since the observation before, less what it regained in between."""


@final
@dataclass(frozen=True, slots=True)
class OwnUnitEnergyLostEvent(Event):
    """A unit of this player's has less energy than in the observation before, and kept its type: it cast a spell, or
    lost energy to a feedback or an EMP."""

    unit: OwnUnit[Any]
    energy_lost: float
    """The energy it lost since the observation before, less what it regenerated in between."""


@final
@dataclass(frozen=True, slots=True)
class EnemyUnitEnergyLostEvent(Event):
    """A unit of the enemy's in vision now and in the observation before has less energy, and kept its type: it cast a
    spell, or lost energy to a feedback or an EMP."""

    unit: Unit[Any]
    energy_lost: float
    """The energy it lost since the observation before, less what it regenerated in between."""


@final
@dataclass(frozen=True, slots=True)
class OwnUnitCloakChangedEvent(Event):
    """A unit of this player's has cloaked or uncloaked. Cloaked, it reads `CLOAKED_ALLIED` whether the enemy detects
    it or not, so no event says it was detected (in game)."""

    unit: OwnUnit[Any]
    previous_cloak: CloakState
    """The cloak it had."""


@final
@dataclass(frozen=True, slots=True)
class EnemyUnitCloakChangedEvent(Event):
    """A unit of the enemy's in sight now and in the observation before has cloaked or uncloaked, or has come to be
    detected or no longer is. Burrowing is no cloak: a burrowed unit nothing detects is not listed at all (in game)."""

    unit: Unit[Any]
    previous_cloak: CloakState
    """The cloak it had."""


@final
@dataclass(frozen=True, slots=True)
class OwnUnitGainedBuffEvent(Event):
    """A unit of this player's wears a buff it did not in the observation before: a spell, a stim, a cloak, or a
    worker picking up minerals or gas, which it does every trip. A unit's several come in the order of their ids."""

    unit: OwnUnit[Any]
    buff: BuffId
    """The buff it gained."""


@final
@dataclass(frozen=True, slots=True)
class EnemyUnitGainedBuffEvent(Event):
    """A unit of the enemy's in vision now and in the observation before wears a buff it did not then. An enemy unit
    nothing detects shows no buffs, so one coming to be detected gains none (in game). A unit's several come in the
    order of their ids."""

    unit: Unit[Any]
    buff: BuffId
    """The buff it gained."""


@final
@dataclass(frozen=True, slots=True)
class OwnUnitLostBuffEvent(Event):
    """A unit of this player's no longer wears a buff it wore in the observation before: it wore off, was ended, or a
    worker delivered its minerals or gas. A unit's several come in the order of their ids."""

    unit: OwnUnit[Any]
    buff: BuffId
    """The buff it lost."""


@final
@dataclass(frozen=True, slots=True)
class EnemyUnitLostBuffEvent(Event):
    """A unit of the enemy's in vision now and in the observation before no longer wears a buff it wore then. A
    unit's several come in the order of their ids."""

    unit: Unit[Any]
    buff: BuffId
    """The buff it lost."""


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
    """The game has reported a unit dead: killed, cancelled, an egg that hatched, a MULE that expired, or a drone as
    the structure it became finishes or is killed, or a step later (in game). A dead unit gets this or
    `UnitFoundDeadEvent`, never both."""

    unit: Unit[Any]


@final
@dataclass(frozen=True, slots=True)
class UnitFoundDeadEvent(Event):
    """A unit is dead that the game did not report: a structure remembered in the fog whose spot came into vision
    without it (in game), or a drone the game has not reported dead an update after its structure finished or was
    killed, which no game has needed."""

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
    """Any player has sent a message to the game's chat (in game)."""

    player_id: int
    """The id of the player who sent it."""
    text: str


@final
@dataclass(frozen=True, slots=True)
class AlertEvent(Event):
    """The game has alerted this player. Several of one observation come in the order the game raised them."""

    alert: Alert
    """What it alerted to, whose docstring says when the game raises it."""
