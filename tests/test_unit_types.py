"""`UnitType`: a class for each unit type and each group of types, which narrows a unit to one."""

# Each `pyright: ignore` below marks an error the type checker must keep reporting.
# pyright: reportUnnecessaryTypeIgnoreComment=true

from pathlib import Path
from typing import Any, assert_type

import pytest
from s2clientprotocol import data_pb2

from sc2nachos.gamedata import Attribute, GameData
from sc2nachos.gamedata._techtree import TECH_TREE
from sc2nachos.ids import AbilityId, UncuratedIdError, UnitTypeId
from sc2nachos.ids.raw import RawUnitTypeId
from sc2nachos.match import Race
from sc2nachos.units import OwnUnit, RallyTarget, Unit, Units, UnitType
from sc2nachos.units import _unit_type as unit_type_module
from sc2nachos.units._tracking import _Tracker
from support import load_tool, make_enemy, make_observation, make_tables, make_unit


def _unit_types_generator() -> Any:
    """The `tools/generate_unit_types.py` module."""
    return load_tool("generate_unit_types")


@pytest.fixture(scope="module")
def form_of() -> dict[UnitTypeId, UnitTypeId]:
    """The type the corpus's tables call each type a form of, as the sweeps read it."""
    return load_tool("_sandbox").unit_aliases(_unit_types_generator().corpus_answer())


def test_the_sweeps_read_the_type_a_form_is_of_where_both_are_curated(form_of: dict[UnitTypeId, UnitTypeId]) -> None:
    assert form_of[UnitTypeId.SIEGE_TANK_SIEGED] is UnitTypeId.SIEGE_TANK
    assert form_of[UnitTypeId.BARRACKS_FLYING] is UnitTypeId.BARRACKS
    assert UnitTypeId.SIEGE_TANK not in form_of
    assert UnitTypeId.MARINE not in form_of


def _classes_of_one_type() -> list[Any]:
    """The classes in `UnitType` that name one type, not a group."""
    return [member for member in vars(UnitType).values() if isinstance(member, type) and "id" in vars(member)]


# The groups the tables give a type: a race, the structures, or a race's structures.
_TABLE_GROUPS = [
    UnitType.Structure,
    UnitType.Terran,
    UnitType.Zerg,
    UnitType.Protoss,
    UnitType.TerranStructure,
    UnitType.ZergStructure,
    UnitType.ProtossStructure,
]


def _curated_groups() -> list[Any]:
    """The classes in `UnitType` that name a group, less `AnyType` and the groups the tables give."""
    return [
        member
        for member in vars(UnitType).values()
        if isinstance(member, type)
        and "id" not in vars(member)
        and member is not UnitType.AnyType
        and member not in _TABLE_GROUPS
    ]


def _types_named_with(prefix: str) -> set[UnitTypeId]:
    return {unit_type for unit_type in UnitTypeId if unit_type.name.startswith(prefix)}


class TestUnitType:
    @pytest.mark.parametrize("group", sorted(_unit_types_generator().TWO_FORMS), ids=str)
    def test_a_two_form_group_is_a_type_and_the_one_it_changes_to(
        self, group: str, first_corpus_tables: GameData, form_of: dict[UnitTypeId, UnitTypeId]
    ) -> None:
        """One of the two morphs from the other in the tech tree, or the tables give it the other as a tech alias or
        call it a form of the other. The factory trains a hellion and a hellbat both, so neither links them; each
        performs the morph to the other."""
        first, second = sorted(getattr(UnitType, group).type_ids, key=lambda type_id: type_id.name)
        if group == "AnyHellion":
            assert UnitTypeId.HELLION in first_corpus_tables.abilities[AbilityId.HELLION_MORPH_HELLBAT].performers
            assert UnitTypeId.HELLBAT in first_corpus_tables.abilities[AbilityId.HELLBAT_MORPH_HELLION].performers
            return
        linked = [
            TECH_TREE.unit_types.morph_sources.get(one) is other
            or other in first_corpus_tables.units[one].tech_aliases
            or form_of.get(one) is other
            for one, other in ((first, second), (second, first))
        ]
        assert any(linked), (first, second)

    def test_the_module_is_as_generated_from_the_curated_ids_and_the_tables(
        self, first_corpus_tables: GameData
    ) -> None:
        generated = _unit_types_generator().render(first_corpus_tables)
        written = Path(unit_type_module.__file__).read_text(encoding="utf-8")
        assert written == generated, "run tools/generate_unit_types.py"

    def test_every_curated_type_has_one_class_named_after_it(self) -> None:
        assert sorted(unit_type.id for unit_type in _classes_of_one_type()) == sorted(UnitTypeId)
        assert all(unit_type.type_ids == {unit_type.id} for unit_type in _classes_of_one_type())
        assert UnitType.Marine.id is UnitTypeId.MARINE
        assert UnitType.SiegeTankSieged.id is UnitTypeId.SIEGE_TANK_SIEGED

    @pytest.mark.parametrize(
        ("race", "group", "structures"),
        [
            (Race.TERRAN, UnitType.Terran, UnitType.TerranStructure),
            (Race.ZERG, UnitType.Zerg, UnitType.ZergStructure),
            (Race.PROTOSS, UnitType.Protoss, UnitType.ProtossStructure),
        ],
    )
    def test_a_group_is_every_type_the_tables_put_in_it(
        self,
        first_corpus_tables: GameData,
        race: Race,
        group: type[UnitType.AnyType],
        structures: type[UnitType.AnyType],
    ) -> None:
        rows = [first_corpus_tables.units[unit_type] for unit_type in UnitTypeId]
        assert group.type_ids == {row.id for row in rows if row.race is race}
        assert structures.type_ids == group.type_ids & UnitType.Structure.type_ids
        assert UnitType.Structure.type_ids == {row.id for row in rows if Attribute.STRUCTURE in row.attributes}

    @pytest.mark.parametrize("group", _curated_groups(), ids=lambda group: group.__name__)
    def test_a_group_is_in_another_when_all_its_types_are(self, group: Any) -> None:
        for other in [*_TABLE_GROUPS, *_curated_groups()]:
            if other is not group:
                assert issubclass(group, other) == (group.type_ids <= other.type_ids), other

    def test_the_curated_groups_hold_every_type_named_for_them(self) -> None:
        towers = (
            _types_named_with("COLLAPSIBLE_TOWER_")
            - _types_named_with("COLLAPSIBLE_TOWER_DEBRIS")
            - _types_named_with("COLLAPSIBLE_TOWER_PUSH")
        )
        assert UnitType.CollapsibleTower.type_ids == towers
        assert UnitType.CollapsibleTowerDebris.type_ids == _types_named_with("COLLAPSIBLE_TOWER_DEBRIS")
        assert len(UnitType.CollapsibleTowerDebris.type_ids) == 7
        assert UnitType.Critter.type_ids == _types_named_with("CRITTER_")
        assert UnitType.AnyChangeling.type_ids == _types_named_with("CHANGELING")
        assert len(UnitType.AnyChangeling.type_ids) == 6
        assert UnitType.AnyCreepTumor.type_ids == _types_named_with("CREEP_TUMOR")
        assert len(UnitType.AnyCreepTumor.type_ids) == 3
        counts = [
            len(group.type_ids) for group in (UnitType.Destructible, UnitType.InhibitorZone, UnitType.AccelerationZone)
        ]
        assert counts == [68, 6, 6]
        towers_and_debris = UnitType.CollapsibleTower.type_ids | UnitType.CollapsibleTowerDebris.type_ids
        assert UnitType.Destructible.type_ids == _types_named_with("DESTRUCTIBLE_") | towers_and_debris

    def test_a_spire_and_an_overlord_are_a_plain_one_or_what_the_tables_count_as_one(self) -> None:
        assert UnitType.AnySpire.type_ids == {UnitTypeId.SPIRE, UnitTypeId.GREATER_SPIRE}
        assert UnitType.AnyOverlord.type_ids == {
            UnitTypeId.OVERLORD,
            UnitTypeId.OVERLORD_TRANSPORT,
            UnitTypeId.OVERSEER,
            UnitTypeId.OVERSEER_SIEGED,
        }

    def test_a_type_shares_a_group_with_each_type_the_tables_link_it_to(
        self, first_corpus_tables: GameData, form_of: dict[UnitTypeId, UnitTypeId]
    ) -> None:
        """Its tech aliases, and the type the tables call it a form of. They call an adept's shade a form of the
        adept, but it is a unit of its own beside the adept."""
        for unit_type in set(UnitTypeId) - {UnitTypeId.ADEPT_SHADE}:
            row = first_corpus_tables.units[unit_type]
            for linked in {*row.tech_aliases, *filter(None, [form_of.get(unit_type)])}:
                shared = [group for group in _curated_groups() if {unit_type, linked} <= group.type_ids]
                assert shared, (unit_type, linked)

    def test_any_type_is_every_type(self) -> None:
        assert UnitType.AnyType.type_ids == set(UnitTypeId)

    def test_the_workers_and_townhalls_are_those_curated(self) -> None:
        assert UnitType.Worker.type_ids == {
            UnitTypeId.SCV,
            UnitTypeId.PROBE,
            UnitTypeId.DRONE,
            UnitTypeId.DRONE_BURROWED,
        }
        assert UnitType.Townhall.type_ids == {
            UnitTypeId.COMMAND_CENTER,
            UnitTypeId.COMMAND_CENTER_FLYING,
            UnitTypeId.ORBITAL_COMMAND,
            UnitTypeId.ORBITAL_COMMAND_FLYING,
            UnitTypeId.PLANETARY_FORTRESS,
            UnitTypeId.NEXUS,
            UnitTypeId.HATCHERY,
            UnitTypeId.LAIR,
            UnitTypeId.HIVE,
        }
        assert UnitType.AnyTerranTownhall.type_ids == UnitType.Townhall.type_ids & UnitType.Terran.type_ids
        assert UnitType.AnyZergTownhall.type_ids == {UnitTypeId.HATCHERY, UnitTypeId.LAIR, UnitTypeId.HIVE}

    def test_mineral_fields_geysers_and_gas_buildings_are_what_the_tables_say_holds_minerals_or_vespene(
        self, first_corpus_tables: GameData
    ) -> None:
        rows = [first_corpus_tables.units[unit_type] for unit_type in UnitTypeId]
        vespene = {row.id for row in rows if row.has_vespene}
        assert UnitType.AnyMineralField.type_ids == {row.id for row in rows if row.has_minerals}
        assert UnitType.AnyVespeneGeyser.type_ids == vespene - UnitType.GasBuilding.type_ids
        assert UnitType.GasBuilding.type_ids == vespene & UnitType.Structure.type_ids - {
            row.id for row in rows if row.race is Race.NONE
        }
        counts = [len(group.type_ids) for group in (UnitType.AnyMineralField, UnitType.AnyVespeneGeyser)]
        assert counts == [15, 6]
        assert UnitTypeId.REFINERY_RICH in UnitType.GasBuilding.type_ids

    def test_an_add_on_is_a_tech_lab_or_a_reactor_plain_or_of_any_host(self) -> None:
        assert UnitType.AnyTechLab.type_ids == {
            UnitTypeId.TECH_LAB,
            UnitTypeId.TECH_LAB_BARRACKS,
            UnitTypeId.TECH_LAB_FACTORY,
            UnitTypeId.TECH_LAB_STARPORT,
        }
        assert UnitType.AnyReactor.type_ids == {
            UnitTypeId.REACTOR,
            UnitTypeId.REACTOR_BARRACKS,
            UnitTypeId.REACTOR_FACTORY,
            UnitTypeId.REACTOR_STARPORT,
        }
        assert UnitType.AnyAddOn.type_ids == UnitType.AnyTechLab.type_ids | UnitType.AnyReactor.type_ids


_MARINE_TABLES = make_tables(data_pb2.UnitTypeData(unit_id=UnitTypeId.MARINE))


def _unit_observed(unit_type: int) -> Unit[Any]:
    tracker = _Tracker(_MARINE_TABLES, make_enemy())
    tracker.update(make_observation(0, units=[make_unit(1, unit_type)]).observation.raw_data, 0)
    return tracker.unit_tracker.present[0]


class TestIncludes:
    def test_a_type_and_its_groups_include_a_unit_of_it(self) -> None:
        marine = _unit_observed(UnitTypeId.MARINE)
        assert UnitType.Marine.includes(marine)
        assert UnitType.Terran.includes(marine)
        assert not UnitType.Marauder.includes(marine)
        assert not UnitType.TerranStructure.includes(marine)

    def test_a_unit_of_an_uncurated_type_raises(self) -> None:
        with pytest.raises(UncuratedIdError):
            UnitType.Marine.includes(_unit_observed(RawUnitTypeId.Viking))


def _collections_are_typed_by_unit_type(units: Units[Unit[Any]], own: Units[OwnUnit[Any]]) -> None:
    """What the type checker makes of picking units by type. Never run."""
    assert_type(units.of_type(UnitType.Marine), Units[Unit[UnitType.Marine]])
    assert_type(own.of_type(UnitType.Marine), Units[OwnUnit[UnitType.Marine]])
    assert_type(own.of_type(UnitType.Marine, UnitType.Marauder), Units[OwnUnit[UnitType.Marine | UnitType.Marauder]])
    assert_type(units.own.of_type(UnitType.ProtossStructure), Units[OwnUnit[UnitType.ProtossStructure]])
    assert_type(units.of_type(UnitType.Marine).own, Units[OwnUnit[UnitType.Marine]])
    assert_type(units.of_type(UnitTypeId.MARINE), Units[Unit[Any]])
    assert_type(units.own.of_type(UnitType.Worker), Units[OwnUnit[UnitType.Worker]])
    assert_type(units.of_type(UnitType.AnyMineralField), Units[Unit[UnitType.AnyMineralField]])
    assert_type(own.excluding_type(UnitType.Marine), Units[OwnUnit[Any]])
    assert_type(units.of_type(UnitType.Marine, UnitTypeId.MARAUDER), Units[Unit[Any]])


def _a_unit_is_narrowed_by_unit_type(unit: Unit[Any], own_unit: OwnUnit[Any], other: OwnUnit[Any]) -> None:
    """What the type checker makes of `includes`. Never run."""
    if UnitType.Marine.includes(unit):
        assert_type(unit, Unit[UnitType.Marine])
    if UnitType.Terran.includes(own_unit):
        assert_type(own_unit, OwnUnit[UnitType.Terran])
    if UnitType.Marine.includes(other) or UnitType.Marauder.includes(other):
        assert_type(other, OwnUnit[UnitType.Marine] | OwnUnit[UnitType.Marauder])
    assert_type(UnitType.Marine.id, UnitTypeId)
    _ = UnitType.Terran.id  # pyright: ignore[reportAttributeAccessIssue]


def _what_a_structure_of_this_players_builds_and_carries_is_its_own(structure: OwnUnit[Any]) -> None:
    """What the type checker makes of the units an own structure names. Never run."""
    assert_type(structure.add_on, OwnUnit[Any] | None)
    assert_type(structure.construction, OwnUnit[Any] | None)
    assert_type(structure.builder, OwnUnit[Any] | None)
    if (builder := structure.builder) is not None:
        _ = builder.orders


def _a_group_is_typed_as_in_the_groups_all_its_types_are_in(
    depot: Unit[UnitType.AnySupplyDepot], drone: Unit[UnitType.AnyDrone]
) -> None:
    """What the type checker makes of the groups a group is in. Never run."""
    structure: Unit[UnitType.TerranStructure] = depot
    worker: Unit[UnitType.Worker] = drone
    structures: type[UnitType.Structure] = UnitType.CollapsibleTower
    workers: type[UnitType.Structure] = UnitType.Worker  # pyright: ignore[reportAssignmentType]
    del structure, worker, structures, workers


def _a_read_is_limited_by_unit_type(
    unit: Unit[Any], unknown: Unit[UnitType.AnyType], gateway: Unit[UnitType.Gateway], marine: Unit[UnitType.Marine]
) -> None:
    """What the type checker makes of a read only some types have. Never run."""
    assert_type(gateway.is_powered, bool)
    assert_type(unit.is_powered, bool)
    _ = marine.is_powered  # pyright: ignore[reportAttributeAccessIssue]
    _ = unknown.is_powered  # pyright: ignore[reportAttributeAccessIssue]
    if UnitType.ProtossStructure.includes(unknown):
        assert_type(unknown.is_powered, bool)
    any_type: type[UnitType.AnyType] = UnitType.Marine
    del any_type


def _each_read_only_some_types_have_is_limited_to_them(
    field: Unit[UnitType.MineralField750],
    geyser: Unit[UnitType.VespeneGeyser],
    extractor: OwnUnit[UnitType.Extractor],
    nexus: OwnUnit[UnitType.Nexus],
    barracks: OwnUnit[UnitType.BarracksFlying],
    factories: Units[OwnUnit[UnitType.Factory]],
    depot: OwnUnit[UnitType.AnySupplyDepot],
    marine: OwnUnit[UnitType.Marine],
    structure: OwnUnit[UnitType.TerranStructure],
    unit: OwnUnit[Any],
) -> None:
    """What the type checker makes of each read only some types have: on them, through `OwnUnit` and `Units`, on a
    unit typed `Any`, and on a type or a group they leave out. Never run."""
    assert_type(field.mineral_contents, int)
    assert_type(geyser.vespene_contents, int)
    assert_type(extractor.vespene_contents, int)
    assert_type(extractor.assigned_harvesters, int)
    assert_type(nexus.ideal_harvesters, int)
    assert_type(barracks.add_on, OwnUnit[Any] | None)
    for factory in factories:
        assert_type(factory.has_reactor, bool)
        assert_type(factory.has_tech_lab, bool)
    assert_type(depot.builder, OwnUnit[Any] | None)
    assert_type(unit.mineral_contents, int)
    assert_type(unit.vespene_contents, int)
    assert_type(unit.assigned_harvesters, int)
    assert_type(unit.ideal_harvesters, int)
    assert_type(unit.has_reactor, bool)
    assert_type(unit.has_tech_lab, bool)
    _ = marine.mineral_contents  # pyright: ignore[reportAttributeAccessIssue]
    _ = marine.vespene_contents  # pyright: ignore[reportAttributeAccessIssue]
    _ = marine.assigned_harvesters  # pyright: ignore[reportAttributeAccessIssue]
    _ = marine.ideal_harvesters  # pyright: ignore[reportAttributeAccessIssue]
    _ = marine.has_reactor  # pyright: ignore[reportAttributeAccessIssue]
    _ = marine.has_tech_lab  # pyright: ignore[reportAttributeAccessIssue]
    _ = marine.builder  # pyright: ignore[reportAttributeAccessIssue]
    _ = structure.add_on  # pyright: ignore[reportAttributeAccessIssue]


def _the_reads_every_unit_has_stay_on_every_unit(marine: OwnUnit[UnitType.Marine]) -> None:
    """What the type checker makes of the reads about shields, building and rallying on a unit that has none. Never
    run."""
    assert_type(marine.shield, float)
    assert_type(marine.shield_max, float)
    assert_type(marine.shield_fraction, float)
    assert_type(marine.shield_upgrade_level, int)
    assert_type(marine.shield_armor, float)
    assert_type(marine.construction, OwnUnit[Any] | None)
    assert_type(marine.rally_targets, tuple[RallyTarget, ...])
    assert_type(marine.production_capacity, int)
    assert_type(marine.is_constructing, bool)
