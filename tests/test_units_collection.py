"""A collection of units, each query checked against working it out unit by unit."""

import math
import random
from collections.abc import Callable
from typing import Any

import pytest
from s2clientprotocol import data_pb2, raw_pb2

from sc2nachos.enemy import Enemy
from sc2nachos.geometry import Circle, Point, Rectangle
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.units import Alliance, NotReportedError, OwnUnit, Unit, Units, UnitType, Visibility
from sc2nachos.units._tracker import _UnitTracker
from support import make_observation, make_tables, make_unit

_TYPES = (UnitTypeId.MARINE, UnitTypeId.SCV, UnitTypeId.BARRACKS, UnitTypeId.SUPPLY_DEPOT)
_STRUCTURES = {UnitTypeId.BARRACKS, UnitTypeId.SUPPLY_DEPOT}
_TABLES = make_tables(
    *(
        data_pb2.UnitTypeData(
            unit_id=unit_type, attributes=[data_pb2.Attribute.Structure] if unit_type in _STRUCTURES else []
        )
        for unit_type in _TYPES
    )
)


def _units(*protos: raw_pb2.Unit) -> Units[Unit[Any]]:
    """`protos` observed once."""
    tracker = _UnitTracker(_TABLES, Enemy())
    tracker.update(make_observation(0, units=protos).observation.raw_data, 0)
    return tracker.present_units


def _mine(count: int, *, seed: int = 0) -> Units[OwnUnit[Any]]:
    """`count` of this player's units of assorted types, readiness and orders, scattered over a small map."""
    rng = random.Random(seed)
    protos = []
    for tag in range(1, count + 1):
        orders = [raw_pb2.UnitOrder(ability_id=AbilityId.GENERAL_MOVE)] if rng.random() < 0.5 else []
        protos.append(
            make_unit(
                tag,
                rng.choice(_TYPES),
                at=(rng.uniform(0, 40), rng.uniform(0, 40)),
                build_progress=rng.choice((1.0, 0.5)),
                orders=orders,
            )
        )
    return _units(*protos).own


def _mixed() -> Units[Unit[Any]]:
    """A unit of every alliance, the enemy's hidden and remembered too."""
    return _units(
        make_unit(1, alliance=Alliance.OWN),
        make_unit(2, alliance=Alliance.ENEMY),
        make_unit(3, alliance=Alliance.ENEMY, visibility=Visibility.INVISIBLE),
        make_unit(4, UnitTypeId.SUPPLY_DEPOT, alliance=Alliance.ENEMY, visibility=Visibility.IN_FOG),
        make_unit(5, alliance=Alliance.NEUTRAL, visibility=Visibility.IN_FOG),
        make_unit(6, alliance=Alliance.ALLY),
    )


def _tags(units: Units[Any]) -> list[int]:
    return [unit.tag for unit in units]


class TestSequence:
    def test_it_is_the_units_in_order(self) -> None:
        units = _mine(5)
        assert len(units) == 5
        assert _tags(units) == [1, 2, 3, 4, 5]
        assert units[0].tag == 1
        assert units[-1].tag == 5
        assert units[1] in units

    def test_a_slice_is_a_collection(self) -> None:
        units = _mine(5)[1:3]
        assert isinstance(units, Units)
        assert _tags(units) == [2, 3]

    def test_an_empty_collection_is_false(self) -> None:
        assert not Units()
        assert _mine(1)


class TestCombining:
    def test_collections_come_one_after_another(self) -> None:
        units = _mine(5)
        assert _tags(Units.combined(units[:2], units[3:])) == [1, 2, 4, 5]

    def test_a_unit_in_two_collections_is_kept_where_it_first_appears(self) -> None:
        units = _mine(5)
        assert _tags(Units.combined(units[2:], units[:3])) == [3, 4, 5, 1, 2]

    def test_it_takes_any_iterable_of_units(self) -> None:
        units = _mine(3)
        assert _tags(Units.combined([units[0]], iter(units))) == [1, 2, 3]

    def test_combining_nothing_is_empty(self) -> None:
        assert not Units.combined()
        assert not Units.combined(Units(), [])

    def test_the_units_are_found_by_id_afterwards(self) -> None:
        units = _mine(5)
        combined = Units.combined(units[:3], units)
        assert combined.ids == units.ids
        assert combined.by_id(100004) is units.by_id(100004)


class TestById:
    def test_a_unit_is_found_by_its_id(self) -> None:
        units = _mine(5)
        assert units.by_id(100003).tag == 3
        assert units.get(100003) is units.by_id(100003)
        assert units.ids == {100001, 100002, 100003, 100004, 100005}

    def test_an_id_without_a_unit(self) -> None:
        units = _mine(5)[1:]
        assert units.get(100001) is None
        with pytest.raises(KeyError):
            units.by_id(100001)

    def test_picking_by_ids_keeps_the_collections_order(self) -> None:
        units = _mine(5)
        assert _tags(units.with_ids([100004, 100002, 100009])) == [2, 4]
        assert _tags(units.without_ids({100004, 100002})) == [1, 3, 5]


class TestFilters:
    @pytest.mark.parametrize("wanted", [UnitTypeId.MARINE, [UnitTypeId.MARINE, UnitTypeId.BARRACKS], set()])
    def test_by_type_one_or_several(self, wanted: UnitTypeId | list[UnitTypeId]) -> None:
        units = _mine(40)
        types = {wanted} if isinstance(wanted, UnitTypeId) else set(wanted)
        assert _tags(units.of_type(wanted)) == [unit.tag for unit in units if unit.type_id in types]
        assert _tags(units.excluding_type(wanted)) == [unit.tag for unit in units if unit.type_id not in types]

    @pytest.mark.parametrize(
        "wanted",
        [
            (UnitType.Marine,),
            (UnitType.Marine, UnitType.Barracks),
            (UnitType.TerranStructure,),
            (UnitType.Structure, UnitType.Scv),
        ],
    )
    def test_by_unit_type_one_or_several_groups_included(self, wanted: tuple[type[UnitType.AnyType], ...]) -> None:
        units = _mine(40)
        types = frozenset[UnitTypeId]().union(*(unit_type._type_ids for unit_type in wanted))
        first, *rest = wanted
        assert _tags(units.of_type(first, *rest)) == [unit.tag for unit in units if unit.type_id in types]
        assert _tags(units.excluding_type(first, *rest)) == [unit.tag for unit in units if unit.type_id not in types]

    def test_by_type_takes_a_one_shot_iterable(self) -> None:
        units = _mine(40)
        assert _tags(units.of_type(iter([UnitTypeId.SCV]))) == _tags(units.of_type(UnitTypeId.SCV))

    def test_by_alliance(self) -> None:
        units = _mixed()
        assert _tags(units.own) == [1]
        assert _tags(units.enemy) == [2, 3, 4]
        assert _tags(units.neutral) == [5]
        assert _tags(units.allied) == [6]

    def test_structures_ready_and_idle(self) -> None:
        units = _mine(40)
        assert _tags(units.structures) == [unit.tag for unit in units if unit.type_id in _STRUCTURES]
        assert _tags(units.complete) == [unit.tag for unit in units if unit.build_progress == 1.0]
        assert _tags(units.idle) == [unit.tag for unit in units if not unit.orders]

    def test_a_filter_over_what_was_never_shown_in_sight_raises(self) -> None:
        """The hidden enemy was never in sight, so how far it is built was never reported."""
        with pytest.raises(NotReportedError):
            _ = _mixed().complete
        assert _tags(_mixed().own.idle) == [1]

    def test_by_predicate(self) -> None:
        units = _mine(40)
        assert _tags(units.filter(lambda unit: unit.position.x > 20)) == [
            unit.tag for unit in units if unit.position.x > 20
        ]


class TestWhereTheyAre:
    @pytest.mark.parametrize("area", [Circle((20, 20), 8), Rectangle(5, 5, 10, 20)], ids=repr)
    def test_in_an_area(self, area: Circle | Rectangle) -> None:
        units = _mine(60)
        assert _tags(units.in_area(area)) == [unit.tag for unit in units if unit.position in area]

    @pytest.mark.parametrize("point", [Point((20.0, 20.0)), (3.0, 37.5), Point((0.0, 0.0)).with_height(9.0)])
    def test_by_distance_to_a_point(self, point: Point | tuple[float, float]) -> None:
        units = _mine(60, seed=3)
        by_distance = sorted(units, key=lambda unit: unit.position.distance_to(point))
        assert _tags(units.sorted_by_distance_to(point)) == [unit.tag for unit in by_distance]
        assert _tags(units.closest(5, point)) == [unit.tag for unit in by_distance[:5]]
        assert units.closest_to(point) is by_distance[0]
        assert units.closest_distance_to(point) == pytest.approx(by_distance[0].position.distance_to(point))

    def test_asking_for_more_than_there_are_answers_them_all(self) -> None:
        units = _mine(3)
        assert len(units.closest(10, (0, 0))) == 3
        with pytest.raises(ValueError, match="cannot pick -1 units"):
            units.closest(-1, (0, 0))

    def test_units_at_equal_distance_keep_their_order(self) -> None:
        units = _units(make_unit(2, at=(1.0, 0.0)), make_unit(1, at=(-1.0, 0.0)), make_unit(3, at=(0.0, 1.0)))
        assert _tags(units.sorted_by_distance_to((0, 0))) == [2, 1, 3]
        assert units.closest_to((0, 0)).tag == 2

    def test_the_center_is_the_mean_position(self) -> None:
        units = _mine(30)
        count = len(units)
        expected = (sum(unit.position.x for unit in units) / count, sum(unit.position.y for unit in units) / count)
        assert units.center == pytest.approx(expected)

    @pytest.mark.parametrize(
        "query",
        [
            lambda units: units.closest_to((0, 0)),
            lambda units: units.closest_distance_to((0, 0)),
            lambda units: units.center,
        ],
        ids=["closest_to", "closest_distance_to", "center"],
    )
    def test_picking_from_nothing_raises(self, query: Callable[[Units[Unit]], object]) -> None:
        with pytest.raises(ValueError, match="no units"):
            query(Units())

    def test_an_area_is_not_a_point(self) -> None:
        with pytest.raises(TypeError, match="pass its .center"):
            _mine(3).closest_to(Circle((0, 0), 1))  # pyright: ignore[reportArgumentType]


def test_a_distance_is_measured_on_the_ground() -> None:
    units = _units(make_unit(1, at=(3.0, 4.0)))
    assert units.closest_distance_to(Point((0.0, 0.0)).with_height(100.0)) == pytest.approx(5.0)
    assert math.isclose(units.closest_distance_to((0.0, 0.0)), 5.0)
