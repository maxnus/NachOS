"""The game's data tables."""

from __future__ import annotations

import math
from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING, final

from sc2nachos.game_time import GameTime
from sc2nachos.gamedata._ability_data import AbilityData, ability_costs, cancel_abilities, order_behaviors
from sc2nachos.gamedata._effect_data import EffectData
from sc2nachos.gamedata._missing_attacks import with_missing_attacks
from sc2nachos.gamedata._techtree import ABILITIES_SENT_AS_ANOTHER, TECH_TREE
from sc2nachos.gamedata._turning import FIRE_WITHOUT_TURNING_UNITS, TURN_BEFORE_MOVE_UNITS, TURN_RATE_UNITS
from sc2nachos.gamedata._unit_type_data import Attribute, UnitTypeData
from sc2nachos.gamedata._upgrade_data import UpgradeData
from sc2nachos.ids import UnitTypeId

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from s2clientprotocol import sc2api_pb2

    from sc2nachos.gamedata._techtree import TechTreeAbilities
    from sc2nachos.ids import AbilityId, EffectId, UpgradeId


@final
class GameData:
    """The game's tables of unit types, abilities, upgrades and effects, as they stand before any upgrade.

    A table holds only the rows the curated ids name, which leaves out most of what the game describes.
    """

    __slots__ = ("_abilities", "_effects", "_units", "_upgrades")

    def __init__(self, data: sc2api_pb2.ResponseData) -> None:
        """Read the tables from the game's answer to `RequestData`."""
        unit_rows = _read_table(
            data.units, lambda unit: UnitTypeData._from_proto(unit, TECH_TREE.unit_types), lambda row: row.id
        )
        self._units = _with_build_times_in_costs(
            _with_turning(with_missing_attacks(_with_build_times_borrowed(unit_rows, TECH_TREE.abilities)))
        )
        structures = frozenset(row.id for row in self._units.values() if Attribute.STRUCTURE in row.attributes)
        behaviors = order_behaviors(TECH_TREE.abilities, structures, frozenset(self._units.keys() - structures))
        self._upgrades = _read_table(
            data.upgrades, lambda upgrade: UpgradeData._from_proto(upgrade, TECH_TREE.upgrades), lambda row: row.id
        )
        # An ability's cost is derived from its product, so the other tables come first.
        costs = ability_costs(self._units, self._upgrades, TECH_TREE.abilities)
        cancels = cancel_abilities(TECH_TREE.abilities, behaviors)
        rows = _read_table(
            data.abilities,
            lambda ability: AbilityData._from_proto(ability, TECH_TREE.abilities, behaviors, costs, cancels),
            lambda row: row.id,
        )
        game_rows = {row.ability_id: row for row in data.abilities}
        custom = {
            ability: AbilityData._custom(ability, TECH_TREE.abilities, behaviors, costs, cancels)
            for ability, sent_as in ABILITIES_SENT_AS_ANOTHER.items()
            if ability.is_custom and all(sending.ability in game_rows for sending in sent_as.values())
        }
        self._abilities = MappingProxyType({**rows, **custom})
        self._effects = _read_table(data.effects, EffectData._from_proto, lambda row: row.id)

    @property
    def units(self) -> Mapping[UnitTypeId, UnitTypeData]:
        """The unit types, by id."""
        return self._units

    @property
    def abilities(self) -> Mapping[AbilityId, AbilityData]:
        """The abilities, by id, the custom ones included."""
        return self._abilities

    @property
    def upgrades(self) -> Mapping[UpgradeId, UpgradeData]:
        """The upgrades, by id."""
        return self._upgrades

    @property
    def effects(self) -> Mapping[EffectId, EffectData]:
        """The effects, by id."""
        return self._effects


def _with_build_times_borrowed(
    units: Mapping[UnitTypeId, UnitTypeData], tech_tree_abilities: TechTreeAbilities
) -> Mapping[UnitTypeId, UnitTypeData]:
    """`units`, where a type the table gives no build time takes the build time of the one other type its creation
    ability makes: a rich gas building, which builds in the same time as its plain one, takes its plain one's (tool
    `sweep_orders`)."""
    rows_with_build_times = dict(units)
    for unit_type, row in units.items():
        if row.build_time or row.creation_ability is None:
            continue
        products = tech_tree_abilities.products.get(row.creation_ability, {}).values()
        types_made = {product for product in products if isinstance(product, UnitTypeId)}
        if len(types_made) != 1 or unit_type in types_made:
            continue
        if (built_as := units.get(types_made.pop())) is not None:
            rows_with_build_times[unit_type] = replace(row, build_time=built_as.build_time)
    return MappingProxyType(rows_with_build_times)


# The game turns a unit in 1/4096 of a full turn, `TURN_RATE_UNITS`'s unit.
_RADIANS_A_TURN_UNIT = math.tau / 4096


def _with_turning(unit_rows: Mapping[UnitTypeId, UnitTypeData]) -> Mapping[UnitTypeId, UnitTypeData]:
    """`unit_rows`, each given how its type turns and whether its weapons turn to fire, as `tools/sweep_turning.py`
    measured them."""
    return MappingProxyType({unit_type: _row_with_turning(type_row) for unit_type, type_row in unit_rows.items()})


def _row_with_turning(type_row: UnitTypeData) -> UnitTypeData:
    """`type_row` given how its type turns, where it turns, and whether its weapons turn to fire."""
    if type_row.id in FIRE_WITHOUT_TURNING_UNITS:
        type_row = replace(type_row, weapons=tuple(replace(weapon, turns_to_fire=False) for weapon in type_row.weapons))
    if (rate_units := TURN_RATE_UNITS.get(type_row.id)) is None:
        return type_row
    return replace(
        type_row,
        turn_rate=rate_units * _RADIANS_A_TURN_UNIT,
        moves_while_turning=type_row.id not in TURN_BEFORE_MOVE_UNITS,
    )


def _with_build_times_in_costs(unit_rows: Mapping[UnitTypeId, UnitTypeData]) -> Mapping[UnitTypeId, UnitTypeData]:
    """`unit_rows`, each cost given the build time of each stage of its type that costs something. A switch that costs
    nothing, such as a tank's siege or a depot's lowering, adds none, so every form of a unit costs the same time."""
    return MappingProxyType(
        {
            unit_type: replace(
                type_row, cost=replace(type_row.cost, time=_build_time_of_stages_that_cost(type_row, unit_rows))
            )
            for unit_type, type_row in unit_rows.items()
        }
    )


def _build_time_of_stages_that_cost(type_row: UnitTypeData, unit_rows: Mapping[UnitTypeId, UnitTypeData]) -> GameTime:
    """The build time of each stage of `type_row`'s type, back along `morphed_from`, whose resources differ from the
    stage before it, as a morph's do and a switch's do not, and of the first stage, whatever it costs."""
    counted_time, stage_row, stages_passed = GameTime.zero, type_row, {type_row.id}
    while (source_type := stage_row.morphed_from) is not None and source_type not in stages_passed:
        if (row_before := unit_rows.get(source_type)) is None:
            break
        if stage_row.cost.resources != row_before.cost.resources:
            counted_time += stage_row.build_time
        stage_row = row_before
        stages_passed.add(source_type)
    return counted_time + stage_row.build_time


def _read_table[MessageT, IdT, RowT](
    entries: Iterable[MessageT], read: Callable[[MessageT], RowT], key: Callable[[RowT], IdT]
) -> Mapping[IdT, RowT]:
    """The entries the curated ids name, read into rows keyed by id."""
    rows: dict[IdT, RowT] = {}
    for entry in entries:
        try:
            row = read(entry)
        except ValueError:
            # An uncurated id, which most are, or one newer than `data/stableid.json`.
            continue
        rows[key(row)] = row
    return MappingProxyType(rows)
