"""The events the api hands out."""

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
from sc2nachos.units._units import _unit_type_ids_in

# The step of an event made without one; `api.emit` hands out a copy with the current step.
_NO_STEP = -1


class Event(metaclass=_EventMeta):
    """Something that happened in the game, stamped with the step the bot learns of it.

    A subclass is a new event, made a frozen, slotted dataclass of the fields it declares; adding `@dataclass` to
    it fails. A handler of a class is handed the events of its subclasses too.
    """

    step: int = field(default=_NO_STEP, kw_only=True)
    """The step of the observation it comes with. An event made without one is emitted as a copy with the current
    step."""

    @classmethod
    def _key_of(cls, *made_of: Any) -> Hashable:
        """The key `only` or `of` selects an event of this type by, from what it is made of: its fields but `step`, in
        order. `None` for a type with neither."""
        return None

    def _key(self) -> Hashable:
        """The key `only` or `of` selects this event by, for a type that has either."""
        return None


class ParameterizedEvent(Event):
    """An event that exists only for given parameters. A handler subscribes through `of`, which takes them; `on`
    refuses the bare type."""


class GameStartEvent(Event):
    """A game has started. Everything the api answers now comes from it."""


class TurnStartEvent(Event):
    """A turn is starting. It comes before the turn's other events."""


class TurnEvent(Event):
    """The bot's turn, after the events the new observation reports."""


class GameEndEvent(Event):
    """The game has ended. The observation it ended on gets no turn."""

    result: Result
    """How it ended for this player."""


# What an observation reports since the one before, handed out in this order between `TurnStartEvent` and
# `TurnEvent`. What happened out of sight is reported when next seen.


class AnyUnitEvent(Event):
    """Something happened to one unit. `only` selects by the unit's type. NachOS never hands one out itself.

    A handler of it is handed every event with a `unit`, whoever's the unit is, except the buff, vital, area and upgrade
    events, which have bases of their own. Where an event comes in an own and an enemy kind, the side
    is the one the unit is on at the time, whose class it has: a unit a neural parasite holds is the holder's.
    """

    unit: Unit[Any]

    @classmethod
    def only(
        cls, unit_type: type[UnitType.AnyType] | UnitTypeId, /, *unit_types: type[UnitType.AnyType] | UnitTypeId
    ) -> EventFilter[Self]:
        """The events of units of these types, for `on`. Each is a `UnitType`, a group of them, or a `UnitTypeId`. A
        unit is matched by its type at the time of the event."""
        return EventFilter(cls, keys=_unit_type_ids_in((unit_type, *unit_types)))

    @classmethod
    def _key_of(cls, *made_of: Any) -> Hashable:
        return made_of[0].type_id

    def _key(self) -> Hashable:
        return self._key_of(self.unit)


class OwnUnitCreatedEvent(AnyUnitEvent):
    """A unit of this player's is first seen: one the game started with, or one trained, hatched, placed, or starting
    to warp in."""

    unit: OwnUnit[Any]


class EnemyUnitFirstSeenEvent(AnyUnitEvent):
    """A unit of the enemy's is seen for the first time, in sight or in the fog. One first seen in sight also gets
    `EnemyUnitEnteredSightEvent`."""


class AnyUnitTypeChangedEvent(AnyUnitEvent):
    """A unit has changed type: it burrowed, sieged, lowered, became a cocoon, or finished a morph. `only` selects it by
    the type it changed to. NachOS never hands one out itself: a unit of this player's comes as
    `OwnUnitTypeChangedEvent` and one of the enemy's as `EnemyUnitTypeChangedEvent`, and an ally's or the map's gets
    none."""

    previous_type: UnitTypeId
    """The type it was."""


class OwnUnitTypeChangedEvent(AnyUnitTypeChangedEvent):
    """A unit of this player's has changed type."""

    unit: OwnUnit[Any]


class EnemyUnitTypeChangedEvent(AnyUnitTypeChangedEvent):
    """A unit of the enemy's has changed type. A change out of sight comes when the unit is next seen."""


class UnitAllianceChangedEvent(AnyUnitEvent):
    """A unit has changed sides to or from this player's: a neural parasite took it over or let it go. To the vital and
    area events of the side it left, it is a unit gone from the observation, and to those of the side it joined, one
    first seen. It neither enters nor leaves sight."""

    previous_alliance: Alliance
    """The side it was on."""


class OwnConstructionStartedEvent(AnyUnitEvent):
    """A structure of this player's is first seen unfinished, add-ons and creep tumors included. Not an auto-turret,
    which is first seen finished (in game)."""

    unit: OwnUnit[Any]


class OwnConstructionFinishedEvent(AnyUnitEvent):
    """A structure of this player's that got `OwnConstructionStartedEvent` has finished."""

    unit: OwnUnit[Any]


class OwnWarpInStartedEvent(AnyUnitEvent):
    """A unit of this player's has started warping in: it is first seen unfinished. It gets `OwnUnitCreatedEvent`
    too."""

    unit: OwnUnit[Any]


class OwnWarpInFinishedEvent(AnyUnitEvent):
    """A unit of this player's has finished warping in."""

    unit: OwnUnit[Any]


class AnyUpgradeEvent(Event):
    """Something happened to one of this player's upgrades. `only` selects by the upgrade. NachOS never hands one out
    itself."""

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


class OwnUpgradeStartedEvent(AnyUpgradeEvent):
    """A research of this player's first shows in a structure's orders, queued behind another included, which the game
    charges at once. From now on `api.researches` holds it."""

    unit: OwnUnit[Any]
    """The structure researching it."""


class OwnUpgradeStoppedEvent(AnyUpgradeEvent):
    """A research of this player's shows in no structure's orders, having shown in the observation before, and the
    upgrade has not arrived: it was cancelled, or its structure died. A finished research never reads as stopped, since
    its order leaves the structure in the step its upgrade arrives (in game). One that leaves a structure and shows in
    another between two observations is neither stopped nor started."""

    unit: OwnUnit[Any]
    """The structure that was researching it."""


class OwnUpgradeFinishedEvent(AnyUpgradeEvent):
    """This player has finished researching an upgrade. Several in one observation come in id order."""


class OwnUnitDamagedEvent(AnyUnitEvent):
    """A unit of this player's has lost health or shields since the observation before, and kept its type."""

    unit: OwnUnit[Any]
    damage: float
    """The health and shields it lost since the observation before, less what it regained in between."""


class EnemyUnitDamagedEvent(AnyUnitEvent):
    """A unit of the enemy's in vision now and in the observation before has lost health or shields, and kept its
    type."""

    damage: float
    """The health and shields it lost since the observation before, less what it regained in between."""


class OwnUnitEnergyLostEvent(AnyUnitEvent):
    """A unit of this player's has less energy than in the observation before, and kept its type: it cast a spell, or
    lost energy to a feedback or an EMP."""

    unit: OwnUnit[Any]
    energy_lost: float
    """The energy it lost since the observation before, less what it regenerated in between."""


class EnemyUnitEnergyLostEvent(AnyUnitEvent):
    """A unit of the enemy's in vision now and in the observation before has less energy, and kept its type: it cast a
    spell, or lost energy to a feedback or an EMP."""

    energy_lost: float
    """The energy it lost since the observation before, less what it regenerated in between."""


class AnyVitalEvent(ParameterizedEvent):
    """A unit's health, shields or energy crossed a value. NachOS never hands one out itself.

    A handler of `AnyVitalEvent.of(...)` is handed both reaching and dropping below the value, by this player's units
    and the enemy's alike.
    """

    unit: Unit[Any]
    vital: VitalType
    """Which of health, shields and energy crossed the value."""
    value: float
    """The value crossed, as given to `of`."""

    @classmethod
    def of(
        cls, vital: VitalType, value: float, /, *unit_types: type[UnitType.AnyType] | UnitTypeId
    ) -> EventFilter[Self]:
        """The events of units whose `vital` crosses `value`, for `on`, limited to `unit_types` if any are given. Each
        of `unit_types` is a `UnitType`, a group of them, or a `UnitTypeId`.

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


class OwnUnitVitalReachedEvent(AnyVitalEvent):
    """A unit of this player's in vision has at least the value given to `of`, having last been seen in vision below
    it: `of(VitalType.LIFE_FRACTION, 1.0)` is a unit back to full. Not for a unit first seen at it, and not again until
    it has been seen below it."""

    unit: OwnUnit[Any]


class EnemyUnitVitalReachedEvent(AnyVitalEvent):
    """A unit of the enemy's in vision has at least the value given to `of`, having last been seen in vision below it.
    One that regenerated past it out of sight is reported when next seen. Not for a unit first seen at it, and not
    again until it has been seen below it."""


class OwnUnitVitalDroppedEvent(AnyVitalEvent):
    """A unit of this player's in vision has less than the value given to `of`, having last been seen in vision at or
    above it: `of(VitalType.LIFE_FRACTION, 1.0)` is a unit hurt. Not for a unit first seen below it, and not again
    until it has been seen at or above it."""

    unit: OwnUnit[Any]


class EnemyUnitVitalDroppedEvent(AnyVitalEvent):
    """A unit of the enemy's in vision has less than the value given to `of`, having last been seen in vision at or
    above it. Not for a unit first seen below it, and not again until it has been seen at or above it."""


class OwnUnitCloakChangedEvent(AnyUnitEvent):
    """A unit of this player's has cloaked or uncloaked. Cloaked, it reads `CLOAKED_ALLIED` whether or not the enemy
    detects it, so no event says it was detected (in game)."""

    unit: OwnUnit[Any]
    previous_cloak_state: CloakState
    """The cloak it had."""


class EnemyUnitCloakChangedEvent(AnyUnitEvent):
    """A unit of the enemy's in sight now and in the observation before has cloaked or uncloaked, or become detected or
    undetected. Burrowing is not a cloak: a burrowed unit nothing detects is not listed at all (in game)."""

    previous_cloak_state: CloakState
    """The cloak it had."""


class AnyBuffEvent(Event):
    """A unit has gained or lost a buff. `only` selects by the buff. NachOS never hands one out itself.

    A handler of it is handed every buff gained or lost, by this player's units and the enemy's alike.
    """

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


class OwnUnitGainedBuffEvent(AnyBuffEvent):
    """A unit of this player's has a buff it did not have in the observation before: a spell, a stim, a cloak, or a
    worker picking up minerals or gas, which it does every trip. Several on one unit come in id order."""

    unit: OwnUnit[Any]
    buff: BuffId
    """The buff it gained."""


class EnemyUnitGainedBuffEvent(AnyBuffEvent):
    """A unit of the enemy's in vision now and in the observation before has a buff it did not have then. An enemy unit
    nothing detects shows no buffs, so one becoming detected gains none (in game). Several on one unit come in id
    order."""

    buff: BuffId
    """The buff it gained."""


class OwnUnitLostBuffEvent(AnyBuffEvent):
    """A unit of this player's no longer has a buff it had in the observation before: it wore off, was ended, or a
    worker delivered its minerals or gas. Several on one unit come in id order."""

    unit: OwnUnit[Any]
    buff: BuffId
    """The buff it lost."""


class EnemyUnitLostBuffEvent(AnyBuffEvent):
    """A unit of the enemy's in vision now and in the observation before no longer has a buff it had then. Several on
    one unit come in id order."""

    buff: BuffId
    """The buff it lost."""


class EnemyUnitEnteredSightEvent(AnyUnitEvent):
    """A unit of the enemy's has come into sight: seen for the first time, back in the observation, or out of the fog.
    A unit that cloaks where it stands stays in sight."""


class EnemyUnitLeftSightEvent(AnyUnitEvent):
    """A unit of the enemy's that was in sight in the observation before is not now, and is not dead. A unit of this
    player's the enemy took over in sight gets it with no `EnemyUnitEnteredSightEvent` before."""


class AnyAreaEvent(ParameterizedEvent):
    """A unit has crossed the edge of an area. NachOS never hands one out itself.

    A handler of `AnyAreaEvent.of(area)` is handed every crossing of its edge, in and out, by this player's units and
    the enemy's alike.
    """

    unit: Unit[Any]
    area: Area
    """The area, as given to `of`."""

    @classmethod
    def of(cls, area: Area, /) -> EventFilter[Self]:
        """The events of units crossing the edge of `area`, for `on`."""
        return EventFilter(cls, keys=frozenset((area,)))

    @classmethod
    def _key_of(cls, *made_of: Any) -> Hashable:
        return made_of[1]

    def _key(self) -> Hashable:
        return self._key_of(self.unit, self.area)


class OwnUnitEnteredAreaEvent(AnyAreaEvent):
    """A unit of this player's is inside the area given to `of`, having last been seen outside it, or first seen inside
    it once the area has been watched for a turn."""

    unit: OwnUnit[Any]


class EnemyUnitEnteredAreaEvent(AnyAreaEvent):
    """A unit of the enemy's in sight is inside the area given to `of`, having last been seen in sight outside it, or
    first seen in sight inside it once the area has been watched for a turn. One in the fog counts for neither."""


class OwnUnitLeftAreaEvent(AnyAreaEvent):
    """A unit of this player's is outside the area given to `of`, having last been seen inside it. One that dies inside
    it, or leaves the observation there, has not left it."""

    unit: OwnUnit[Any]


class EnemyUnitLeftAreaEvent(AnyAreaEvent):
    """A unit of the enemy's in sight is outside the area given to `of`, having last been seen in sight inside it. One
    that dies inside it, or goes out of sight there, has not left it."""


class OwnUnitBecameIdleEvent(AnyUnitEvent):
    """A finished unit of this player's has no orders, and none NachOS sent it that the observations may not show yet,
    having had orders or such an order in the observation before, been unfinished or another side's, or not been
    listed: a unit trained with no rally, a structure just finished, a worker whose mineral field ran out, a unit whose
    order the game dropped, a unit a neural parasite just took over. One that holds no order, a larva, or a depot even
    as it lowers (in game), gets it once. A sieged tank and a burrowed lurker hold the order that made the form, so
    neither is idle in it (in game)."""

    unit: OwnUnit[Any]


class AnyUnitDiedEvent(AnyUnitEvent):
    """The game has reported a unit dead: killed, cancelled, an egg that hatched, a MULE that expired, or a drone when
    the structure it became finishes or is killed, or a step later (in game). NachOS never hands one out itself: a unit
    of this player's comes as `OwnUnitDiedEvent`, one of the enemy's as `EnemyUnitDiedEvent` and one of the map's as
    `NeutralUnitDiedEvent`, and an ally's gets none. A unit reported dead never also gets `UnitFoundDeadEvent`."""


class OwnUnitDiedEvent(AnyUnitDiedEvent):
    """The game has reported a unit of this player's dead."""

    unit: OwnUnit[Any]


class EnemyUnitDiedEvent(AnyUnitDiedEvent):
    """The game has reported a unit of the enemy's dead. Only deaths this player sees are reported (in game)."""


class NeutralUnitDiedEvent(AnyUnitDiedEvent):
    """The game has reported a unit of the map's dead, as it does a mineral field killed (in game)."""


class UnitFoundDeadEvent(AnyUnitEvent):
    """A unit is dead without the game reporting it: a structure remembered in the fog whose spot came into vision
    empty (in game), or a drone not reported dead an update after its structure finished or was killed, which no game
    has needed."""


class OwnActionEvent(Event):
    """The game carried out an action of this player's."""

    action: Action
    """The action. Its `step` is when the game carried it out."""


class AnyChatEvent(Event):
    """A player, this one included, sent a message to the game's chat (in game). NachOS never hands one out itself:
    this player's comes as `OwnChatEvent` and another player's as `OpponentChatEvent`, and one from a player the game's
    info lists as an observer gets none."""

    player_id: int
    """The id of the player who sent it."""
    text: str


class OwnChatEvent(AnyChatEvent):
    """This player sent a message: through `api.chat`, or typed into its game window."""


class OpponentChatEvent(AnyChatEvent):
    """A player other than this one, and not listed as an observer, sent a message. The game names no teams, so in a
    game of more than two an ally's message comes as this too."""


class AlertEvent(Event):
    """The game alerted this player. Several in one observation come in the order the game raised them."""

    alert: Alert
    """The alert. Its docstring says when the game raises it."""

    @classmethod
    def only(cls, alert: Alert, /, *alerts: Alert) -> EventFilter[Self]:
        """The events of these alerts, for `on`."""
        return EventFilter(cls, keys=frozenset((alert, *alerts)))

    @classmethod
    def _key_of(cls, *made_of: Any) -> Hashable:
        return made_of[0]

    def _key(self) -> Hashable:
        return self._key_of(self.alert)


# Every unit type: what a vital is watched for when `of` is given no types.
_EVERY_TYPE: frozenset[UnitTypeId] = UnitType.AnyType._type_ids
