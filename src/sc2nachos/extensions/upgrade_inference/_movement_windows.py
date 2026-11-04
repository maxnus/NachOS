"""How far each enemy unit of a type a speed upgrade changes moved over short windows, against how far it would have
moved without the upgrade and with it: what upgrade inference is to read speed upgrades off."""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, final

from sc2nachos.events import EventPriority, TurnStartEvent
from sc2nachos.gametime import STEPS_PER_SECOND
from sc2nachos.ids import BuffId, UnitTypeId, UpgradeId
from sc2nachos.units import Alliance

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from sc2nachos import Api
    from sc2nachos.gamedata import GameData
    from sc2nachos.geometry import Point

# Upgrade inference reads Charge off the buff a charging zealot wears, not off its speed.
_READ_OFF_BUFFS: Final[frozenset[UpgradeId]] = frozenset({UpgradeId.CHARGE})
# How fast each Zerg ground type a speed upgrade changes moves on creep, without the upgrade and with it, in distance
# per second at the Faster speed: the rows hold no creep speed (in game).
_CREEP_SPEEDS: Final[Mapping[UnitTypeId, tuple[float, float]]] = MappingProxyType(
    {
        UnitTypeId.BANELING: (4.55, 5.3703),
        UnitTypeId.HYDRALISK: (4.0906, 4.8289),
        UnitTypeId.ROACH: (4.0906, 5.4578),
        UnitTypeId.ULTRALISK: (5.3703, 5.5344),
        UnitTypeId.ZERGLING: (5.3703, 8.5477),
    }
)
_NO_BUFFS: Final[frozenset[BuffId]] = frozenset()


def speed_upgrade_by_type(data: GameData) -> dict[UnitTypeId, UpgradeId]:
    """Each type whose speed exactly one upgrade changes in the rows `data` holds, with that upgrade, but a type whose
    upgrade inference reads off a buff, and a type the rows give no speed without one, as a burrowed roach."""
    speed_upgrades: dict[UnitTypeId, UpgradeId] = {}
    for unit_type, row in data.units.items():
        if row.speed == 0:
            continue
        speeding = [upgrade for upgrade, change in row.upgrades.items() if change.speed != 0]
        if len(speeding) == 1 and speeding[0] not in _READ_OFF_BUFFS:
            speed_upgrades[unit_type] = speeding[0]
    return speed_upgrades


@final
@dataclass(frozen=True, slots=True)
class MovementWindow:
    """How far one enemy unit moved from `start_step` to `end_step`, every observation of it in between visible, of its
    type and on its first side, against how far it would have moved at full speed without `upgrade` and with it."""

    unit_id: int
    """The unit's NachOS id."""
    type_id: UnitTypeId
    """The unit's type throughout."""
    upgrade: UpgradeId
    """The upgrade that changes the type's speed."""
    start_step: int
    """The step of the window's first observation."""
    end_step: int
    """The step of its last."""
    covers_its_whole_block: bool
    """Whether it began at the first observation of its block of steps, rather than where the unit was first seen, or
    seen again, within the block: only then does it run through the whole block."""
    distance: float
    """How far the unit moved, observation to observation, added up."""
    without_upgrade: float
    """How far it would have moved at full speed without `upgrade`, at its speed for the ground it stood on at each
    observation."""
    with_upgrade: float
    """How far it would have moved at full speed with `upgrade`, likewise."""
    least_step_share: float
    """The least `upgraded_share` of a single observation's move in the window."""
    greatest_step_share: float
    """The greatest `upgraded_share` of a single observation's move in the window: both are 0 for a unit that moved
    at full speed without `upgrade` at every observation, and both 1 for one at full speed with it."""
    steps_on_creep: int
    """How many of the window's steps began on creep, for a type creep speeds up; 0 for any other."""
    buffs_worn: frozenset[BuffId]
    """Every buff the unit wore at any observation of the window."""
    is_hallucination: bool
    """Whether the unit read as a hallucination at the window's last observation."""

    @property
    def upgraded_share(self) -> float:
        """Where `distance` falls between the two predictions: 0 at `without_upgrade`, 1 at `with_upgrade`, below 0 for
        a unit that moved less than at full speed without the upgrade."""
        return (self.distance - self.without_upgrade) / (self.with_upgrade - self.without_upgrade)


@final
@dataclass(frozen=True, slots=True)
class _SpeedsOfAType:
    """How far a unit of one type moves in a step at full speed without its speed upgrade and with it, off creep and
    on it; on creep as off it for a type creep does not speed up."""

    upgrade: UpgradeId
    off_creep: tuple[float, float]
    on_creep: tuple[float, float]
    creep_speeds_it_up: bool


@final
class _OpenWindow:
    """A window still being measured, as of its last observation."""

    __slots__ = (
        "buffs_worn",
        "covers_its_whole_block",
        "distance",
        "greatest_step_share",
        "last_position",
        "last_step",
        "least_step_share",
        "start_step",
        "steps_on_creep",
        "type_id",
        "with_upgrade",
        "without_upgrade",
    )

    def __init__(self, type_id: UnitTypeId, step: int, position: Point, *, covers_its_whole_block: bool) -> None:
        self.type_id = type_id
        self.covers_its_whole_block = covers_its_whole_block
        self.start_step = step
        self.last_step = step
        self.last_position = position
        self.distance = 0.0
        self.without_upgrade = 0.0
        self.with_upgrade = 0.0
        self.least_step_share = math.inf
        self.greatest_step_share = -math.inf
        self.steps_on_creep = 0
        self.buffs_worn = _NO_BUFFS

    def add_move(
        self, position: Point, step: int, step_speeds: tuple[float, float], *, on_creep: bool, buffs: frozenset[BuffId]
    ) -> None:
        """Add the unit's move to `position`, where `step` observed it, wearing `buffs`, against `step_speeds`: how far
        it moves in a step at full speed without the upgrade and with it, on the ground the move began on, creep if
        `on_creep`."""
        steps = step - self.last_step
        last_position = self.last_position
        distance = math.hypot(position[0] - last_position[0], position[1] - last_position[1])
        without_upgrade, with_upgrade = step_speeds
        step_share = (distance / steps - without_upgrade) / (with_upgrade - without_upgrade)
        self.distance += distance
        self.without_upgrade += without_upgrade * steps
        self.with_upgrade += with_upgrade * steps
        self.least_step_share = min(self.least_step_share, step_share)
        self.greatest_step_share = max(self.greatest_step_share, step_share)
        if on_creep:
            self.steps_on_creep += steps
        if buffs:
            self.buffs_worn |= buffs
        self.last_step = step
        self.last_position = position

    def ended(self, unit_id: int, upgrade: UpgradeId, *, is_hallucination: bool) -> MovementWindow:
        """The window, ending at its last observation, of the unit `unit_id`, whose type `upgrade` speeds up."""
        return MovementWindow(
            unit_id,
            self.type_id,
            upgrade,
            self.start_step,
            self.last_step,
            self.covers_its_whole_block,
            self.distance,
            self.without_upgrade,
            self.with_upgrade,
            self.least_step_share,
            self.greatest_step_share,
            self.steps_on_creep,
            self.buffs_worn,
            is_hallucination,
        )


@final
class MovementWindows:
    """How far each enemy unit of a type one speed upgrade changes moves over windows of about `window_steps` steps,
    handed to `on_windows` together as they end, once a block of `window_steps` steps.

    A unit's window runs while each observation shows it visible, of the type it had when the window began and on the
    side it was first seen on. A unit missing from an observation, as one in a transport is (in game), or not visible
    in it, begins a new window when it is seen again. Windows end on one clock for every unit, at the first observation
    of each block of `window_steps` steps, a block beginning at each step whose quotient by `window_steps`, rounded
    down, is new: so blocks of 22.4 steps are 22 or 23 long. A window is as long as its block where every step is
    observed, but for a unit's first window, which may be shorter, as one that begins again does, and as long as the
    observations fall otherwise. The prediction of each observation's move takes the speed for the ground the unit
    stood on at the observation before.

    It reads each turn's observation on `TurnStartEvent` at `EventPriority.BEFORE`.
    """

    __slots__ = ("_api", "_on_windows", "_open", "_previous_step", "_speeds", "_window_steps")

    def __init__(
        self, api: Api[object], *, window_steps: float, on_windows: Callable[[Sequence[MovementWindow]], object]
    ) -> None:
        """Measure the enemy of `api`'s game, which must have started, from its next turn."""
        self._api = api
        self._window_steps = window_steps
        self._on_windows = on_windows
        self._speeds = self._speeds_by_type(api.data)
        self._open: dict[int, _OpenWindow] = {}
        self._previous_step = -1
        api.on(TurnStartEvent, priority=EventPriority.BEFORE)(self._measure)

    @staticmethod
    def _speeds_by_type(data: GameData) -> dict[UnitTypeId, _SpeedsOfAType]:
        """The speeds of each type `speed_upgrade_by_type` gives, a step at a time."""
        speeds: dict[UnitTypeId, _SpeedsOfAType] = {}
        for unit_type, upgrade in speed_upgrade_by_type(data).items():
            row = data.units[unit_type]
            off_creep = (row.speed / STEPS_PER_SECOND, row.with_upgrades({upgrade}).speed / STEPS_PER_SECOND)
            creep_speeds = _CREEP_SPEEDS.get(unit_type)
            on_creep = (
                off_creep
                if creep_speeds is None
                else (creep_speeds[0] / STEPS_PER_SECOND, creep_speeds[1] / STEPS_PER_SECOND)
            )
            speeds[unit_type] = _SpeedsOfAType(upgrade, off_creep, on_creep, creep_speeds is not None)
        return speeds

    def _measure(self, *_: object) -> None:
        """Carry each visible enemy unit's window on to this observation, and end those whose block of steps ended."""
        api = self._api
        step = api.step
        previous_step = self._previous_step
        block_ended = step // self._window_steps != previous_step // self._window_steps
        previously_open = self._open
        still_open: dict[int, _OpenWindow] = {}
        ended: list[MovementWindow] = []
        for unit in api.units.enemy:
            speeds = self._speeds.get(type_id := unit.type_id)
            # The units are the enemy's, so one first seen on another side has changed sides.
            if speeds is None or unit.first_alliance is not Alliance.ENEMY or not unit.is_visible:
                continue
            window = previously_open.get(unit_id := unit.id)
            if window is None or window.last_step != previous_step or window.type_id is not type_id:
                still_open[unit_id] = _OpenWindow(type_id, step, unit.position, covers_its_whole_block=block_ended)
                continue
            on_creep = speeds.creep_speeds_it_up and api.creep[window.last_position]
            step_speeds = speeds.on_creep if on_creep else speeds.off_creep
            window.add_move(unit.position, step, step_speeds, on_creep=on_creep, buffs=unit.buffs)
            if block_ended:
                ended.append(window.ended(unit_id, speeds.upgrade, is_hallucination=unit.is_hallucination))
                window = _OpenWindow(type_id, step, unit.position, covers_its_whole_block=True)
            still_open[unit_id] = window
        self._open = still_open
        self._previous_step = step
        if block_ended:
            self._on_windows(ended)
