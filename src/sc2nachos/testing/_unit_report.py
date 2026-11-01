"""A unit as the game reports it in one observation, described for a bot's tests."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import KW_ONLY, dataclass

from s2clientprotocol import common_pb2, raw_pb2

from sc2nachos.game_time import GameTime
from sc2nachos.geometry import PointLike
from sc2nachos.ids import AbilityId, BuffId, UnitTypeId
from sc2nachos.units import Alliance, CloakState, Visibility

# The players of the game `MadeUpUnits` makes up, as the game numbers them. The game reports a neutral unit as player
# 16's (corpus).
OWN_PLAYER_ID = 1
ENEMY_PLAYER_ID = 2
_PLAYER_IDS_BY_ALLIANCE = {Alliance.OWN: OWN_PLAYER_ID, Alliance.ENEMY: ENEMY_PLAYER_ID, Alliance.NEUTRAL: 16}

type ReportedTarget = PointLike | UnitReport | int | None
"""What an order is aimed at: a point on the ground, the unit a report describes, the tag of a unit, or nothing."""

type ReportedOrder = tuple[AbilityId, ReportedTarget] | tuple[AbilityId, ReportedTarget, float]
"""An order as a unit reports it: the ability, its target, and its progress from 0 to 1, by default 0."""


@dataclass(frozen=True, slots=True)
class UnitReport:
    """A unit as the game reports it in one observation, for `MadeUpUnits` to take in.

    By default it is this player's unit, finished, in sight and not cloaked, of half a tile's radius, doing nothing.
    Each field
    reads back as the property of a `Unit` or `OwnUnit` of the same name, `at` as `position`. A field not given reads
    zero, as one the game leaves out does, and a maximum not given reads as its value. `dataclasses.replace` makes the
    report of the same unit in a later observation.
    """

    tag: int
    """The game's name for the unit. A report under the same tag in a later observation is of the same unit."""
    type_id: UnitTypeId
    _: KW_ONLY
    at: PointLike = (10.0, 10.0)
    """Where the unit stands, on the ground. A point of three coordinates raises `ValueError`: give `height`."""
    height: float = 0.0
    alliance: Alliance = Alliance.OWN
    """Whose unit it is. `ALLY` raises `ValueError`: the game made up has two players."""
    visibility: Visibility = Visibility.VISIBLE
    build_progress: float = 1.0
    health: float = 0.0
    health_max: float | None = None
    """The most health the unit can have, by default `health`."""
    shield: float = 0.0
    shield_max: float | None = None
    """The most shield the unit can have, by default `shield`."""
    energy: float = 0.0
    energy_max: float | None = None
    """The most energy the unit can have, by default `energy`."""
    radius: float = 0.5
    facing: float = 0.0
    cloak_state: CloakState = CloakState.NOT_CLOAKED
    buffs: Collection[BuffId] = ()
    orders: Sequence[ReportedOrder] = ()
    """What the unit is doing, the current order first."""
    is_selected: bool = False
    is_flying: bool = False
    is_burrowed: bool = False
    is_hallucination: bool = False
    is_powered: bool = False
    is_active: bool = False
    detect_range: float = 0.0
    radar_range: float = 0.0
    attack_upgrade_level: int = 0
    armor_upgrade_level: int = 0
    shield_upgrade_level: int = 0
    mineral_contents: int = 0
    vespene_contents: int = 0
    assigned_harvesters: int = 0
    ideal_harvesters: int = 0
    cargo_used: int = 0
    cargo_max: int = 0
    weapon_cooldown: GameTime | None = None
    buff_duration_max: GameTime | None = None
    buff_duration_remaining: GameTime | None = None
    add_on: UnitReport | int | None = None
    """The unit's add-on: its report, or its tag."""
    engaged_target: UnitReport | int | None = None
    """The unit the unit is attacking: its report, or its tag."""

    def __post_init__(self) -> None:
        """Raise `ValueError` for a point of three coordinates, and for an ally's unit."""
        if len(self.at) != 2:
            raise ValueError(f"a unit stands at two coordinates, not {tuple(self.at)}; give its height as height")
        if self.alliance not in _PLAYER_IDS_BY_ALLIANCE:
            raise ValueError(f"a made-up game has no {self.alliance.name} units, only own, enemy and neutral ones")

    def _as_reported(self) -> raw_pb2.Unit:
        """The report as the game sends it."""
        return raw_pb2.Unit(
            tag=self.tag,
            unit_type=self.type_id,
            alliance=self.alliance.value,
            owner=_PLAYER_IDS_BY_ALLIANCE[self.alliance],
            display_type=self.visibility.value,
            pos=common_pb2.Point(x=self.at[0], y=self.at[1], z=self.height),
            build_progress=self.build_progress,
            health=self.health,
            health_max=self.health if self.health_max is None else self.health_max,
            shield=self.shield,
            shield_max=self.shield if self.shield_max is None else self.shield_max,
            energy=self.energy,
            energy_max=self.energy if self.energy_max is None else self.energy_max,
            radius=self.radius,
            facing=self.facing,
            cloak=self.cloak_state.value,
            buff_ids=sorted(self.buffs),
            orders=[_order_as_reported(order) for order in self.orders],
            is_selected=self.is_selected,
            is_flying=self.is_flying,
            is_burrowed=self.is_burrowed,
            is_hallucination=self.is_hallucination,
            is_powered=self.is_powered,
            is_active=self.is_active,
            detect_range=self.detect_range,
            radar_range=self.radar_range,
            attack_upgrade_level=self.attack_upgrade_level,
            armor_upgrade_level=self.armor_upgrade_level,
            shield_upgrade_level=self.shield_upgrade_level,
            mineral_contents=self.mineral_contents,
            vespene_contents=self.vespene_contents,
            assigned_harvesters=self.assigned_harvesters,
            ideal_harvesters=self.ideal_harvesters,
            cargo_space_taken=self.cargo_used,
            cargo_space_max=self.cargo_max,
            weapon_cooldown=0.0 if self.weapon_cooldown is None else self.weapon_cooldown.steps,
            buff_duration_max=0 if self.buff_duration_max is None else self.buff_duration_max.nearest_step,
            buff_duration_remain=0
            if self.buff_duration_remaining is None
            else self.buff_duration_remaining.nearest_step,
            add_on_tag=_tag_of(self.add_on),
            engaged_target_tag=_tag_of(self.engaged_target),
        )


def _order_as_reported(order: ReportedOrder) -> raw_pb2.UnitOrder:
    """The order as a unit's report carries it."""
    target = order[1]
    unit_order = raw_pb2.UnitOrder(ability_id=order[0], progress=order[2] if len(order) == 3 else 0.0)
    if isinstance(target, tuple):
        unit_order.target_world_space_pos.CopyFrom(common_pb2.Point(x=target[0], y=target[1]))
    elif target is not None:
        unit_order.target_unit_tag = _tag_of(target)
    return unit_order


def _tag_of(unit: UnitReport | int | None) -> int:
    """The tag of the unit a report describes, or the tag given, or 0, the game's tag for no unit."""
    if isinstance(unit, UnitReport):
        return unit.tag
    return 0 if unit is None else unit
