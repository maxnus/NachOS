"""What an api tells its handlers about."""

from collections.abc import Hashable
from dataclasses import field
from typing import Any, Self

from sc2nachos.events._event_filter import EventFilter
from sc2nachos.events._event_meta import _EventMeta
from sc2nachos.geometry import Area
from sc2nachos.ids import BuffId, UnitTypeId, UpgradeId
from sc2nachos.match import Result
from sc2nachos.state import Action, Alert
from sc2nachos.units import Alliance, CloakState, OwnUnit, Unit, UnitType, VitalType

# The step of an event made without one, which `EventBus.emit` replaces with the step of the game being played.
_NO_STEP = -1


class Event(metaclass=_EventMeta):
    """Something a game has come to, as of the step a bot learns of it.

    A subclass is an event of its own, made a frozen, slotted dataclass of the fields it declares, so it takes no
    `@dataclass` of its own, which fails. A handler of a class is handed the events of its subclasses too.
    """

    step: int = field(default=_NO_STEP, kw_only=True)
    """The step of the observation it comes with. One made without it is given the step of the game being played as
    it is emitted."""

    @classmethod
    def _key_of(cls, *made_of: Any) -> Hashable:
        """What `only` or `of` selects an event of this type by, read off what it is made of: its fields but `step`, in
        order. `None` for a type that has neither."""
        return None

    def _key(self) -> Hashable:
        """What `only` or `of` selects this event by, for a type that has either."""
        return None


class ParameterizedEvent(Event):
    """An event there is none of without its parameters: a handler subscribes to one through `of`, which takes them,
    and `on` refuses the type bare."""


class GameStartEvent(Event):
    """A game has started, and everything the api answers comes from it."""


class TurnStartEvent(Event):
    """A turn is starting, before anything else of it."""


class TurnEvent(Event):
    """A bot's turn, after the events of what the new observation reports."""


class GameEndEvent(Event):
    """A game has ended. The observation it ended on gets no turn of its own."""

    result: Result
    """How it ended for this player."""


# What an observation reports has happened since the one before, handed on in this order between `TurnStartEvent` and
# `TurnEvent`. What happened out of sight is reported when it is next seen.


class _UnitEvent(Event):
    """Something that happened to one unit, which `only` selects by the unit's type."""

    unit: Unit[Any]

    @classmethod
    def only(
        cls, unit_type: type[UnitType.AnyType] | UnitTypeId, /, *unit_types: type[UnitType.AnyType] | UnitTypeId
    ) -> EventFilter[Self]:
        """The events of units of these types, for `on`: each a `UnitType`, a group of them, or a `UnitTypeId`. A
        unit's type is the one it has as the event is made."""
        return EventFilter(cls, keys=_unit_type_ids_in((unit_type, *unit_types)))

    @classmethod
    def _key_of(cls, *made_of: Any) -> Hashable:
        return made_of[0].type_id

    def _key(self) -> Hashable:
        return self._key_of(self.unit)


class OwnUnitCreatedEvent(_UnitEvent):
    """A unit of this player's is first seen: one the game started with, or one trained, hatched, placed, or starting
    to warp in."""

    unit: OwnUnit[Any]


class EnemyUnitFirstSeenEvent(_UnitEvent):
    """A unit of the enemy's is seen for the first time, in sight or in the fog. One first seen in sight also gets
    `EnemyUnitEnteredSightEvent`."""


class UnitTypeChangedEvent(_UnitEvent):
    """A unit has changed type: it burrowed, sieged, lowered, became a cocoon, or finished a morph. `only` selects it by
    the type it changed to."""

    previous_type: UnitTypeId
    """The type it was."""


class UnitAllianceChangedEvent(_UnitEvent):
    """A unit has changed sides to or from this player's: a neural parasite took it over or let it go."""

    previous_alliance: Alliance
    """The side it was on."""


class OwnConstructionStartedEvent(_UnitEvent):
    """A structure of this player's is first seen unfinished: an add-on and a creep tumor included, but not an
    auto-turret, which is first seen finished (in game)."""

    unit: OwnUnit[Any]


class OwnConstructionFinishedEvent(_UnitEvent):
    """A structure of this player's that got `OwnConstructionStartedEvent` has finished."""

    unit: OwnUnit[Any]


class OwnWarpInFinishedEvent(_UnitEvent):
    """A unit of this player's has finished warping in."""

    unit: OwnUnit[Any]


class OwnUpgradeFinishedEvent(Event):
    """This player has finished researching an upgrade. Several of one observation come in the order of their ids."""

    upgrade: UpgradeId

    @classmethod
    def only(cls, upgrade: UpgradeId, /, *upgrades: UpgradeId) -> EventFilter[Self]:
        """The events of these upgrades, for `on`."""
        return EventFilter(cls, keys=frozenset((upgrade, *upgrades)))

    @classmethod
    def _key_of(cls, *made_of: Any) -> Hashable:
        return made_of[0]

    def _key(self) -> Hashable:
        return self._key_of(self.upgrade)


class OwnUnitDamagedEvent(_UnitEvent):
    """A unit of this player's has lost health or shields since the observation before, and kept its type."""

    unit: OwnUnit[Any]
    damage: float
    """The health and shields it lost since the observation before, less what it regained in between."""


class EnemyUnitDamagedEvent(_UnitEvent):
    """A unit of the enemy's in vision now and in the observation before has lost health or shields, and kept its
    type."""

    damage: float
    """The health and shields it lost since the observation before, less what it regained in between."""


class OwnUnitEnergyLostEvent(_UnitEvent):
    """A unit of this player's has less energy than in the observation before, and kept its type: it cast a spell, or
    lost energy to a feedback or an EMP."""

    unit: OwnUnit[Any]
    energy_lost: float
    """The energy it lost since the observation before, less what it regenerated in between."""


class EnemyUnitEnergyLostEvent(_UnitEvent):
    """A unit of the enemy's in vision now and in the observation before has less energy, and kept its type: it cast a
    spell, or lost energy to a feedback or an EMP."""

    energy_lost: float
    """The energy it lost since the observation before, less what it regenerated in between."""


class _VitalEvent(ParameterizedEvent):
    """A unit's health, shields or energy has crossed a value."""

    unit: Unit[Any]
    vital: VitalType
    """What of its health, shields and energy crossed the value."""
    value: float
    """The value it crossed, as `of` was given it."""

    @classmethod
    def of(
        cls, vital: VitalType, value: float, /, *unit_types: type[UnitType.AnyType] | UnitTypeId
    ) -> EventFilter[Self]:
        """The events of units of `unit_types`, or of any type if none is given, whose `vital` crosses `value`, for
        `on`. Each of `unit_types` is a `UnitType`, a group of them, or a `UnitTypeId`.

        Raises `ValueError` unless `value` is above 0, and at most 1 for a fraction.
        """
        if not value > 0 or (vital.is_fraction and value > 1):
            most = " and at most 1" if vital.is_fraction else ""
            raise ValueError(f"a {vital.value} is crossed above 0{most}, not at {value}")
        types = _unit_type_ids_in(unit_types) if unit_types else _EVERY_TYPE
        return EventFilter(cls, keys=frozenset((vital, float(value), type_id) for type_id in types))

    @classmethod
    def _key_of(cls, *made_of: Any) -> Hashable:
        unit, vital, value = made_of
        return vital, value, unit.type_id

    def _key(self) -> Hashable:
        return self._key_of(self.unit, self.vital, self.value)


class OwnUnitVitalReachedEvent(_VitalEvent):
    """A unit of this player's in vision is seen with at least the value `of` was given of a vital, having last been
    seen in vision with less: `of(VitalType.LIFE_FRACTION, 1.0)` is a unit back to full. Never a unit first seen at it,
    and not again until it has been seen below it."""

    unit: OwnUnit[Any]


class EnemyUnitVitalReachedEvent(_VitalEvent):
    """A unit of the enemy's in vision is seen with at least the value `of` was given of a vital, having last been seen
    in vision with less. One that regenerated past it out of sight is reported when it is next seen. Never a unit first
    seen at it, and not again until it has been seen below it."""


class OwnUnitVitalDroppedEvent(_VitalEvent):
    """A unit of this player's in vision is seen with less than the value `of` was given of a vital, having last been
    seen in vision with at least it: `of(VitalType.LIFE_FRACTION, 1.0)` is a unit hurt. Never a unit first seen below
    it, and not again until it has been seen at or above it."""

    unit: OwnUnit[Any]


class EnemyUnitVitalDroppedEvent(_VitalEvent):
    """A unit of the enemy's in vision is seen with less than the value `of` was given of a vital, having last been
    seen in vision with at least it. Never a unit first seen below it, and not again until it has been seen at or above
    it."""


class OwnUnitCloakChangedEvent(_UnitEvent):
    """A unit of this player's has cloaked or uncloaked. Cloaked, it reads `CLOAKED_ALLIED` whether the enemy detects
    it or not, so no event says it was detected (in game)."""

    unit: OwnUnit[Any]
    previous_cloak: CloakState
    """The cloak it had."""


class EnemyUnitCloakChangedEvent(_UnitEvent):
    """A unit of the enemy's in sight now and in the observation before has cloaked or uncloaked, or has come to be
    detected or no longer is. Burrowing is no cloak: a burrowed unit nothing detects is not listed at all (in game)."""

    previous_cloak: CloakState
    """The cloak it had."""


class _BuffEvent(Event):
    """A unit has gained or lost a buff, which `only` selects by the buff."""

    unit: Unit[Any]
    buff: BuffId

    @classmethod
    def only(cls, buff: BuffId, /, *buffs: BuffId) -> EventFilter[Self]:
        """The events of these buffs, for `on`."""
        return EventFilter(cls, keys=frozenset((buff, *buffs)))

    @classmethod
    def _key_of(cls, *made_of: Any) -> Hashable:
        return made_of[1]

    def _key(self) -> Hashable:
        return self._key_of(self.unit, self.buff)


class OwnUnitGainedBuffEvent(_BuffEvent):
    """A unit of this player's wears a buff it did not in the observation before: a spell, a stim, a cloak, or a
    worker picking up minerals or gas, which it does every trip. A unit's several come in the order of their ids."""

    unit: OwnUnit[Any]
    buff: BuffId
    """The buff it gained."""


class EnemyUnitGainedBuffEvent(_BuffEvent):
    """A unit of the enemy's in vision now and in the observation before wears a buff it did not then. An enemy unit
    nothing detects shows no buffs, so one coming to be detected gains none (in game). A unit's several come in the
    order of their ids."""

    buff: BuffId
    """The buff it gained."""


class OwnUnitLostBuffEvent(_BuffEvent):
    """A unit of this player's no longer wears a buff it wore in the observation before: it wore off, was ended, or a
    worker delivered its minerals or gas. A unit's several come in the order of their ids."""

    unit: OwnUnit[Any]
    buff: BuffId
    """The buff it lost."""


class EnemyUnitLostBuffEvent(_BuffEvent):
    """A unit of the enemy's in vision now and in the observation before no longer wears a buff it wore then. A
    unit's several come in the order of their ids."""

    buff: BuffId
    """The buff it lost."""


class EnemyUnitEnteredSightEvent(_UnitEvent):
    """A unit of the enemy's has come into sight: seen for the first time, back in the observation, or back from the
    fog. A unit that cloaks where it stands is still in sight."""


class EnemyUnitLeftSightEvent(_UnitEvent):
    """A unit of the enemy's in sight in the observation before is not now, and is not dead."""


class _AreaEvent(ParameterizedEvent):
    """A unit has crossed the edge of an area."""

    unit: Unit[Any]
    area: Area
    """The area, as `of` was given it."""

    @classmethod
    def of(cls, area: Area, /) -> EventFilter[Self]:
        """The events of units crossing the edge of `area`, for `on`."""
        return EventFilter(cls, keys=frozenset((area,)))

    @classmethod
    def _key_of(cls, *made_of: Any) -> Hashable:
        return made_of[1]

    def _key(self) -> Hashable:
        return self._key_of(self.unit, self.area)


class OwnUnitEnteredAreaEvent(_AreaEvent):
    """A unit of this player's is seen inside the area `of` was given, having last been seen outside it, or first seen
    inside it once the area has been watched a turn."""

    unit: OwnUnit[Any]


class OwnUnitLeftAreaEvent(_AreaEvent):
    """A unit of this player's is seen outside the area `of` was given, having last been seen inside it. One that dies
    inside it, or leaves the observation there, has not left it."""

    unit: OwnUnit[Any]


class EnemyUnitEnteredAreaEvent(_AreaEvent):
    """A unit of the enemy's in sight is inside the area `of` was given, having last been seen in sight outside it, or
    first seen in sight inside it once the area has been watched a turn. One in the fog counts for neither."""


class EnemyUnitLeftAreaEvent(_AreaEvent):
    """A unit of the enemy's in sight is outside the area `of` was given, having last been seen in sight inside it. One
    that dies inside it, or goes out of sight there, has not left it."""


class UnitDiedEvent(_UnitEvent):
    """The game has reported a unit dead: killed, cancelled, an egg that hatched, a MULE that expired, or a drone as
    the structure it became finishes or is killed, or a step later (in game). A dead unit gets this or
    `UnitFoundDeadEvent`, never both."""


class UnitFoundDeadEvent(_UnitEvent):
    """A unit is dead that the game did not report: a structure remembered in the fog whose spot came into vision
    without it (in game), or a drone the game has not reported dead an update after its structure finished or was
    killed, which no game has needed."""


class OwnActionEvent(Event):
    """This player did something, as the game carried it out."""

    action: Action
    """What it did, whose `step` is when the game carried it out."""


class ChatEvent(Event):
    """Any player has sent a message to the game's chat (in game)."""

    player_id: int
    """The id of the player who sent it."""
    text: str


class AlertEvent(Event):
    """The game has alerted this player. Several of one observation come in the order the game raised them."""

    alert: Alert
    """What it alerted to, whose docstring says when the game raises it."""

    @classmethod
    def only(cls, alert: Alert, /, *alerts: Alert) -> EventFilter[Self]:
        """The events of these alerts, for `on`."""
        return EventFilter(cls, keys=frozenset((alert, *alerts)))

    @classmethod
    def _key_of(cls, *made_of: Any) -> Hashable:
        return made_of[0]

    def _key(self) -> Hashable:
        return self._key_of(self.alert)


# Every type of unit, which a vital is watched for when `of` is given none.
_EVERY_TYPE: frozenset[UnitTypeId] = UnitType.AnyType._type_ids


def _unit_type_ids_in(unit_types: tuple[type[UnitType.AnyType] | UnitTypeId, ...]) -> frozenset[Hashable]:
    """The ids of the unit types `unit_types` stand for, a group for each type in it."""
    ids: set[UnitTypeId] = set()
    for unit_type in unit_types:
        ids.update((unit_type,) if isinstance(unit_type, UnitTypeId) else unit_type._type_ids)
    return frozenset(ids)
