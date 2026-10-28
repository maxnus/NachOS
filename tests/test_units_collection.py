"""A collection of units, each query checked against a unit-by-unit computation."""

import math
import random
from collections.abc import Callable
from typing import Any

import pytest
from s2clientprotocol import data_pb2, raw_pb2

from sc2nachos.geometry import Circle, Point, Rectangle, Tile
from sc2nachos.ids import AbilityId, BuffId, UnitTypeId
from sc2nachos.units import Alliance, NotReportedError, OwnUnit, Unit, Units, UnitType, Visibility
from sc2nachos.units._tracking import _Tracker
from support import make_enemy, make_observation, make_tables, make_unit

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
    tracker = _Tracker(_TABLES, make_enemy())
    tracker.update(make_observation(0, units=protos).observation.raw_data, 0)
    return tracker.unit_tracker.present


def _mine(count: int, *, seed: int = 0) -> Units[OwnUnit[Any]]:
    """`count` of this player's units, of assorted types, completeness and orders, scattered over a small map."""
    rng = random.Random(seed)
    protos = []
    for tag in range(1, count + 1):
        orders = [raw_pb2.UnitOrder(ability_id=AbilityId.MOVE)] if rng.random() < 0.5 else []
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
    """A unit of every alliance, plus a hidden and a remembered enemy."""
    return _units(
        make_unit(1, alliance=Alliance.OWN),
        make_unit(2, alliance=Alliance.ENEMY),
        make_unit(3, alliance=Alliance.ENEMY, visibility=Visibility.INVISIBLE),
        make_unit(4, UnitTypeId.SUPPLY_DEPOT, alliance=Alliance.ENEMY, visibility=Visibility.IN_FOG),
        make_unit(5, alliance=Alliance.NEUTRAL, visibility=Visibility.IN_FOG),
        make_unit(6, alliance=Alliance.ALLY),
    )


def _held() -> Units[Unit[Any]]:
    """Units kept from one observation to the next. In the next, 1 and the enemy's 5 stay in sight, 2 is left out, as
    in a transport, 3 is reported dead, a neural parasite hands 4 to the enemy, 6 stays a hidden enemy and 7 an enemy
    depot remembered in the fog, and the enemy's 8 leaves sight."""
    tracker = _Tracker(_TABLES, make_enemy())
    hidden = make_unit(6, alliance=Alliance.ENEMY, visibility=Visibility.INVISIBLE)
    remembered = make_unit(7, UnitTypeId.SUPPLY_DEPOT, alliance=Alliance.ENEMY, visibility=Visibility.IN_FOG)
    first = [make_unit(tag) for tag in (1, 2, 3, 4)]
    first += [make_unit(5, alliance=Alliance.ENEMY), hidden, remembered, make_unit(8, alliance=Alliance.ENEMY)]
    tracker.update(make_observation(0, units=first).observation.raw_data, 0)
    held = Units(tracker.unit_tracker.present)
    second = [make_unit(1), make_unit(4, alliance=Alliance.ENEMY), make_unit(5, alliance=Alliance.ENEMY), hidden]
    tracker.update(make_observation(16, units=[*second, remembered], dead=[3]).observation.raw_data, 16)
    return held


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


class TestTyping:
    def test_a_collection_of_own_units_is_a_collection_of_units(self) -> None:
        """For the type checker, which reads this test: `Units` is covariant, so a collection of own units is passed
        where one of units is asked for, and `orderable` and `own` read it."""
        own: Units[OwnUnit[Any]] = _mine(3)
        units: Units[Unit[Any]] = own
        assert list(own.orderable) == list(own.own) == list(units)


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

    def test_a_collection_combined_with_a_unit_or_units_adds_those_not_in_it(self) -> None:
        units = _mine(5)
        assert _tags(units[:2].combined_with(units[3])) == [1, 2, 4]
        assert _tags(units[:2].combined_with(units[1])) == [1, 2]
        assert _tags(units[:2].combined_with(units[1:4])) == [1, 2, 3, 4]
        assert _tags(Units().combined_with(iter(units[:2]))) == [1, 2]

    def test_a_collection_of_own_units_combined_with_a_unit_is_a_collection_of_units(self) -> None:
        """For the type checker too: `Units` stays covariant, so the result holds both kinds."""
        own: Units[OwnUnit[Any]] = _mine(2)
        (enemy,) = _units(make_unit(3, alliance=Alliance.ENEMY))
        combined: Units[Unit[Any]] = own.combined_with(enemy)
        assert _tags(combined) == [1, 2, 3]

    def test_the_units_are_found_by_id_afterwards(self) -> None:
        units = _mine(5)
        combined = Units.combined(units[:3], units)
        assert combined.ids == units.ids
        assert combined.by_id(100004) is units.by_id(100004)
        assert all(combined.by_id(unit.id) is unit for unit in combined)
        assert len(combined) == len(combined.ids)


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

    def test_picking_by_one_id(self) -> None:
        units = _mine(5)
        assert _tags(units.with_ids(100004)) == [4]
        assert _tags(units.without_ids(100004)) == [1, 2, 3, 5]


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
        types = frozenset[UnitTypeId]().union(*(unit_type.type_ids for unit_type in wanted))
        first, *rest = wanted
        assert _tags(units.of_type(first, *rest)) == [unit.tag for unit in units if unit.type_id in types]
        assert _tags(units.excluding_type(first, *rest)) == [unit.tag for unit in units if unit.type_id not in types]

    @pytest.mark.parametrize(
        "wanted",
        [
            (UnitTypeId.MARINE, UnitTypeId.BARRACKS),
            (UnitType.Marine, UnitTypeId.BARRACKS),
            (UnitTypeId.MARINE, UnitType.TerranStructure),
        ],
        ids=["two ids", "a type and an id", "an id and a group"],
    )
    def test_by_several_types_ids_and_groups_mixed(
        self, wanted: tuple[type[UnitType.AnyType] | UnitTypeId, ...]
    ) -> None:
        """As `only` takes them."""
        units = _mine(40)
        types = set[UnitTypeId]().union(*({w} if isinstance(w, UnitTypeId) else w.type_ids for w in wanted))
        first, *rest = wanted
        assert _tags(units.of_type(first, *rest)) == [unit.tag for unit in units if unit.type_id in types]
        assert _tags(units.excluding_type(first, *rest)) == [unit.tag for unit in units if unit.type_id not in types]

    @pytest.mark.parametrize(
        "given",
        [("MARINE",), ([UnitTypeId.MARINE], UnitTypeId.BARRACKS), ([UnitTypeId.MARINE, "BARRACKS"],), (1,)],
        ids=["a name", "a collection and more", "a collection of names", "a number"],
    )
    def test_by_anything_but_types_ids_and_groups_is_refused(self, given: tuple[Any, ...]) -> None:
        units = _mine(5)
        with pytest.raises(TypeError, match="UnitTypeId"):
            units.of_type(*given)
        with pytest.raises(TypeError, match="UnitTypeId"):
            units.excluding_type(*given)

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

    def test_structures_and_non_structures_split_the_units(self) -> None:
        """A lifted barracks is a structure, and a larva, an egg and a sieged tank are not."""
        units = _units(
            make_unit(1, UnitTypeId.SCV),
            make_unit(2, UnitTypeId.BARRACKS_FLYING),
            make_unit(3, UnitTypeId.LARVA),
            make_unit(4, UnitTypeId.EGG),
            make_unit(5, UnitTypeId.SIEGE_TANK_SIEGED),
            make_unit(6, UnitTypeId.SUPPLY_DEPOT_LOWERED, alliance=Alliance.ENEMY),
            make_unit(7, UnitTypeId.MARINE, alliance=Alliance.ENEMY),
        )
        assert _tags(units.non_structures) == [1, 3, 4, 5, 7]
        assert _tags(units.structures) == [2, 6]
        assert _tags(units.enemy.non_structures) == [7]

    def test_the_groups_a_bot_reads_most(self) -> None:
        neutral = Alliance.NEUTRAL
        units = _units(
            make_unit(1, UnitTypeId.SCV),
            make_unit(2, UnitTypeId.MULE),
            make_unit(3, UnitTypeId.COMMAND_CENTER_FLYING),
            make_unit(4, UnitTypeId.MINERAL_FIELD_750, alliance=neutral),
            make_unit(5, UnitTypeId.VESPENE_GEYSER_RICH, alliance=neutral),
            make_unit(6, UnitTypeId.EXTRACTOR, alliance=Alliance.ENEMY),
            make_unit(7, UnitTypeId.MARINE),
        )
        assert _tags(units.workers) == [1]
        assert _tags(units.townhalls) == [3]
        assert _tags(units.mineral_fields) == [4]
        assert _tags(units.geysers) == [5]
        assert _tags(units.gas_buildings) == [6]
        assert _tags(units.own.workers) == [1]

    def test_by_what_each_is_doing(self) -> None:
        units = _mine(40)
        moving = [unit.tag for unit in units if unit.orders and unit.orders[0].ability is AbilityId.MOVE]
        assert _tags(units.doing(AbilityId.MOVE)) == moving
        assert _tags(units.doing({AbilityId.MOVE, AbilityId.GATHER})) == moving
        assert _tags(units.doing(AbilityId.GATHER)) == []
        assert _tags(units.doing(())) == []

    def test_by_buff(self) -> None:
        units = _units(
            make_unit(1, buff_ids=[BuffId.MARINE_STIMMED]),
            make_unit(2, UnitTypeId.SCV, buff_ids=[BuffId.CARRYING_MINERALS]),
            make_unit(3, buff_ids=[BuffId.MARINE_STIMMED, BuffId.MEDIVAC_BOOST], alliance=Alliance.ENEMY),
            make_unit(4),
        )
        assert _tags(units.with_buff(BuffId.MARINE_STIMMED)) == [1, 3]
        assert _tags(units.with_buff({BuffId.MEDIVAC_BOOST, BuffId.CARRYING_MINERALS})) == [2, 3]
        assert _tags(units.with_buff(())) == []

    def test_a_filter_over_what_was_never_shown_visible_raises(self) -> None:
        """The hidden enemy was never visible, so neither its build progress nor its buffs were reported."""
        with pytest.raises(NotReportedError):
            _ = _mixed().complete
        with pytest.raises(NotReportedError):
            _mixed().with_buff(BuffId.MARINE_STIMMED)
        assert _tags(_mixed().own.idle) == [1]

    def test_without_some_of_them(self) -> None:
        units = _mine(10)
        assert _tags(units.without(units[2:5])) == [unit.tag for unit in units if unit not in units[2:5]]
        assert _tags(units.without([])) == _tags(units)

    def test_without_one_of_them(self) -> None:
        units = _mine(3)
        assert _tags(units.without(units[1])) == [1, 3]

    def test_those_not_complete_are_those_complete_leaves_out(self) -> None:
        units = _mine(40)
        assert _tags(units.not_complete) == [unit.tag for unit in units if unit.build_progress < 1.0]
        assert set(_tags(units.not_complete)) | set(_tags(units.complete)) == set(_tags(units))

    def test_on_the_ground_and_in_the_air(self) -> None:
        """A remembered structure never seen flying counts as on the ground, as `is_ground_structure` takes it."""
        units = _units(
            make_unit(1),
            make_unit(2, UnitTypeId.BARRACKS_FLYING, is_flying=True),
            make_unit(3, UnitTypeId.SUPPLY_DEPOT, alliance=Alliance.ENEMY, visibility=Visibility.IN_FOG),
        )
        assert _tags(units.ground) == [1, 3]
        assert _tags(units.air) == [2]

    def test_by_predicate(self) -> None:
        units = _mine(40)
        assert _tags(units.filter(lambda unit: unit.position.x > 20)) == [
            unit.tag for unit in units if unit.position.x > 20
        ]


class TestWhatBecameOfThem:
    """A collection kept from an earlier observation, filtered by what became of each unit since."""

    @pytest.mark.parametrize(
        ("units_filter", "unit_read"),
        [
            ("alive", "is_alive"),
            ("dead", "is_dead"),
            ("listed", "is_listed"),
            ("visible", "is_visible"),
            ("orderable", "is_orderable"),
        ],
    )
    def test_each_filter_keeps_the_units_its_read_is_true_of(self, units_filter: str, unit_read: str) -> None:
        held = _held()
        assert list(getattr(held, units_filter)) == [unit for unit in held if getattr(unit, unit_read)]

    def test_what_became_of_each(self) -> None:
        held = _held()
        assert _tags(held.dead) == [3]
        assert _tags(held.not_listed) == [2, 3, 8]
        assert _tags(held.visible) == [1, 4, 5]
        assert _tags(held.orderable) == [1]

    def test_alive_and_dead_split_the_units_and_so_do_listed_and_not_listed(self) -> None:
        held = _held()
        assert sorted(_tags(held.alive) + _tags(held.dead)) == sorted(_tags(held))
        assert sorted(_tags(held.listed) + _tags(held.not_listed)) == sorted(_tags(held))

    def test_a_unit_a_neural_parasite_holds_is_not_this_players_to_order(self) -> None:
        """It is the enemy's, of the enemy's class, though it was this player's when the collection was kept."""
        held = _held()
        (parasited,) = (unit for unit in held if unit.tag == 4)
        assert not isinstance(parasited, OwnUnit) and parasited not in held.orderable
        assert all(isinstance(unit, OwnUnit) for unit in held.orderable)


class TestWhereTheyAre:
    @pytest.mark.parametrize("area", [Circle((20, 20), 8), Rectangle(5, 5, 10, 20)], ids=repr)
    def test_in_an_area(self, area: Circle | Rectangle) -> None:
        units = _mine(60)
        assert _tags(units.inside(area)) == [unit.tag for unit in units if unit.position in area]

    @pytest.mark.parametrize("point", [Point((20.0, 20.0)), (3.0, 37.5), Point((0.0, 0.0)).with_height(9.0)])
    def test_by_distance_to_a_point(self, point: Point | tuple[float, float]) -> None:
        units = _mine(60, seed=3)
        by_distance = sorted(units, key=lambda unit: unit.position.distance_to(point))
        assert _tags(units.sorted_by_distance_to(point)) == [unit.tag for unit in by_distance]
        assert _tags(units.closest_n_to(5, point)) == [unit.tag for unit in by_distance[:5]]
        assert units.closest_to(point) is by_distance[0]
        assert units.closest_distance_to(point) == pytest.approx(by_distance[0].position.distance_to(point))

    @pytest.mark.parametrize("in_the_collection", [False, True], ids=["outside the collection", "in the collection"])
    def test_by_distance_to_a_unit_is_by_distance_to_its_position(self, in_the_collection: bool) -> None:
        units = _mine(60, seed=3)
        enemy_outside = _units(make_unit(99, at=(12.5, 30.0), alliance=Alliance.ENEMY))[0]
        reference_unit = units[7] if in_the_collection else enemy_outside
        by_distance = sorted(units, key=lambda other: other.position.distance_to(reference_unit.position))
        assert _tags(units.sorted_by_distance_to(reference_unit)) == [other.tag for other in by_distance]
        assert _tags(units.closest_n_to(5, reference_unit)) == [other.tag for other in by_distance[:5]]
        assert units.closest_to(reference_unit) is by_distance[0]
        nearest_distance = by_distance[0].position.distance_to(reference_unit.position)
        assert units.closest_distance_to(reference_unit) == pytest.approx(nearest_distance)

    def test_a_unit_in_the_collection_is_at_no_distance_from_itself(self) -> None:
        units = _mine(60, seed=3)
        assert units.closest_to(units[7]) is units[7]
        assert units.closest_distance_to(units[7]) == 0.0
        assert units.sorted_by_distance_to(units[7])[0] is units[7]

    def test_of_two_units_on_one_spot_the_first_is_the_nearest_to_either(self) -> None:
        units = _units(make_unit(1, at=(4.0, 4.0)), make_unit(2, at=(4.0, 4.0)), make_unit(3, at=(9.0, 4.0)))
        assert units.closest_to(units[1]) is units[0]
        assert _tags(units.sorted_by_distance_to(units[1])) == [1, 2, 3]

    @pytest.mark.parametrize("area", [Circle((20, 20), 8), Rectangle(5, 5, 10, 20)], ids=repr)
    def test_outside_an_area_are_those_inside_leaves_out(self, area: Circle | Rectangle) -> None:
        units = _mine(60)
        assert _tags(units.outside(area)) == [unit.tag for unit in units if unit.position not in area]
        areas = [area, Circle((35, 35), 4)]
        assert _tags(units.outside(areas)) == [
            unit.tag for unit in units if not any(unit.position in region for region in areas)
        ]

    def test_in_any_of_several_areas(self) -> None:
        units = _mine(60)
        areas = [Circle((10, 10), 5), Rectangle(25, 25, 10, 10)]
        assert _tags(units.inside(areas)) == [unit.tag for unit in units if any(unit.position in a for a in areas)]
        assert _tags(units.inside([])) == []

    @pytest.mark.parametrize("distance", [0.0, 6.5, 30.0])
    def test_within_a_distance_of_a_point_a_unit_or_several(self, distance: float) -> None:
        units = _mine(60, seed=5)
        anchor = units[0]
        points = [Point((5.0, 5.0)), (30.0, 12.0)]

        def near(*centers: Point | tuple[float, float]) -> list[int]:
            return [unit.tag for unit in units if any(unit.position.distance_to(c) <= distance for c in centers)]

        assert _tags(units.within(distance, of=points[0])) == near(points[0])
        assert _tags(units.within(distance, of=(30.0, 12.0))) == near((30.0, 12.0))
        assert _tags(units.within(distance, of=anchor)) == near(anchor.position)
        assert _tags(units.within(distance, of=points)) == near(*points)
        assert _tags(units.within(distance, of=[anchor, points[1]])) == near(anchor.position, points[1])
        assert _tags(units.within(distance, of=units[:3])) == near(*(unit.position for unit in units[:3]))

    @pytest.mark.parametrize("distance", [0.0, 6.5, 30.0])
    def test_beyond_a_distance_are_those_within_leaves_out(self, distance: float) -> None:
        units = _mine(60, seed=5)
        centers = [Point((5.0, 5.0)), units[0]]
        near = set(_tags(units.within(distance, of=centers)))
        assert _tags(units.beyond(distance, of=centers)) == [unit.tag for unit in units if unit.tag not in near]
        with pytest.raises(ValueError, match="a distance is at least 0"):
            units.beyond(-1.0, of=(0.0, 0.0))

    def test_sorted_by_a_key_keeps_ties_in_their_order(self) -> None:
        units = _units(make_unit(3, at=(1.0, 0.0)), make_unit(1, at=(5.0, 0.0)), make_unit(2, at=(1.0, 9.0)))
        assert _tags(units.sorted(key=lambda unit: unit.position.x)) == [3, 2, 1]
        assert _tags(units.sorted(key=lambda unit: unit.position.x, reverse=True)) == [1, 3, 2]
        assert isinstance(units.sorted(key=lambda unit: unit.tag), Units)

    def test_within_a_distance_of_a_tile_raises(self) -> None:
        with pytest.raises(TypeError):
            _mine(3).within(5, of=Tile(1, 2))  # pyright: ignore[reportArgumentType]

    @pytest.mark.parametrize("distance", [-1.0, math.nan])
    def test_within_a_negative_distance_raises(self, distance: float) -> None:
        with pytest.raises(ValueError, match="a distance is at least 0"):
            _mine(3).within(distance, of=(0.0, 0.0))

    def test_asking_for_more_than_there_are_answers_them_all(self) -> None:
        units = _mine(3)
        assert len(units.closest_n_to(10, (0, 0))) == 3
        with pytest.raises(ValueError, match="cannot pick -1 units"):
            units.closest_n_to(-1, (0, 0))

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
        with pytest.raises(TypeError):
            _mine(3).closest_to(Circle((0, 0), 1))  # pyright: ignore[reportArgumentType]


class TestStructureAt:
    def test_it_is_the_structure_whose_footprint_holds_the_point(self) -> None:
        """A depot at (4, 4) covers the tiles (3, 3) to (4, 4)."""
        units = _units(make_unit(1, UnitTypeId.SUPPLY_DEPOT, at=(4.0, 4.0)), make_unit(2, at=(5.5, 4.5)))
        (depot,) = units.of_type(UnitTypeId.SUPPLY_DEPOT)
        assert units.structure_at((3.5, 3.5)) is depot
        assert units.structure_at(Point((4.9, 4.1))) is depot
        assert units.structure_at((5.5, 4.5)) is None
        assert Units().structure_at((3.5, 3.5)) is None

    def test_a_tile_reads_as_itself(self) -> None:
        units = _units(make_unit(1, UnitTypeId.SUPPLY_DEPOT, at=(4.0, 4.0)))
        assert units.structure_at(Tile(3, 3)) is units[0]
        assert units.structure_at(Tile(5, 4)) is None

    def test_a_lifted_structure_covers_no_ground(self) -> None:
        units = _units(make_unit(1, UnitTypeId.BARRACKS, at=(4.5, 4.5), is_flying=True))
        assert units.structure_at((4.5, 4.5)) is None

    def test_one_in_sight_comes_before_a_snapshot_over_the_same_ground(self) -> None:
        snapshot = make_unit(
            1, UnitTypeId.SUPPLY_DEPOT, at=(5.0, 5.0), alliance=Alliance.ENEMY, visibility=Visibility.IN_FOG
        )
        units = _units(snapshot, make_unit(2, UnitTypeId.SUPPLY_DEPOT, at=(4.0, 4.0)))
        assert units.structure_at((4.5, 4.5)) is units.own[0]
        assert units.structure_at((5.5, 5.5)) is units.enemy[0]

    def test_a_snapshot_never_seen_in_sight_counts_as_on_the_ground(self) -> None:
        """The game never said whether it flies, and reading `is_flying` of it raises."""
        snapshot = make_unit(
            1, UnitTypeId.SUPPLY_DEPOT, at=(4.0, 4.0), alliance=Alliance.ENEMY, visibility=Visibility.IN_FOG
        )
        units = _units(snapshot)
        with pytest.raises(NotReportedError):
            _ = units[0].is_flying
        assert units.structure_at((4.5, 4.5)) is units[0]


def test_a_distance_is_measured_on_the_ground() -> None:
    units = _units(make_unit(1, at=(3.0, 4.0)))
    assert units.closest_distance_to(Point((0.0, 0.0)).with_height(100.0)) == pytest.approx(5.0)
    assert math.isclose(units.closest_distance_to((0.0, 0.0)), 5.0)
