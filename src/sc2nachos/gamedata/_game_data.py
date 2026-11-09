"""The game's data tables."""

from __future__ import annotations

import math
from dataclasses import replace
from itertools import pairwise
from types import MappingProxyType
from typing import TYPE_CHECKING, final

from sc2nachos.gamedata._ability_data import AbilityData, ability_costs, cancel_abilities, order_behaviors
from sc2nachos.gamedata._effect_data import EffectData
from sc2nachos.gamedata._missing_attacks import with_missing_attacks
from sc2nachos.gamedata._tech import Tech
from sc2nachos.gamedata._techtree import (
    ABILITIES_SENT_AS_ANOTHER,
    COCOON_PRODUCTS,
    MERGE_SOURCES,
    TECH_TREE,
    UNIT_TYPE_BUILD_TIME_OVERRIDES,
    UNIT_TYPE_COST_OVERRIDES,
    UNRECORDED_BASE_FORMS,
)
from sc2nachos.gamedata._turning import FIRE_WITHOUT_TURNING_UNITS, TURN_BEFORE_MOVE_UNITS, TURN_RATE_UNITS
from sc2nachos.gamedata._unit_type_data import Attribute, UnitTypeData
from sc2nachos.gamedata._upgrade_data import UpgradeData
from sc2nachos.gametime import GameTime
from sc2nachos.ids import UnitTypeId

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from s2clientprotocol import sc2api_pb2

    from sc2nachos.gamedata._cost import Cost
    from sc2nachos.gamedata._techtree import TechTree
    from sc2nachos.ids import AbilityId, EffectId, UpgradeId


@final
class GameData:
    """The game's tables of unit types, abilities, upgrades and effects, as they stand before any upgrade.

    A table holds only the rows the curated ids name, which leaves out most of what the game describes.
    """

    __slots__ = (
        "_abilities",
        "_effects",
        "_placing_abilities",
        "_types_granting",
        "_unit_trainers",
        "_units",
        "_upgrades",
    )

    def __init__(self, data: sc2api_pb2.ResponseData) -> None:
        """Read the tables from the game's answer to `RequestData`."""
        unit_rows = _read_table(data.units, lambda unit: UnitTypeData._from_proto(unit, TECH_TREE), lambda row: row.id)
        unit_rows = _with_build_times_borrowed(_with_build_times_corrected(unit_rows), TECH_TREE)
        unit_rows = _with_build_times_in_whole_costs(_with_turning(with_missing_attacks(unit_rows)))
        step_costs = _step_costs(unit_rows)
        self._units = _with_base_forms(_with_costs_from_steps(unit_rows, step_costs), step_costs, TECH_TREE)
        structures = frozenset(row.id for row in self._units.values() if Attribute.STRUCTURE in row.attributes)
        behaviors = order_behaviors(TECH_TREE, structures, frozenset(self._units.keys() - structures))
        self._upgrades = _read_table(
            data.upgrades, lambda upgrade: UpgradeData._from_proto(upgrade, TECH_TREE), lambda row: row.id
        )
        # An ability's cost is derived from its product, so the other tables come first.
        costs = ability_costs(step_costs, self._upgrades, TECH_TREE)
        cancels = cancel_abilities(TECH_TREE, behaviors)
        rows = _read_table(
            data.abilities,
            lambda ability: AbilityData._from_proto(ability, TECH_TREE, behaviors, costs, cancels),
            lambda row: row.id,
        )
        game_rows = {row.ability_id: row for row in data.abilities}
        custom = {
            ability: AbilityData._custom(ability, TECH_TREE, behaviors, costs, cancels)
            for ability, sent_as in ABILITIES_SENT_AS_ANOTHER.items()
            if ability.is_custom and all(sending.ability in game_rows for sending in sent_as.values())
        }
        self._abilities = MappingProxyType({**rows, **custom})
        self._types_granting = _types_granting_each(self._units)
        self._effects = _read_table(data.effects, EffectData._from_proto, lambda row: row.id)
        # Read by `OwnUnit.is_constructing` and `production_capacity`, worked out once a game.
        self._placing_abilities = frozenset(ability for ability, row in self._abilities.items() if row.needs_placement)
        self._unit_trainers = frozenset(
            producer
            for row in self._units.values()
            if row.id not in structures
            for producer in row.producers
            if producer in structures
        )

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

    def types_granting(self, tech: Tech) -> frozenset[UnitTypeId]:
        """Every unit type whose `techs_granted` hold `tech`: a spire and a greater spire for `Tech.SPIRE`, a supply
        depot and a lowered one for `Tech.SUPPLY_DEPOT`."""
        return self._types_granting[tech]


def _with_build_times_borrowed(
    units: Mapping[UnitTypeId, UnitTypeData], tech_tree: TechTree
) -> Mapping[UnitTypeId, UnitTypeData]:
    """`units`, where a type the table gives no build time takes the build time of the one other type its creation
    ability makes: a rich gas building, which builds in the same time as its plain one, takes its plain one's (tool
    `sweep_orders`)."""
    rows_with_build_times = dict(units)
    for unit_type, row in units.items():
        if row.build_time or (built_as_type := _other_type_its_creation_ability_makes(row, tech_tree)) is None:
            continue
        if (built_as := units.get(built_as_type)) is not None:
            rows_with_build_times[unit_type] = replace(
                row, build_time=built_as.build_time, _unrounded_build_time=built_as._unrounded_build_time
            )
    return MappingProxyType(rows_with_build_times)


def _other_type_its_creation_ability_makes(type_row: UnitTypeData, tech_tree: TechTree) -> UnitTypeId | None:
    """The one type the creation ability of `type_row`'s type makes, where that is another type: a refinery, for a rich
    refinery. `None` where it makes the type itself, several types or none, or where the type has no creation
    ability."""
    if type_row.creation_ability is None:
        return None
    products = tech_tree.ability(type_row.creation_ability).products.values()
    types_made = {product for product in products if isinstance(product, UnitTypeId)}
    if len(types_made) != 1 or type_row.id in types_made:
        return None
    return types_made.pop()


def _with_build_times_corrected(units: Mapping[UnitTypeId, UnitTypeData]) -> Mapping[UnitTypeId, UnitTypeData]:
    """`units`, where a type `UNIT_TYPE_BUILD_TIME_OVERRIDES` names takes the build time it gives, rounded and unrounded
    alike."""
    corrected = dict(units)
    for unit_type, build_time in UNIT_TYPE_BUILD_TIME_OVERRIDES.items():
        if (row := units.get(unit_type)) is not None:
            corrected[unit_type] = replace(row, build_time=build_time, _unrounded_build_time=build_time)
    return MappingProxyType(corrected)


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


def _with_build_times_in_whole_costs(
    unit_rows: Mapping[UnitTypeId, UnitTypeData],
) -> Mapping[UnitTypeId, UnitTypeData]:
    """`unit_rows`, each whole cost given the build time of each stage of its type that costs something. A switch that
    costs nothing, such as a tank's siege or a depot's lowering, adds none, so every form of a unit costs the same
    time."""
    return MappingProxyType(
        {
            unit_type: replace(
                type_row,
                _whole_cost=replace(type_row._whole_cost, time=_build_time_of_stages_that_cost(type_row, unit_rows)),
            )
            for unit_type, type_row in unit_rows.items()
        }
    )


def _build_time_of_stages_that_cost(type_row: UnitTypeData, unit_rows: Mapping[UnitTypeId, UnitTypeData]) -> GameTime:
    """The build time of each stage of `type_row`'s type, back along `morphed_from`, whose resources differ from the
    stage before it, as a morph's do and a switch's do not, and of the first stage, whatever it costs."""
    counted_time, stage_row, stages_passed = GameTime.ZERO, type_row, {type_row.id}
    while (source_type := stage_row.morphed_from) is not None and source_type not in stages_passed:
        if (row_before := unit_rows.get(source_type)) is None:
            break
        if stage_row._whole_cost.resources != row_before._whole_cost.resources:
            counted_time += stage_row.build_time
        stage_row = row_before
        stages_passed.add(source_type)
    return counted_time + stage_row.build_time


def _step_costs(unit_rows: Mapping[UnitTypeId, UnitTypeData]) -> Mapping[UnitTypeId, Cost]:
    """The cost of the step that makes each unit type: its whole cost less its source type's, an orbital command 150 of
    550, a siege nothing, or the whole cost of a type made from nothing, but where `UNIT_TYPE_COST_OVERRIDES` corrects
    it. Its time is the type's build time, or none for a switch, which costs nothing."""
    return MappingProxyType({unit_type: _step_cost(type_row, unit_rows) for unit_type, type_row in unit_rows.items()})


def _step_cost(type_row: UnitTypeData, unit_rows: Mapping[UnitTypeId, UnitTypeData]) -> Cost:
    """The cost of the step that makes `type_row`'s type from its source, as `_step_costs` says."""
    source_row = None if type_row.morphed_from is None else unit_rows.get(type_row.morphed_from)
    if (corrected := UNIT_TYPE_COST_OVERRIDES.get(type_row.id)) is not None:
        step_cost = corrected
    elif source_row is not None:
        step_cost = type_row._whole_cost - source_row._whole_cost
    else:
        step_cost = type_row._whole_cost
    if source_row is not None and step_cost.is_free:
        return replace(step_cost, time=GameTime.ZERO)
    return replace(step_cost, time=type_row.build_time)


def _with_costs_from_steps(
    unit_rows: Mapping[UnitTypeId, UnitTypeData], step_costs: Mapping[UnitTypeId, Cost]
) -> Mapping[UnitTypeId, UnitTypeData]:
    """`unit_rows`, each given its `cost` and its `total_cost` from `step_costs`."""
    return MappingProxyType(
        {
            unit_type: replace(
                type_row,
                cost=_cost_of_last_step_with_a_cost(unit_type, unit_rows, step_costs),
                total_cost=_total_cost_of(unit_type, unit_rows, step_costs),
            )
            for unit_type, type_row in unit_rows.items()
        }
    )


def _cost_of_last_step_with_a_cost(
    unit_type: UnitTypeId, unit_rows: Mapping[UnitTypeId, UnitTypeData], step_costs: Mapping[UnitTypeId, Cost]
) -> Cost:
    """The cost of the last step to `unit_type` that costs anything, back along `morphed_from` through every switch
    that costs nothing, from what a cocoon becomes. A type `UNIT_TYPE_COST_OVERRIDES` names costs what it says."""
    if (becoming := COCOON_PRODUCTS.get(unit_type)) is not None and becoming in unit_rows:
        unit_type = becoming
    stages_passed = {unit_type}
    while unit_type not in UNIT_TYPE_COST_OVERRIDES and step_costs[unit_type].is_free:
        source_type = unit_rows[unit_type].morphed_from
        if source_type is None or source_type not in unit_rows or source_type in stages_passed:
            break
        unit_type = source_type
        stages_passed.add(source_type)
    return step_costs[unit_type]


def _total_cost_of(
    unit_type: UnitTypeId,
    unit_rows: Mapping[UnitTypeId, UnitTypeData],
    step_costs: Mapping[UnitTypeId, Cost],
    *,
    stages_passed: frozenset[UnitTypeId] = frozenset(),
) -> Cost:
    """The cost of every step that made `unit_type`, back along `morphed_from`, or through each unit `MERGE_SOURCES`
    names, from what a cocoon becomes. `stages_passed` are the types further along, which are not counted again."""
    if (becoming := COCOON_PRODUCTS.get(unit_type)) is not None and becoming in unit_rows:
        unit_type = becoming
    stages_passed |= {unit_type}
    morphed_from = unit_rows[unit_type].morphed_from
    source_types = MERGE_SOURCES.get(unit_type) or (() if morphed_from is None else (morphed_from,))
    return sum(
        (
            _total_cost_of(source_type, unit_rows, step_costs, stages_passed=stages_passed)
            for source_type in source_types
            if source_type in unit_rows and source_type not in stages_passed
        ),
        start=step_costs[unit_type],
    )


def _with_base_forms(
    unit_rows: Mapping[UnitTypeId, UnitTypeData], step_costs: Mapping[UnitTypeId, Cost], tech_tree: TechTree
) -> Mapping[UnitTypeId, UnitTypeData]:
    """`unit_rows`, each given its `base_form`, each step's cost read from `step_costs`, which hold what
    `UNIT_TYPE_COST_OVERRIDES` corrects: an overlord transport, whose row reads an overlord's, is its own. A row whose
    type is its own base form already holds it."""
    rows_with_base_forms = dict(unit_rows)
    for unit_type, type_row in unit_rows.items():
        if (base_form := _base_form_of(unit_type, unit_rows, step_costs, tech_tree)) is not unit_type:
            rows_with_base_forms[unit_type] = replace(type_row, base_form=base_form)
    return MappingProxyType(rows_with_base_forms)


def _base_form_of(
    unit_type: UnitTypeId,
    unit_rows: Mapping[UnitTypeId, UnitTypeData],
    step_costs: Mapping[UnitTypeId, Cost],
    tech_tree: TechTree,
) -> UnitTypeId:
    """The type of which `unit_type` is a form: what `_form_named_for` gives, followed back along `morphed_from`
    through each step that costs nothing to a source the table holds a row of."""
    form = _form_named_for(unit_type, unit_rows, tech_tree)
    for stage, source in pairwise(tech_tree.morph_stages_of(form)):
        if source not in unit_rows or not step_costs[stage].is_free:
            break
        form = source
    return form


def _form_named_for(
    unit_type: UnitTypeId, unit_rows: Mapping[UnitTypeId, UnitTypeData], tech_tree: TechTree
) -> UnitTypeId:
    """The type `UNRECORDED_BASE_FORMS` names for `unit_type`, or else the one other type its creation ability makes,
    where the table holds a row of it; `unit_type` itself otherwise."""
    if (written_down := UNRECORDED_BASE_FORMS.get(unit_type)) is not None and written_down in unit_rows:
        return written_down
    built_as = _other_type_its_creation_ability_makes(unit_rows[unit_type], tech_tree)
    if built_as is not None and built_as in unit_rows:
        return built_as
    return unit_type


def _types_granting_each(unit_rows: Mapping[UnitTypeId, UnitTypeData]) -> Mapping[Tech, frozenset[UnitTypeId]]:
    """Each tech, with every unit type that grants it."""
    granting: dict[Tech, set[UnitTypeId]] = {tech: set() for tech in Tech}
    for unit_type, type_row in unit_rows.items():
        for tech in type_row.techs_granted:
            granting[tech].add(unit_type)
    return MappingProxyType({tech: frozenset(types) for tech, types in granting.items()})


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
