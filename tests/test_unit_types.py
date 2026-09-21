"""`UnitType`: a class naming each type of unit and each group of types, and narrowing a unit to one."""

# Each `pyright: ignore` below is an error the type checker must go on reporting.
# pyright: reportUnnecessaryTypeIgnoreComment=true

import importlib.util
from pathlib import Path
from typing import Any, assert_type

import pytest
from s2clientprotocol import data_pb2

from sc2nachos.enemy import Enemy
from sc2nachos.gamedata import Attribute, GameData
from sc2nachos.ids import UncuratedIdError, UnitTypeId
from sc2nachos.ids.raw import RawUnitTypeId
from sc2nachos.match import Race
from sc2nachos.units import OwnUnit, Unit, Units, UnitType
from sc2nachos.units import _unit_type as unit_type_module
from sc2nachos.units._tracking import _Tracker
from support import make_observation, make_tables, make_unit

_GENERATOR = Path(__file__).parents[1] / "tools" / "generate_unit_types.py"


def _generator() -> Any:
    """`tools/generate_unit_types.py`, which is no package to import from."""
    spec = importlib.util.spec_from_file_location("generate_unit_types", _GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tables() -> GameData:
    return _generator().corpus_tables()


def _types() -> list[Any]:
    """The classes in `UnitType` that each name one type, rather than a group."""
    return [member for member in vars(UnitType).values() if isinstance(member, type) and "id" in vars(member)]


class TestUnitType:
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

    def test_any_type_is_every_type(self) -> None:
        assert UnitType.AnyType._type_ids == set(UnitTypeId)


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
