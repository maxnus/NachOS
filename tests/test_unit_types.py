"""`UnitType`: a class for each unit type and each group of types, which narrows a unit to one."""

# Each `pyright: ignore` below marks an error the type checker must keep reporting.
# pyright: reportUnnecessaryTypeIgnoreComment=true

import importlib.util
from pathlib import Path
from typing import Any, assert_type

import pytest
from s2clientprotocol import data_pb2

from sc2nachos.enemy import Enemy
from sc2nachos.gamedata import Attribute, GameData
from sc2nachos.gamedata._techtree import TECH_TREE
from sc2nachos.ids import AbilityId, UncuratedIdError, UnitTypeId
from sc2nachos.ids.raw import RawUnitTypeId
from sc2nachos.match import Race
from sc2nachos.units import OwnUnit, Unit, Units, UnitType
from sc2nachos.units import _unit_type as unit_type_module
from sc2nachos.units._tracking import _Tracker
from support import make_observation, make_tables, make_unit

_GENERATOR = Path(__file__).parents[1] / "tools" / "generate_unit_types.py"


def _generator() -> Any:
    """The `tools/generate_unit_types.py` module, loaded by path since `tools` is not a package."""
    spec = importlib.util.spec_from_file_location("generate_unit_types", _GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tables() -> GameData:
    return _generator().corpus_tables()


def _types() -> list[Any]:
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


def _named(prefix: str) -> set[UnitTypeId]:
    return {unit_type for unit_type in UnitTypeId if unit_type.name.startswith(prefix)}


class TestUnitType:
    @pytest.mark.parametrize("group", sorted(_generator().TWO_FORMS), ids=str)
    def test_a_two_form_group_is_a_type_and_the_one_it_changes_to(self, group: str, tables: GameData) -> None:
        """One of the two morphs from the other in the tech tree, or the tables give it the other as a tech alias. The
        factory trains a hellion and a hellbat both, so neither links them; each performs the morph to the other."""
        first, second = sorted(getattr(UnitType, group)._type_ids, key=lambda type_id: type_id.name)
        if group == "AnyHellion":
            assert UnitTypeId.HELLION in tables.abilities[AbilityId.HELLION_MORPH_HELLBAT].performers
            assert UnitTypeId.HELLBAT in tables.abilities[AbilityId.HELLBAT_MORPH_HELLION].performers
            return
        linked = [
            TECH_TREE.morph_sources.get(one) is other or other in tables.units[one].tech_aliases
            for one, other in ((first, second), (second, first))
        ]
        assert any(linked), (first, second)

    def test_the_module_is_as_generated_from_the_curated_ids_and_the_tables(self, tables: GameData) -> None:
        generated = _generator().render(tables)
        written = Path(unit_type_module.__file__).read_text(encoding="utf-8")
        assert written == generated, "run tools/generate_unit_types.py"

    def test_every_curated_type_has_one_class_named_after_it(self) -> None:
        assert sorted(unit_type.id for unit_type in _types()) == sorted(UnitTypeId)
        assert all(unit_type._type_ids == {unit_type.id} for unit_type in _types())
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
        self, tables: GameData, race: Race, group: type[UnitType.AnyType], structures: type[UnitType.AnyType]
    ) -> None:
        rows = [tables.units[unit_type] for unit_type in UnitTypeId]
        assert group._type_ids == {row.id for row in rows if row.race is race}
        assert structures._type_ids == group._type_ids & UnitType.Structure._type_ids
        assert UnitType.Structure._type_ids == {row.id for row in rows if Attribute.STRUCTURE in row.attributes}

    @pytest.mark.parametrize("group", _curated_groups(), ids=lambda group: group.__name__)
    def test_a_group_is_in_a_race_or_structure_group_when_all_its_types_are(self, group: Any) -> None:
        for table_group in _TABLE_GROUPS:
            assert issubclass(group, table_group) == (group._type_ids <= table_group._type_ids), table_group

    def test_the_curated_groups_hold_every_type_named_for_them(self) -> None:
        towers = _named("COLLAPSIBLE_TOWER_") - _named("COLLAPSIBLE_TOWER_DEBRIS") - _named("COLLAPSIBLE_TOWER_PUSH")
        assert UnitType.CollapsibleTower._type_ids == towers
        assert UnitType.CollapsibleTowerDebris._type_ids == _named("COLLAPSIBLE_TOWER_DEBRIS")
        assert len(UnitType.CollapsibleTowerDebris._type_ids) == 7
        assert UnitType.Critter._type_ids == _named("CRITTER_")
        assert UnitType.AnyChangeling._type_ids == _named("CHANGELING")
        assert len(UnitType.AnyChangeling._type_ids) == 6
        assert UnitType.AnyCreepTumor._type_ids == _named("CREEP_TUMOR")
        assert len(UnitType.AnyCreepTumor._type_ids) == 3

    def test_any_type_is_every_type(self) -> None:
        assert UnitType.AnyType._type_ids == set(UnitTypeId)

    def test_the_workers_and_townhalls_are_those_curated(self) -> None:
        assert UnitType.Worker._type_ids == {
            UnitTypeId.SCV,
            UnitTypeId.PROBE,
            UnitTypeId.DRONE,
            UnitTypeId.DRONE_BURROWED,
        }
        assert UnitType.Townhall._type_ids == {
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

    def test_mineral_fields_geysers_and_gas_buildings_are_what_the_tables_say_holds_minerals_or_vespene(
        self, tables: GameData
    ) -> None:
        rows = [tables.units[unit_type] for unit_type in UnitTypeId]
        vespene = {row.id for row in rows if row.has_vespene}
        assert UnitType.AnyMineralField._type_ids == {row.id for row in rows if row.has_minerals}
        assert UnitType.AnyVespeneGeyser._type_ids == vespene - UnitType.GasBuilding._type_ids
        assert UnitType.GasBuilding._type_ids == vespene & UnitType.Structure._type_ids - {
            row.id for row in rows if row.race is Race.NONE
        }
        counts = [len(group._type_ids) for group in (UnitType.AnyMineralField, UnitType.AnyVespeneGeyser)]
        assert counts == [15, 6]
        assert UnitTypeId.REFINERY_RICH in UnitType.GasBuilding._type_ids

    def test_an_add_on_is_a_tech_lab_or_a_reactor_plain_or_of_any_host(self) -> None:
        assert UnitType.AnyTechLab._type_ids == {
            UnitTypeId.TECH_LAB,
            UnitTypeId.TECH_LAB_BARRACKS,
            UnitTypeId.TECH_LAB_FACTORY,
            UnitTypeId.TECH_LAB_STARPORT,
        }
        assert UnitType.AnyReactor._type_ids == {
            UnitTypeId.REACTOR,
            UnitTypeId.REACTOR_BARRACKS,
            UnitTypeId.REACTOR_FACTORY,
            UnitTypeId.REACTOR_STARPORT,
        }
        assert UnitType.AnyAddOn._type_ids == UnitType.AnyTechLab._type_ids | UnitType.AnyReactor._type_ids


_TABLES = make_tables(data_pb2.UnitTypeData(unit_id=UnitTypeId.MARINE))


def _unit(unit_type: int) -> Unit[Any]:
    tracker = _Tracker(_TABLES, Enemy())
    tracker.update(make_observation(0, units=[make_unit(1, unit_type)]).observation.raw_data, 0)
    return tracker.unit_tracker.present[0]


class TestIncludes:
    def test_a_type_and_its_groups_include_a_unit_of_it(self) -> None:
        marine = _unit(UnitTypeId.MARINE)
        assert UnitType.Marine.includes(marine)
        assert UnitType.Terran.includes(marine)
        assert not UnitType.Marauder.includes(marine)
        assert not UnitType.TerranStructure.includes(marine)

    def test_a_unit_of_an_uncurated_type_raises(self) -> None:
        with pytest.raises(UncuratedIdError):
            UnitType.Marine.includes(_unit(RawUnitTypeId.Viking))


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
    units.of_type(UnitType.Marine, UnitTypeId.MARINE)  # pyright: ignore[reportArgumentType]


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


def _a_group_is_typed_as_in_the_groups_all_its_types_are_in(depot: Unit[UnitType.AnySupplyDepot]) -> None:
    """What the type checker makes of a group's race and structure groups. Never run."""
    structure: Unit[UnitType.TerranStructure] = depot
    structures: type[UnitType.Structure] = UnitType.CollapsibleTower
    workers: type[UnitType.Structure] = UnitType.Worker  # pyright: ignore[reportAssignmentType]
    del structure, structures, workers


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
