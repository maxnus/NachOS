"""A game's units, from observations written here: ids, the fog, staleness, death, and every property."""

import math
import weakref
from contextlib import closing
from typing import Any

import pytest
from s2clientprotocol import common_pb2, data_pb2, debug_pb2, raw_pb2, sc2api_pb2

from sc2nachos import Api, GameTime, NachOSError
from sc2nachos.constants import STEPS_PER_SECOND
from sc2nachos.gamedata import Cost, GameData
from sc2nachos.gamedata._repair_steps import REPAIR_STEPS_EXCEPTIONS
from sc2nachos.geometry import Footprint, Point
from sc2nachos.ids import AbilityId, BuffId, UncuratedIdError, UnitTypeId, UpgradeId
from sc2nachos.ids.raw import RawAbilityId, RawBuffId, RawUnitTypeId
from sc2nachos.launch import GameProcess, MapFile, MapNotFoundError
from sc2nachos.match import Computer, Difficulty, Participant, Race, Result
from sc2nachos.protocol import Client, Status, WebSocketTransport
from sc2nachos.units import (
    Alliance,
    CloakState,
    NotReportedError,
    OwnUnit,
    Passenger,
    RallyTarget,
    Unit,
    UnitIdsExhaustedError,
    UnitOrder,
    UnknownTagError,
    Visibility,
)
from sc2nachos.units._tracking import _Tracker
from sc2nachos.units._tracking._unit_tracker import _MOVABLE_UNIT_TYPE_IDS
from support import (
    FakeTransport,
    RealGame,
    make_enemy,
    make_game_info,
    make_observation,
    make_response,
    make_tables,
    make_unit,
)

_TABLES = make_tables(
    data_pb2.UnitTypeData(unit_id=UnitTypeId.BARRACKS, attributes=[data_pb2.Attribute.Structure]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.MARINE, attributes=[data_pb2.Attribute.Biological]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.SPINE_CRAWLER, ability_id=AbilityId.DRONE_MORPH_SPINE_CRAWLER),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.EXTRACTOR, ability_id=AbilityId.DRONE_MORPH_EXTRACTOR),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.SUPPLY_DEPOT, ability_id=AbilityId.SCV_BUILD_SUPPLY_DEPOT),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.REFINERY, ability_id=AbilityId.SCV_BUILD_REFINERY),
    data_pb2.UnitTypeData(
        unit_id=UnitTypeId.BUNKER,
        race=Race.TERRAN.value,
        mineral_cost=100,
        build_time=640.0,
        attributes=[data_pb2.Attribute.Structure, data_pb2.Attribute.Mechanical],
    ),
    *(
        data_pb2.UnitTypeData(
            unit_id=tank,
            race=Race.TERRAN.value,
            mineral_cost=150,
            vespene_cost=125,
            build_time=build_steps,
            attributes=[data_pb2.Attribute.Mechanical],
        )
        for tank, build_steps in ((UnitTypeId.SIEGE_TANK, 720.0), (UnitTypeId.SIEGE_TANK_SIEGED, 68.67))
    ),
    data_pb2.UnitTypeData(
        unit_id=UnitTypeId.MULE, race=Race.TERRAN.value, mineral_cost=50, attributes=[data_pb2.Attribute.Mechanical]
    ),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.THOR),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.COLOSSUS),
    data_pb2.UnitTypeData(
        unit_id=UnitTypeId.STALKER,
        race=Race.PROTOSS.value,
        mineral_cost=125,
        vespene_cost=50,
        build_time=432.0,
        attributes=[data_pb2.Attribute.Mechanical],
    ),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.CREEP_TUMOR_QUEEN, attributes=[data_pb2.Attribute.Structure]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.INHIBITOR_ZONE_FLYING_LARGE),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.ACCELERATION_ZONE_LARGE),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.FORCE_FIELD, race=Race.PROTOSS.value),
    # The critters, at the speeds the corpus's tables give them.
    data_pb2.UnitTypeData(unit_id=UnitTypeId.CRITTER_CLEANING_BOT, movement_speed=2.0),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.CRITTER_LAB_BOT, movement_speed=2.25),
)
_ENEMY = Alliance.ENEMY
_IN_FOG = Visibility.IN_FOG
_INVISIBLE = Visibility.INVISIBLE


class _Game:
    """A tracker fed one observation at a time, as `Api.play` feeds it."""

    def __init__(self) -> None:
        self.tracker = _Tracker(_TABLES, make_enemy())

    def observe(self, step: int, *units: raw_pb2.Unit, dead: tuple[int, ...] = ()) -> list[Unit[Any]]:
        """Observe `units` at `step` and return the tracked unit for each, in the order given."""
        self.tracker.update(make_observation(step, units=units, dead=dead).observation.raw_data, step)
        by_tag = {unit.tag: unit for unit in self.tracker.unit_tracker.present}
        return [by_tag[unit.tag] for unit in units if unit.tag in by_tag]


def _one(proto: raw_pb2.Unit) -> Unit[Any]:
    """`proto` observed once."""
    return _Game().observe(0, proto)[0]


def _depot(tag: int, *, visibility: Visibility = Visibility.VISIBLE, **fields: Any) -> raw_pb2.Unit:
    """An enemy supply depot at a fixed spot, in sight or remembered."""
    return make_unit(tag, UnitTypeId.SUPPLY_DEPOT, at=(30.5, 40.5), alliance=_ENEMY, visibility=visibility, **fields)


class TestIds:
    def test_an_id_is_the_alliances_digit_then_a_count_of_its_units(self) -> None:
        units = _Game().observe(
            0,
            make_unit(11),
            make_unit(12),
            make_unit(13, alliance=Alliance.ALLY),
            make_unit(14, UnitTypeId.MINERAL_FIELD, alliance=Alliance.NEUTRAL),
            make_unit(15, alliance=_ENEMY),
            make_unit(16, alliance=_ENEMY),
        )
        assert [unit.id for unit in units] == [100001, 100002, 200001, 300001, 400001, 400002]

    def test_the_digit_is_the_alliance_first_seen_even_once_the_unit_changes_sides(self) -> None:
        """Tested in game: a neural parasite keeps the tag and hands the unit to the caster's player."""
        game = _Game()
        (marine,) = game.observe(0, make_unit(1, alliance=_ENEMY))
        game.observe(16, make_unit(1, alliance=Alliance.OWN))
        assert marine.id == 400001
        assert marine.alliance is Alliance.OWN
        assert marine.first_alliance is Alliance.ENEMY

    def test_a_unit_reads_the_alliance_of_its_latest_report(self) -> None:
        """Through a change between two sides neither of which is this player's."""
        game = _Game()
        (unit,) = game.observe(0, make_unit(1, alliance=Alliance.ALLY))
        game.observe(16, make_unit(1, alliance=_ENEMY))
        assert unit.alliance is _ENEMY
        assert list(game.tracker.unit_tracker.present.enemy) == [unit]
        assert not game.tracker.last_changes.units_alliance_changed

    def test_a_units_orders_are_read_once_per_report(self) -> None:
        game = _Game()
        moving = raw_pb2.UnitOrder(ability_id=AbilityId.MOVE, target_world_space_pos=common_pb2.Point(x=9.0, y=9.0))
        (marine,) = game.observe(0, make_unit(1, orders=[moving]))
        assert isinstance(marine, OwnUnit)
        orders = marine.orders
        assert marine.orders is orders and len(orders) == 1
        game.observe(16, make_unit(1))
        assert marine.orders == ()

    def test_a_unit_holds_no_report_but_its_latest_and_its_latest_visible(self) -> None:
        """Any other would keep the observation it came in alive, as a cache of the report its orders were last read
        from would once the unit is stale."""
        game = _Game()
        moving = raw_pb2.UnitOrder(ability_id=AbilityId.MOVE, target_world_space_pos=common_pb2.Point(x=9.0, y=9.0))
        (marine,) = game.observe(0, make_unit(1, orders=[moving]))
        assert isinstance(marine, OwnUnit) and marine.orders
        game.observe(16)
        assert marine.is_stale
        held = [getattr(marine, name) for name in Unit.__slots__ if hasattr(marine, name)]
        reports = [value for value in held if isinstance(value, raw_pb2.Unit)]
        assert all(report is marine._latest_report or report is marine._latest_visible_report for report in reports)

    def test_running_out_of_ids_raises(self) -> None:
        game = _Game()
        game.tracker.unit_tracker._units_seen_per_alliance[Alliance.OWN] = 99_998
        game.observe(0, make_unit(1))
        with pytest.raises(UnitIdsExhaustedError, match="all its ids"):
            game.observe(16, make_unit(1), make_unit(2))


class TestLifecycle:
    def test_a_unit_is_one_object_for_the_whole_game(self) -> None:
        game = _Game()
        (first,) = game.observe(0, make_unit(1))
        (again,) = game.observe(16, make_unit(1, at=(12.0, 10.0)))
        assert again is first
        assert first.position == Point((12.0, 10.0))
        assert not first.is_stale

    def test_a_unit_left_out_is_stale_and_keeps_what_it_last_read(self) -> None:
        game = _Game()
        (zergling,) = game.observe(0, make_unit(1, alliance=_ENEMY, at=(5.0, 6.0), health=35.0))
        game.observe(16)
        assert zergling.is_stale
        assert (zergling.position, zergling.health, zergling.last_seen) == (Point((5.0, 6.0)), 35.0, GameTime(steps=0))
        assert not game.tracker.unit_tracker.present
        assert list(game.tracker.unit_tracker.known) == [zergling]

    def test_a_stale_unit_that_comes_back_is_the_same_object(self) -> None:
        game = _Game()
        (zergling,) = game.observe(0, make_unit(1, alliance=_ENEMY))
        game.observe(16)
        (back,) = game.observe(32, make_unit(1, alliance=_ENEMY, at=(20.0, 20.0)))
        assert back is zergling
        assert not zergling.is_stale
        assert zergling.position == Point((20.0, 20.0))
        assert list(game.tracker.unit_tracker.known) == [zergling]

    def test_a_unit_the_game_reports_dead_is_dead_and_let_go(self) -> None:
        game = _Game()
        (marine,) = game.observe(0, make_unit(1, health=45.0))
        game.observe(16, dead=(1,))
        assert marine.is_dead
        assert marine.is_stale
        assert marine.health == 45.0
        assert not game.tracker.unit_tracker.known
        assert repr(marine) == "OwnUnit(MARINE, id=100001, at (10.00, 10.00), dead)"

    def test_the_dead_units_are_those_the_last_observation_reported_dead(self) -> None:
        """The game also reports deaths under tags it never reported a unit under (corpus); those name no unit."""
        game = _Game()
        (marine, _) = game.observe(0, make_unit(1), make_unit(2))
        game.observe(16, make_unit(2), dead=(1, 77))
        assert game.tracker.last_changes.units_died == [marine]
        assert not game.tracker.last_changes.units_found_dead
        game.observe(32, make_unit(2))
        assert not game.tracker.last_changes.units_died
        game.tracker.update(make_observation(48, dead=(2,)).observation.raw_data, 48)
        game.tracker.end()
        assert not game.tracker.last_changes.units_died

    def test_a_morph_keeps_the_unit_and_changes_its_type(self) -> None:
        game = _Game()
        (unit,) = game.observe(0, make_unit(1, UnitTypeId.BARRACKS))
        (morphed,) = game.observe(16, make_unit(1, UnitTypeId.BARRACKS_FLYING))
        assert morphed is unit
        assert unit.type_id is UnitTypeId.BARRACKS_FLYING

    def test_a_blip_and_a_placeholder_are_not_units(self) -> None:
        """Tested in game: neither has a tag, and a blip's type is `NotAUnit`."""
        blip = make_unit(0, RawUnitTypeId.NotAUnit, alliance=_ENEMY, visibility=_INVISIBLE, is_blip=True)
        placeholder = raw_pb2.Unit(
            unit_type=UnitTypeId.SUPPLY_DEPOT, alliance=raw_pb2.Self, display_type=raw_pb2.Placeholder
        )
        game = _Game()
        game.observe(0, blip, placeholder, make_unit(1))
        assert [unit.tag for unit in game.tracker.unit_tracker.present] == [1]

    def test_units_are_listed_in_the_order_the_game_reported_them_new_ones_included(self) -> None:
        game = _Game()
        game.observe(0, make_unit(3), make_unit(1))
        game.observe(16, make_unit(2), make_unit(3), make_unit(4), make_unit(1))
        assert [unit.tag for unit in game.tracker.unit_tracker.present] == [2, 3, 4, 1]

    def test_ending_the_game_leaves_every_unit_stale(self) -> None:
        game = _Game()
        (marine,) = game.observe(0, make_unit(1))
        game.tracker.end()
        assert marine.is_stale
        assert not game.tracker.unit_tracker.present


class TestTheFog:
    """Tested in game: a structure going out of sight is replaced, within the step, by a remembered copy under a new
    tag at a bit-identical position. Seen again, it is back under its first tag."""

    def test_a_structure_keeps_its_object_and_id_through_the_fog_and_back(self) -> None:
        game = _Game()
        (depot,) = game.observe(0, _depot(1, health=400.0))
        (remembered,) = game.observe(16, _depot(900, visibility=_IN_FOG))
        assert remembered is depot
        assert (depot.id, depot.tag, depot.visibility) == (400001, 900, _IN_FOG)
        assert depot.health == 400.0
        assert depot.last_seen == GameTime(steps=0)
        (seen,) = game.observe(32, _depot(1, health=300.0))
        assert seen is depot
        assert (depot.tag, depot.visibility, depot.health, depot.last_seen) == (
            1,
            Visibility.VISIBLE,
            300.0,
            GameTime(steps=32),
        )
        (again,) = game.observe(48, _depot(901, visibility=_IN_FOG))
        assert again is depot
        assert [unit.id for unit in game.tracker.unit_tracker.known] == [400001]
        assert depot.first_seen == GameTime(steps=0)

    def test_a_unit_is_first_seen_at_the_first_observation_that_shows_it_visible(self) -> None:
        """Not while it is in sight but undetected, and for good once seen, cloaked again or gone."""
        game = _Game()
        (banshee,) = game.observe(0, make_unit(2, alliance=_ENEMY, visibility=_INVISIBLE))
        assert banshee.first_seen is None
        game.observe(16, make_unit(2, alliance=_ENEMY))
        game.observe(32, make_unit(2, alliance=_ENEMY, visibility=_INVISIBLE))
        game.observe(48)
        assert (banshee.first_seen, banshee.last_seen) == (GameTime(steps=16), GameTime(steps=16))

    def test_a_mineral_field_never_seen_keeps_the_id_it_had_when_it_is_first_seen(self) -> None:
        field = {"at": (60.0, 20.5), "alliance": Alliance.NEUTRAL}
        game = _Game()
        (remembered,) = game.observe(0, make_unit(700, UnitTypeId.MINERAL_FIELD, visibility=_IN_FOG, **field))
        with pytest.raises(NotReportedError, match="never showed .* visible, so it never reported its minerals"):
            _ = remembered.mineral_contents
        (seen,) = game.observe(16, make_unit(5, UnitTypeId.MINERAL_FIELD, mineral_contents=1800, **field))
        assert seen is remembered
        assert (remembered.id, remembered.mineral_contents) == (300001, 1800)

    def test_a_structure_that_changes_form_as_it_goes_out_of_sight_is_still_matched(self) -> None:
        """Tested in game: the computer lowered a depot within the same turn it went out of sight."""
        game = _Game()
        (depot,) = game.observe(0, _depot(1))
        lowered = make_unit(900, UnitTypeId.SUPPLY_DEPOT_LOWERED, at=(30.5, 40.5), alliance=_ENEMY, visibility=_IN_FOG)
        (remembered,) = game.observe(60, lowered)
        assert remembered is depot
        assert depot.type_id is UnitTypeId.SUPPLY_DEPOT_LOWERED
        assert game.tracker.last_changes.units_type_changed == [(depot, UnitTypeId.SUPPLY_DEPOT)]

    @pytest.mark.parametrize(
        "copy",
        [
            make_unit(900, UnitTypeId.SUPPLY_DEPOT, at=(30.5, 41.0), alliance=_ENEMY, visibility=_IN_FOG),
            make_unit(900, UnitTypeId.SUPPLY_DEPOT, at=(30.5, 40.5), alliance=Alliance.NEUTRAL, visibility=_IN_FOG),
        ],
        ids=["another position", "another alliance"],
    )
    def test_a_copy_that_differs_is_another_unit(self, copy: raw_pb2.Unit) -> None:
        game = _Game()
        (depot,) = game.observe(0, _depot(1))
        (other,) = game.observe(16, copy)
        assert other is not depot
        assert depot.is_stale

    def test_a_structure_rebuilt_where_one_died_out_of_sight_is_another_unit(self) -> None:
        """A structure seen in vision comes back under its first tag, so a new tag in vision at its spot is new."""
        game = _Game()
        (old,) = game.observe(0, _depot(1, health=400.0))
        game.observe(16, _depot(900, visibility=_IN_FOG))
        (new,) = game.observe(32, _depot(2, health=100.0))
        changes = game.tracker.last_changes
        assert new is not old
        assert (old.is_dead, new.health) == (True, 100.0)
        assert (changes.units_found_dead, changes.enemy_units_first_seen) == ([old], [new])
        assert not changes.units_type_changed

    def test_a_copy_remembered_afresh_between_two_observations_is_the_same_unit(self) -> None:
        """Tested in game (tool `sweep_fog`, `retag`): a structure seen and lost again between two observations is
        remembered under a new copy tag, at the same position."""
        game = _Game()
        (depot,) = game.observe(0, _depot(1, health=400.0))
        game.observe(16, _depot(900, visibility=_IN_FOG))
        (remembered,) = game.observe(32, _depot(901, visibility=_IN_FOG))
        changes = game.tracker.last_changes
        assert remembered is depot
        assert (depot.tag, depot.health) == (901, 400.0)
        assert (changes.units_found_dead, changes.enemy_units_first_seen, changes.enemy_units_left_sight) == (
            [],
            [],
            [],
        )
        (seen,) = game.observe(48, _depot(1, health=300.0))
        assert seen is depot
        assert game.tracker.last_changes.enemy_units_entered_sight == [depot]

    def test_a_mineral_field_never_seen_remembered_afresh_is_the_same_unit(self) -> None:
        """A copy of the map's is remembered afresh alike: a geyser's was, in the scouting games of #163."""
        field = {"at": (60.0, 20.5), "alliance": Alliance.NEUTRAL}
        game = _Game()
        (remembered,) = game.observe(0, make_unit(700, UnitTypeId.MINERAL_FIELD, visibility=_IN_FOG, **field))
        (again,) = game.observe(16, make_unit(701, UnitTypeId.MINERAL_FIELD, visibility=_IN_FOG, **field))
        assert again is remembered
        assert not game.tracker.last_changes.units_found_dead

    def test_a_structure_rebuilt_on_its_spot_and_glimpsed_between_observations_is_found_out_once_seen(self) -> None:
        """Tested in game (tool `sweep_fog`, `rebuilt`): its copy comes as the old one's would, under a new tag at the
        same position, so it is taken for the old one until it is seen."""
        game = _Game()
        (old,) = game.observe(0, _depot(1, health=400.0))
        game.observe(16, _depot(900, visibility=_IN_FOG))
        (taken_for_the_old,) = game.observe(32, _depot(901, visibility=_IN_FOG))
        assert taken_for_the_old is old
        (new,) = game.observe(48, _depot(2, health=100.0))
        changes = game.tracker.last_changes
        assert new is not old
        assert (changes.units_found_dead, changes.enemy_units_first_seen) == ([old], [new])

    def test_another_type_built_on_the_spot_and_glimpsed_between_observations_reads_as_a_change_of_type(self) -> None:
        """The type is not compared, since a structure can change form in the fog, as a depot lowering does."""
        game = _Game()
        (old,) = game.observe(0, _depot(1))
        game.observe(16, _depot(900, visibility=_IN_FOG))
        turret = make_unit(901, UnitTypeId.MISSILE_TURRET, at=(30.5, 40.5), alliance=_ENEMY, visibility=_IN_FOG)
        (taken_for_the_old,) = game.observe(32, turret)
        changes = game.tracker.last_changes
        assert taken_for_the_old is old
        assert changes.units_type_changed == [(old, UnitTypeId.SUPPLY_DEPOT)]
        assert not (changes.units_found_dead or changes.enemy_units_first_seen)

    @pytest.mark.parametrize("reported", [1, 900], ids=["its visible tag", "its fog tag"])
    def test_a_copy_at_the_spot_of_a_structure_reported_dead_is_another_unit(self, reported: int) -> None:
        game = _Game()
        (depot,) = game.observe(0, _depot(1))
        game.observe(16, _depot(900, visibility=_IN_FOG))
        (copy,) = game.observe(32, _depot(901, visibility=_IN_FOG), dead=(reported,))
        assert copy is not depot
        assert depot.is_dead
        assert game.tracker.last_changes.units_died == [depot]

    def test_a_unit_arriving_where_another_left_is_not_it(self) -> None:
        """Only a swap between sight and memory is one unit; a visible unit replacing a visible one is not."""
        game = _Game()
        (first,) = game.observe(0, _depot(1))
        (second,) = game.observe(16, _depot(2))
        assert second is not first

    def test_a_structure_missing_from_its_spot_is_dead(self) -> None:
        """Tested in game: a depot burned down or killed out of sight is never reported dead. Its remembered copy
        disappears once the spot is in sight again, with nothing in its place."""
        game = _Game()
        (depot,) = game.observe(0, _depot(1))
        game.observe(16, _depot(900, visibility=_IN_FOG))
        game.observe(32)
        assert depot.is_dead
        assert not game.tracker.unit_tracker.known
        assert game.tracker.last_changes.units_found_dead == [depot]
        assert not game.tracker.last_changes.units_died

    @pytest.mark.parametrize("reported", [1, 900], ids=["its visible tag", "its fog tag"])
    def test_a_structure_the_game_reports_dead_as_its_spot_comes_back_into_sight_died(self, reported: int) -> None:
        """The report wins over finding it missing, as for a structure that can move."""
        game = _Game()
        (depot,) = game.observe(0, _depot(1))
        game.observe(16, _depot(900, visibility=_IN_FOG))
        game.observe(32, dead=(reported,))
        assert depot.is_dead
        assert (game.tracker.last_changes.units_died, game.tracker.last_changes.units_found_dead) == ([depot], [])
        assert not game.tracker.last_changes.enemy_units_left_sight

    def test_a_structure_that_can_move_missing_from_its_spot_is_stale_until_it_turns_up(self) -> None:
        game = _Game()
        at = {"at": (20.5, 20.5), "alliance": _ENEMY}
        (barracks,) = game.observe(0, make_unit(1, UnitTypeId.BARRACKS, **at))
        game.observe(16, make_unit(900, UnitTypeId.BARRACKS, visibility=_IN_FOG, **at))
        game.observe(32)
        assert barracks.is_stale
        assert not barracks.is_dead
        (landed,) = game.observe(48, make_unit(1, UnitTypeId.BARRACKS, at=(60.5, 30.5), alliance=_ENEMY))
        assert landed is barracks

    def test_a_copy_left_behind_is_ignored_only_while_the_game_lists_it(self) -> None:
        """Each return to sight leaves a copy to ignore; once the game stops listing it, it is forgotten."""
        game = _Game()
        game.observe(0, _depot(1))
        for cycle in range(1, 101):
            game.observe(32 * cycle - 16, _depot(900 + cycle, visibility=_IN_FOG))
            game.observe(32 * cycle, _depot(1))
        assert game.tracker.unit_tracker._ignored_tags == {1000}
        game.observe(3232, _depot(1))
        assert not game.tracker.unit_tracker._ignored_tags

    def test_the_structures_that_can_move_are_those_that_lift_off_or_uproot(self) -> None:
        movable = {UnitTypeId(value) for value in _MOVABLE_UNIT_TYPE_IDS}
        assert movable == {
            UnitTypeId.BARRACKS,
            UnitTypeId.COMMAND_CENTER,
            UnitTypeId.FACTORY,
            UnitTypeId.ORBITAL_COMMAND,
            UnitTypeId.SPINE_CRAWLER,
            UnitTypeId.SPORE_CRAWLER,
            UnitTypeId.STARPORT,
        }

    def test_a_unit_that_died_is_not_the_one_a_copy_at_its_position_is(self) -> None:
        game = _Game()
        (depot,) = game.observe(0, _depot(1))
        (copy,) = game.observe(16, _depot(900, visibility=_IN_FOG), dead=(1,))
        assert copy is not depot
        assert depot.is_dead

    @pytest.mark.parametrize("copy_first", [True, False], ids=["copy listed first", "copy listed last"])
    def test_a_structure_seen_where_it_moved_to_is_listed_once_while_its_copy_lingers(self, copy_first: bool) -> None:
        """Tested in game: a barracks that lifted off and landed out of sight, or a spine crawler that uprooted and
        rooted again, is reported in sight where it went while its remembered copy stays listed where it was."""
        barracks = UnitTypeId.BARRACKS
        copy = make_unit(900, barracks, at=(20.5, 20.5), alliance=_ENEMY, visibility=_IN_FOG)
        game = _Game()
        (unit,) = game.observe(0, make_unit(1, barracks, at=(20.5, 20.5), alliance=_ENEMY, health=1000.0))
        game.observe(16, copy)
        landed = make_unit(1, barracks, at=(60.5, 30.5), alliance=_ENEMY, health=900.0)
        for step in (32, 48):
            game.observe(step, *((copy, landed) if copy_first else (landed, copy)))
            assert list(game.tracker.unit_tracker.present) == [unit]
            assert (unit.position, unit.visibility, unit.health) == (Point((60.5, 30.5)), Visibility.VISIBLE, 900.0)
        game.observe(64, landed)
        assert list(game.tracker.unit_tracker.known) == [unit]
        assert not unit.is_stale


class TestWhatAUnitReads:
    def test_where_and_how_it_is_seen_read_now_and_the_rest_as_last_seen(self) -> None:
        """A banshee seen, then cloaked and undetected, keeps moving while its health stays as last seen."""
        game = _Game()
        (banshee,) = game.observe(0, make_unit(1, alliance=_ENEMY, at=(5.0, 5.0), health=140.0, owner=2))
        game.observe(16, make_unit(1, alliance=_ENEMY, at=(9.0, 5.0), visibility=_INVISIBLE, cloak=raw_pb2.Cloaked))
        assert (banshee.position, banshee.visibility, banshee.cloak_state) == (
            Point((9.0, 5.0)),
            _INVISIBLE,
            CloakState.CLOAKED,
        )
        assert (banshee.health, banshee.owner_id, banshee.last_seen) == (140.0, 2, GameTime(steps=0))

    def test_a_unit_is_visible_while_the_last_observation_shows_it_so(self) -> None:
        """Not once it is invisible, remembered in the fog, or gone from the observation, though its `visibility` then
        reads as it was last listed."""
        game = _Game()
        (depot,) = game.observe(0, _depot(1))
        assert depot.is_visible
        game.observe(16, _depot(900, visibility=_IN_FOG))
        assert not depot.is_visible
        (banshee,) = game.observe(32, make_unit(2, alliance=_ENEMY, visibility=_INVISIBLE))
        assert not banshee.is_visible
        (marine,) = game.observe(48, make_unit(3, alliance=_ENEMY))
        game.observe(64)
        assert (marine.is_stale, marine.visibility, marine.is_visible) == (True, Visibility.VISIBLE, False)

    def test_a_unit_visible_or_invisible_is_in_sight_and_one_remembered_is_not(self) -> None:
        assert [visibility.in_sight for visibility in Visibility] == [True, False, True]
        assert list(Visibility) == [Visibility.VISIBLE, _IN_FOG, _INVISIBLE]

    def test_what_was_never_shown_visible_raises(self) -> None:
        mine = _one(make_unit(1, alliance=_ENEMY, visibility=_INVISIBLE, is_burrowed=True))
        assert mine.is_burrowed
        assert mine.last_seen is None
        for name in (
            "health",
            "owner_id",
            "build_progress",
            "buffs",
            "is_carrying_resource",
            "facing",
            "energy_fraction",
        ):
            with pytest.raises(NotReportedError):
                getattr(mine, name)

    def test_an_enemy_hallucination_stays_one_once_detected(self) -> None:
        """The game flags it only while a detector is near (in game)."""
        game = _Game()
        stalker = {"alliance": _ENEMY, "at": (5.0, 5.0)}
        (unit,) = game.observe(0, make_unit(1, UnitTypeId.STALKER, is_hallucination=False, **stalker))
        assert not unit.is_hallucination
        game.observe(16, make_unit(1, UnitTypeId.STALKER, is_hallucination=True, **stalker))
        game.observe(32, make_unit(1, UnitTypeId.STALKER, is_hallucination=False, **stalker))
        assert unit.is_hallucination

    def test_flying_and_burrowing_of_a_remembered_structure_are_as_last_seen(self) -> None:
        game = _Game()
        (depot,) = game.observe(0, _depot(1, is_flying=False))
        game.observe(16, _depot(900, visibility=_IN_FOG, is_flying=True))
        assert not depot.is_flying

    def test_a_unit_held_up_by_a_graviton_beam_is_flying(self) -> None:
        assert _one(make_unit(1, alliance=_ENEMY, buff_ids=[BuffId.PHOENIX_GRAVITON_BEAM])).is_flying
        assert not _one(make_unit(1, alliance=_ENEMY, buff_ids=[BuffId.MARINE_STIMMED])).is_flying

    def test_identity_and_where_it_is(self) -> None:
        proto = make_unit(7, UnitTypeId.BARRACKS, at=(3.5, 4.25), owner=1, facing=1.5, radius=1.8125)
        proto.pos.z = 11.99
        unit = _one(proto)
        assert (unit.tag, unit.type_id, unit.owner_id, unit.alliance) == (7, UnitTypeId.BARRACKS, 1, Alliance.OWN)
        assert (unit.height, unit.facing, unit.radius) == pytest.approx((11.99, 1.5, 1.8125))
        assert unit.is_structure

    def test_a_ground_structure_is_one_not_lifted_or_one_remembered_out_of_sight(self) -> None:
        assert _one(make_unit(1, UnitTypeId.BARRACKS)).is_ground_structure
        assert not _one(make_unit(1, UnitTypeId.BARRACKS, is_flying=True)).is_ground_structure
        assert not _one(make_unit(1)).is_ground_structure
        remembered = _one(make_unit(1, UnitTypeId.BARRACKS, alliance=_ENEMY, visibility=_IN_FOG))
        assert remembered.is_ground_structure
        with pytest.raises(NotReportedError):
            _ = remembered.is_flying

    def test_vitals_and_their_fractions(self) -> None:
        unit = _one(make_unit(1, health=30.0, health_max=40.0, shield=0.0, shield_max=0.0, energy=50, energy_max=200))
        assert (unit.health_fraction, unit.shield_fraction, unit.energy_fraction) == (0.75, 0.0, 0.25)

    def test_life_is_health_and_shield_together(self) -> None:
        unit = _one(make_unit(1, health=40.0, health_max=80.0, shield=20.0, shield_max=80.0))
        assert (unit.life, unit.life_max, unit.life_fraction) == (60.0, 160.0, 0.375)
        assert _one(make_unit(1)).life_fraction == 0.0

    def test_completeness_is_being_fully_built(self) -> None:
        assert _one(make_unit(1, build_progress=1.0)).is_complete
        assert not _one(make_unit(1, build_progress=0.99)).is_complete

    def test_buffs_are_the_curated_ids(self) -> None:
        unit = _one(make_unit(1, buff_ids=[BuffId.MARINE_STIMMED, BuffId.MEDIVAC_BOOST]))
        assert unit.buffs == {BuffId.MARINE_STIMMED, BuffId.MEDIVAC_BOOST}

    @pytest.mark.parametrize(
        "game_id",
        [
            RawBuffId.CarryMineralFieldMinerals,
            RawBuffId.CarryHighYieldMineralFieldMinerals,
            RawBuffId.CarryHarvestableVespeneGeyserGas,
            RawBuffId.CarryHarvestableVespeneGeyserGasProtoss,
            RawBuffId.CarryHarvestableVespeneGeyserGasZerg,
        ],
        ids=lambda game_id: game_id.name,
    )
    def test_a_worker_carries_a_resource_under_each_carrying_buff(self, game_id: RawBuffId) -> None:
        """This player's SCV, and an enemy probe in sight, under each of the game's five ids."""
        assert _one(make_unit(1, UnitTypeId.SCV, buff_ids=[game_id])).is_carrying_resource
        assert _one(make_unit(1, UnitTypeId.PROBE, alliance=_ENEMY, buff_ids=[game_id])).is_carrying_resource

    def test_a_unit_without_a_carrying_buff_carries_nothing(self) -> None:
        assert not _one(make_unit(1, UnitTypeId.SCV)).is_carrying_resource
        assert not _one(make_unit(1, buff_ids=[BuffId.MARINE_STIMMED])).is_carrying_resource

    def test_a_unit_has_a_buff_or_any_of_several(self) -> None:
        marine = _one(make_unit(1, buff_ids=[BuffId.MARINE_STIMMED, BuffId.MEDIVAC_BOOST]))
        assert marine.has_buff(BuffId.MARINE_STIMMED)
        assert not marine.has_buff(BuffId.QUEEN_TRANSFUSED)
        assert marine.has_buff({BuffId.QUEEN_TRANSFUSED, BuffId.MEDIVAC_BOOST})
        assert not marine.has_buff([BuffId.QUEEN_TRANSFUSED])
        assert not marine.has_buff(())
        assert not _one(make_unit(1)).has_buff(set(BuffId))

    def test_a_buff_has_its_every_game_id(self) -> None:
        """A drone's gas comes under the Zerg's own id, which reads as `CARRYING_GAS`."""
        drone = _one(make_unit(1, UnitTypeId.DRONE, buff_ids=[RawBuffId.CarryHarvestableVespeneGeyserGasZerg]))
        assert drone.has_buff(BuffId.CARRYING_GAS)
        assert drone.buffs == {BuffId.CARRYING_GAS}

    def test_whether_a_unit_never_shown_visible_has_a_buff_raises(self) -> None:
        mine = _one(make_unit(1, alliance=_ENEMY, visibility=_INVISIBLE, is_burrowed=True))
        with pytest.raises(NotReportedError):
            mine.has_buff(BuffId.MARINE_STIMMED)

    def test_a_unit_is_doing_its_current_orders_ability_alone(self) -> None:
        """The game's own id for an SCV's gather reads as `GATHER`, and a queued order does not count."""
        gather_order = raw_pb2.UnitOrder(ability_id=RawAbilityId.Harvest_Gather_SCV)
        queued_return_order = raw_pb2.UnitOrder(ability_id=RawAbilityId.Harvest_Return_SCV)
        scv = _one(make_unit(1, UnitTypeId.SCV, orders=[gather_order, queued_return_order]))
        assert isinstance(scv, OwnUnit)
        assert scv.is_doing(AbilityId.GATHER)
        assert not scv.is_doing(AbilityId.RETURN)
        assert scv.is_doing({AbilityId.GATHER, AbilityId.RETURN})
        assert scv.is_doing([AbilityId.MOVE, AbilityId.GATHER])
        assert not scv.is_doing([AbilityId.MOVE])
        assert not scv.is_doing(())

    def test_an_idle_unit_is_doing_nothing(self) -> None:
        marine = _one(make_unit(1))
        assert isinstance(marine, OwnUnit)
        assert not marine.is_doing(AbilityId.MOVE)
        assert not marine.is_doing(set(AbilityId))

    def test_what_a_unit_is_doing_follows_its_latest_report(self) -> None:
        """Whether or not its orders were read under the report before."""
        game = _Game()
        gather_order = raw_pb2.UnitOrder(ability_id=RawAbilityId.Harvest_Gather_SCV)
        return_order = raw_pb2.UnitOrder(ability_id=RawAbilityId.Harvest_Return_SCV)
        (scv,) = game.observe(0, make_unit(1, UnitTypeId.SCV, orders=[gather_order]))
        assert isinstance(scv, OwnUnit)
        assert scv.orders[0].ability is AbilityId.GATHER and scv.is_doing(AbilityId.GATHER)
        game.observe(16, make_unit(1, UnitTypeId.SCV, orders=[return_order]))
        assert scv.is_doing(AbilityId.RETURN) and not scv.is_doing(AbilityId.GATHER)
        game.observe(32, make_unit(1, UnitTypeId.SCV))
        assert not scv.is_doing({AbilityId.GATHER, AbilityId.RETURN})

    def test_a_unit_is_this_players_or_the_enemys(self) -> None:
        own = _one(make_unit(1))
        enemy = _one(make_unit(2, alliance=_ENEMY))
        field = _one(make_unit(3, UnitTypeId.MINERAL_FIELD, alliance=Alliance.NEUTRAL))
        assert [(unit.is_own, unit.is_enemy) for unit in (own, enemy, field)] == [
            (True, False),
            (False, True),
            (False, False),
        ]

    def test_a_unit_the_last_observation_left_out_is_stale_and_one_it_listed_is_not(self) -> None:
        """Listed includes in sight but cloaked, which `is_visible` is not."""
        game = _Game()
        cloaked, gone = game.observe(0, make_unit(1, alliance=_ENEMY, visibility=_INVISIBLE), make_unit(2))
        assert cloaked.is_not_stale and not cloaked.is_visible
        game.observe(16, make_unit(1, alliance=_ENEMY, visibility=_INVISIBLE))
        assert not gone.is_not_stale and gone.is_stale

    @pytest.mark.parametrize(
        ("game_id", "carrying"),
        [
            (RawBuffId.CarryMineralFieldMinerals, "minerals"),
            (RawBuffId.CarryHighYieldMineralFieldMinerals, "minerals"),
            (RawBuffId.CarryHarvestableVespeneGeyserGas, "gas"),
            (RawBuffId.CarryHarvestableVespeneGeyserGasProtoss, "gas"),
            (RawBuffId.CarryHarvestableVespeneGeyserGasZerg, "gas"),
        ],
        ids=lambda value: value.name if isinstance(value, RawBuffId) else value,
    )
    def test_a_worker_carries_minerals_or_gas_by_its_buff(self, game_id: RawBuffId, carrying: str) -> None:
        worker = _one(make_unit(1, UnitTypeId.SCV, buff_ids=[game_id]))
        assert (worker.is_carrying_minerals, worker.is_carrying_gas) == (carrying == "minerals", carrying == "gas")

    def test_a_worker_without_a_carrying_buff_carries_neither(self) -> None:
        worker = _one(make_unit(1, UnitTypeId.SCV))
        assert not worker.is_carrying_minerals and not worker.is_carrying_gas

    def test_an_uncurated_type_raises_as_the_observation_is_taken_in_and_names_the_id(self) -> None:
        """A type the game reports belongs among the curated ids, so a missing one is a mistake to fix."""
        with pytest.raises(UncuratedIdError, match=r"UnitTypeId has no member for id \d+ \(RawUnitTypeId.Viking\)"):
            _one(make_unit(1, RawUnitTypeId.Viking))

    def test_an_uncurated_buff_raises_when_read(self) -> None:
        with pytest.raises(UncuratedIdError, match="DutchMarauderSlow"):
            _ = _one(make_unit(1, buff_ids=[RawBuffId.DutchMarauderSlow])).buffs

    def test_the_errors_are_the_librarys_own_and_the_built_ins_they_are(self) -> None:
        assert issubclass(NotReportedError, NachOSError) and issubclass(NotReportedError, LookupError)
        assert issubclass(UnknownTagError, NachOSError) and issubclass(UnknownTagError, LookupError)
        assert issubclass(UncuratedIdError, NachOSError) and issubclass(UncuratedIdError, ValueError)


def _drone_building(tag: int, ability: AbilityId, **target: Any) -> raw_pb2.Unit:
    """This player's drone at a fixed spot, carrying out `ability` aimed at `target`."""
    order = raw_pb2.UnitOrder(ability_id=ability, **target)
    return make_unit(tag, UnitTypeId.DRONE, at=(20.0, 20.0), orders=[order])


def _spine(tag: int, progress: float) -> raw_pb2.Unit:
    return make_unit(tag, UnitTypeId.SPINE_CRAWLER, at=(24.0, 20.0), build_progress=progress)


def test_a_unit_measures_its_distance_to_a_point_or_another_unit_between_positions() -> None:
    game = _Game()
    first, second = game.observe(0, make_unit(1, at=(3.0, 4.0)), make_unit(2, at=(6.0, 8.0), radius=2.0))
    assert first.distance_to(second) == pytest.approx(5.0)
    assert first.distance_to(Point((0.0, 0.0))) == pytest.approx(5.0)
    assert first.distance_to((3.0, 1.0)) == pytest.approx(3.0)


class TestFootprint:
    def test_a_unit_covers_its_types_footprint(self) -> None:
        assert _one(_depot(1)).footprint == Footprint.square(2)

    def test_a_unit_that_is_no_structure_has_no_footprint(self) -> None:
        assert _one(make_unit(1, UnitTypeId.MARINE)).footprint is None

    def test_a_force_field_covers_nothing_though_no_player_owns_it(self) -> None:
        """A sentry's force field is a neutral unit (in game), of a protoss type the sweep did not measure."""
        force_field = make_unit(1, UnitTypeId.FORCE_FIELD, alliance=Alliance.NEUTRAL, radius=1.0)
        assert _one(force_field).footprint is None

    @pytest.mark.parametrize("critter", [UnitTypeId.CRITTER_CLEANING_BOT, UnitTypeId.CRITTER_LAB_BOT])
    def test_a_critter_covers_nothing_since_it_moves(self, critter: UnitTypeId) -> None:
        """A structure ordered over a critter goes up once the critter has walked off its site (in game)."""
        assert _one(make_unit(1, critter, alliance=Alliance.NEUTRAL, radius=0.375)).footprint is None

    def test_one_of_the_maps_units_with_no_footprint_measured_covers_the_square_its_radius_spans(self) -> None:
        zone = make_unit(1, UnitTypeId.INHIBITOR_ZONE_FLYING_LARGE, alliance=Alliance.NEUTRAL, radius=2.6)
        assert _one(zone).footprint == Footprint.square(5)

    def test_one_of_the_maps_units_keeps_the_square_its_radius_spans(self) -> None:
        zone = _one(make_unit(1, UnitTypeId.INHIBITOR_ZONE_FLYING_LARGE, alliance=Alliance.NEUTRAL, radius=2.6))
        assert zone.footprint is zone.footprint

    def test_one_of_the_maps_units_spanning_no_tile_covers_nothing(self) -> None:
        zone = make_unit(1, UnitTypeId.ACCELERATION_ZONE_LARGE, alliance=Alliance.NEUTRAL, radius=0.0)
        assert _one(zone).footprint is None


class TestRepair:
    """What one SCV's repair of a unit takes, from its health now and from none: the steps its type costs, but for the
    types `tools/sweep_repair.py` measured taking others, and a quarter of the resources its type costs."""

    @staticmethod
    def _tank(health: float, unit_type: UnitTypeId = UnitTypeId.SIEGE_TANK) -> Unit[Any]:
        """A siege tank, which costs 150 minerals, 125 vespene and 720 steps, at `health` of its 175."""
        return _one(make_unit(1, unit_type, health=health, health_max=175.0, build_progress=1.0))

    def test_a_whole_repair_takes_the_steps_its_type_costs_and_a_quarter_of_its_resources_rounded_up(self) -> None:
        """A quarter of 150 minerals and 125 vespene is 37.5 and 31.25."""
        assert self._tank(87.5).total_repair_cost == Cost(38, 32, time=GameTime(steps=720))

    def test_a_form_switched_into_for_nothing_is_repaired_as_the_form_it_switched_from(self) -> None:
        """A sieged tank costs the tank's steps, its siege's left out (in game)."""
        assert self._tank(87.5, UnitTypeId.SIEGE_TANK_SIEGED).total_repair_cost == Cost(
            38, 32, time=GameTime(steps=720)
        )

    def test_a_type_measured_taking_other_steps_than_it_costs_takes_those(self) -> None:
        """A MULE, whose row gives it no build steps, is repaired in 270 (in game)."""
        mule = _one(make_unit(1, UnitTypeId.MULE, health=30.0, health_max=60.0, build_progress=1.0))
        assert mule.total_repair_cost == Cost(13, 0, time=GameTime(steps=REPAIR_STEPS_EXCEPTIONS[UnitTypeId.MULE]))

    def test_it_takes_the_share_of_a_whole_repair_that_its_health_misses_each_resource_rounded_up(self) -> None:
        """Half of a quarter of 150 minerals and 125 vespene is 18.75 and 15.625."""
        assert self._tank(87.5).required_repair_cost == Cost(19, 16, time=GameTime(steps=360))

    def test_a_cost_that_comes_out_whole_is_not_rounded_up(self) -> None:
        """A bunker missing 112 of its 400 health costs 7 of a quarter of 100 minerals, which a share of the cost worked
        out first would make a little more than 7."""
        bunker = _one(make_unit(1, UnitTypeId.BUNKER, health=288.0, health_max=400.0, build_progress=1.0))
        assert bunker.required_repair_cost == Cost(7, 0, time=GameTime(steps=180))

    def test_a_unit_whole_takes_no_steps_and_costs_nothing(self) -> None:
        assert self._tank(175.0).required_repair_cost == Cost(0, 0)

    def test_a_unit_an_scv_is_refused_has_no_repair(self) -> None:
        """A marine is not mechanical (in game), and a stalker, though mechanical, is not Terran."""
        marine = _one(make_unit(1, UnitTypeId.MARINE, health=20.0, health_max=45.0, build_progress=1.0))
        stalker = _one(make_unit(2, UnitTypeId.STALKER, health=20.0, health_max=80.0, build_progress=1.0))
        repair_costs = [(unit.required_repair_cost, unit.total_repair_cost) for unit in (marine, stalker)]
        assert repair_costs == [(None, None), (None, None)]

    def test_a_unit_is_repairable_where_it_has_a_repair(self) -> None:
        tank = self._tank(87.5)
        marine = _one(make_unit(2, UnitTypeId.MARINE, health=20.0, health_max=45.0, build_progress=1.0))
        bunker = _one(make_unit(3, UnitTypeId.BUNKER, health=100.0, health_max=400.0, build_progress=0.5))
        assert [unit.is_repairable for unit in (tank, marine, bunker)] == [True, False, False]

    def test_a_structure_under_construction_has_no_repair(self) -> None:
        """An SCV ordered to repair one is refused `CantTargetUnderConstructionUnits` (in game)."""
        bunker = _one(make_unit(1, UnitTypeId.BUNKER, health=100.0, health_max=400.0, build_progress=0.5))
        assert (bunker.required_repair_cost, bunker.total_repair_cost) == (None, None)


class TestTurning:
    """The steps a unit turns in to face where it is sent, at its type's rate: 711/4096 of a full turn a step, 62.49
    degrees, for a marine and 512/4096, 45, for a thor, as `tools/sweep_turning.py` measured them."""

    @staticmethod
    def _facing_east(unit_type: UnitTypeId, tag: int = 1, at: tuple[float, float] = (10.0, 10.0)) -> Unit[Any]:
        """A `unit_type` of this player's at `at`, facing along the positive x-axis."""
        return _one(make_unit(tag, unit_type, at=at, facing=0.0))

    def test_a_turn_takes_its_angle_over_the_rate_rounded_up(self) -> None:
        """90 degrees is two steps of 45 for a thor, and 180 three of 62.49 for a marine."""
        thor, marine = self._facing_east(UnitTypeId.THOR), self._facing_east(UnitTypeId.MARINE)
        assert (thor.time_to_face((10.0, 20.0)), marine.time_to_face((0.0, 10.0))) == (
            GameTime(steps=2),
            GameTime(steps=3),
        )

    def test_it_turns_to_face_another_unit_as_it_would_its_position(self) -> None:
        marine = self._facing_east(UnitTypeId.MARINE)
        (behind,) = _Game().observe(0, make_unit(2, UnitTypeId.MARINE, at=(0.0, 10.0), alliance=_ENEMY))
        assert marine.time_to_face(behind) == GameTime(steps=3)

    def test_a_unit_that_faces_its_target_to_within_a_degree_takes_no_steps(self) -> None:
        marine = self._facing_east(UnitTypeId.MARINE)
        assert [marine.time_to_face(target) for target in ((20.0, 10.1), (10.0, 10.0))] == [GameTime(steps=0)] * 2

    def test_how_near_facing_counts_as_facing_can_be_given(self) -> None:
        """A target 5 degrees off is a step's turn by default, and none within 10."""
        marine = self._facing_east(UnitTypeId.MARINE)
        target = (10.0 + 10 * math.cos(math.radians(5)), 10.0 + 10 * math.sin(math.radians(5)))
        within_ten_degrees = marine.time_to_face(target, facing_tolerance=math.radians(10))
        assert (marine.time_to_face(target), within_ten_degrees) == (GameTime(steps=1), GameTime(steps=0))

    def test_a_type_whose_facing_never_changes_has_no_time_to_face(self) -> None:
        """A colossus moves with its facing as it was (in game)."""
        assert self._facing_east(UnitTypeId.COLOSSUS).time_to_face((0.0, 10.0)) is None


class TestUnitsThatBecomeStructures:
    """Tested in game: a drone that morphs into a structure leaves the observation with no death reported, and the
    structure appears under a new tag. The drone is reported dead as the structure finishes or is killed, or a step
    later, and comes back under its own tag when the structure is cancelled."""

    _AIMED: Any = {"target_world_space_pos": common_pb2.Point(x=24.0, y=20.0)}

    def _started(self) -> tuple[_Game, Unit[Any], Unit[Any]]:
        game = _Game()
        (drone,) = game.observe(0, _drone_building(1, AbilityId.DRONE_MORPH_SPINE_CRAWLER, **self._AIMED))
        (spine,) = game.observe(16, _spine(2, 0.1))
        return game, drone, spine

    def test_while_the_structure_is_built_the_drone_is_stale_and_its_builder(self) -> None:
        _, drone, spine = self._started()
        assert drone.is_stale
        assert not drone.is_dead
        assert isinstance(spine, OwnUnit) and isinstance(drone, OwnUnit)
        assert spine.builder is drone
        assert drone.construction is spine

    def test_an_update_after_the_structure_finishes_the_drone_is_dead(self) -> None:
        game, drone, _ = self._started()
        game.observe(32, _spine(2, 0.5))
        assert not drone.is_dead
        (spine,) = game.observe(48, _spine(2, 1.0))
        assert not drone.is_dead
        game.observe(64, _spine(2, 1.0))
        assert drone.is_dead
        assert drone.id not in game.tracker.unit_tracker.known.ids
        assert isinstance(spine, OwnUnit)
        assert spine.builder is None

    def test_the_drone_is_dead_as_the_game_reports_once_its_structure_finishes(self) -> None:
        game, drone, _ = self._started()
        game.observe(32, _spine(2, 1.0), dead=(1,))
        assert game.tracker.last_changes.units_died == [drone]
        assert not game.tracker.last_changes.units_found_dead

    def test_the_drone_is_dead_as_the_game_reports_an_observation_after_its_structure_finishes(self) -> None:
        game, drone, _ = self._started()
        game.observe(32, _spine(2, 1.0))
        assert not drone.is_dead
        game.observe(48, _spine(2, 1.0), dead=(1,))
        assert game.tracker.last_changes.units_died == [drone]
        assert not game.tracker.last_changes.units_found_dead

    def test_a_drone_the_game_did_not_report_is_found_dead_an_update_after_its_structure_finishes(self) -> None:
        game, drone, _ = self._started()
        game.observe(32, _spine(2, 1.0))
        assert not game.tracker.last_changes.units_found_dead
        game.observe(48, _spine(2, 1.0))
        assert game.tracker.last_changes.units_found_dead == [drone]

    def test_a_cancel_brings_the_same_drone_back(self) -> None:
        game, drone, spine = self._started()
        (back,) = game.observe(32, make_unit(1, UnitTypeId.DRONE, at=(24.0, 20.0)), dead=(2,))
        assert back is drone
        assert not drone.is_dead
        assert spine.is_dead
        game.observe(48, make_unit(1, UnitTypeId.DRONE, at=(24.0, 20.0)))
        assert not drone.is_dead

    def test_a_structure_destroyed_while_built_takes_the_drone_with_it(self) -> None:
        game, drone, _ = self._started()
        game.observe(32, dead=(2,))
        game.observe(48)
        assert drone.is_dead

    def test_an_extractor_is_matched_to_the_drone_through_the_geyser_it_was_aimed_at(self) -> None:
        game = _Game()
        geyser = make_unit(9, UnitTypeId.VESPENE_GEYSER, at=(30.5, 30.5), alliance=Alliance.NEUTRAL)
        drone, _ = game.observe(0, _drone_building(1, AbilityId.DRONE_MORPH_EXTRACTOR, target_unit_tag=9), geyser)
        extractor = make_unit(3, UnitTypeId.EXTRACTOR, at=(30.5, 30.5), build_progress=1.0)
        game.observe(16, extractor, geyser)
        game.observe(32, extractor, geyser)
        assert drone.is_dead

    def test_an_scv_gone_into_the_refinery_it_built_is_not_what_it_became(self) -> None:
        """A worker that built a gas structure goes inside it to harvest (in game), and comes out under its own tag."""
        game = _Game()
        geyser = make_unit(9, UnitTypeId.VESPENE_GEYSER, at=(30.5, 30.5), alliance=Alliance.NEUTRAL)
        order = raw_pb2.UnitOrder(ability_id=AbilityId.SCV_BUILD_REFINERY, target_unit_tag=9)
        scv, _ = game.observe(0, make_unit(1, UnitTypeId.SCV, at=(29.0, 30.5), orders=[order]), geyser)
        for step, progress in ((16, 0.1), (32, 1.0), (48, 1.0)):
            game.observe(step, make_unit(3, UnitTypeId.REFINERY, at=(30.5, 30.5), build_progress=progress), geyser)
        assert scv.is_stale and not scv.is_dead
        (back,) = game.observe(64, make_unit(1, UnitTypeId.SCV, at=(29.0, 30.5)))
        assert back is scv
        assert not game.tracker.last_changes.own_units_created

    def test_a_structure_far_from_where_the_drone_was_sent_is_not_what_it_became(self) -> None:
        game = _Game()
        (drone,) = game.observe(0, _drone_building(1, AbilityId.DRONE_MORPH_SPINE_CRAWLER, **self._AIMED))
        game.observe(16, make_unit(2, UnitTypeId.SPINE_CRAWLER, at=(40.0, 20.0), build_progress=1.0))
        assert drone.is_stale
        assert not drone.is_dead


def _scv(tag: int, *orders: raw_pb2.UnitOrder) -> raw_pb2.Unit:
    """One of this player's SCVs beside the depot `_building_depot` builds."""
    return make_unit(tag, UnitTypeId.SCV, at=(20.0, 20.0), orders=orders)


def _building_depot(tag: int, progress: float) -> raw_pb2.Unit:
    """One of this player's supply depots, `progress` of the way built."""
    return make_unit(tag, UnitTypeId.SUPPLY_DEPOT, at=(24.0, 20.0), build_progress=progress)


class TestConstruction:
    """Tested in game: an SCV's build order targets the structure's snapped center once construction starts, and the
    structure itself when another SCV resumes it. A halted SCV has no orders."""

    _BUILD = raw_pb2.UnitOrder(
        ability_id=AbilityId.SCV_BUILD_SUPPLY_DEPOT, target_world_space_pos=common_pb2.Point(x=24.3, y=19.8)
    )

    def test_a_queen_planting_a_tumor_is_its_builder_by_her_own_id_of_the_build(self) -> None:
        """She reports the queen's own id of the creep tumor build, which reads as the action's, the tumor's creation
        ability."""
        plant = raw_pb2.UnitOrder(
            ability_id=RawAbilityId.Build_CreepTumor_Queen, target_world_space_pos=common_pb2.Point(x=24.0, y=20.0)
        )
        tumor_at = make_unit(2, UnitTypeId.CREEP_TUMOR_QUEEN, at=(24.0, 20.0), build_progress=0.2)
        queen, tumor = _Game().observe(0, make_unit(1, UnitTypeId.QUEEN, at=(22.0, 20.0), orders=(plant,)), tumor_at)
        assert isinstance(queen, OwnUnit) and isinstance(tumor, OwnUnit)
        assert (queen.construction, tumor.builder) == (tumor, queen)

    def test_a_unit_whose_order_places_a_structure_is_constructing(self, first_corpus_tables: GameData) -> None:
        """Whether an ability places a structure is the game's tables' to say: an SCV's build on its way to the site, a
        barracks building its add-on and a queen planting a tumor, each by its own id, and not a gather."""
        tracker = _Tracker(first_corpus_tables, make_enemy())
        gather = raw_pb2.UnitOrder(ability_id=RawAbilityId.Harvest_Gather_SCV)
        reactor = raw_pb2.UnitOrder(ability_id=RawAbilityId.Build_Reactor_Barracks)
        plant = raw_pb2.UnitOrder(
            ability_id=RawAbilityId.Build_CreepTumor_Queen, target_world_space_pos=common_pb2.Point(x=30.0, y=30.0)
        )
        reports = [
            _scv(1, self._BUILD),
            make_unit(2, UnitTypeId.BARRACKS, at=(40.0, 40.0), build_progress=1.0, orders=[reactor]),
            make_unit(3, UnitTypeId.QUEEN, at=(28.0, 30.0), orders=[plant]),
            _scv(4, gather),
            _scv(5),
        ]
        tracker.update(make_observation(0, units=reports).observation.raw_data, 0)
        units = tracker.unit_tracker.present.own.sorted(key=lambda unit: unit.tag)
        assert [unit.is_constructing for unit in units] == [True, True, True, False, False]

    def test_an_scv_with_a_construction_is_constructing(self) -> None:
        scv, depot = _Game().observe(0, _scv(1, self._BUILD), _building_depot(2, 0.3))
        assert isinstance(scv, OwnUnit) and scv.construction is depot
        assert scv.is_constructing

    def test_an_scv_building_a_structure_is_its_builder_until_it_finishes(self) -> None:
        game = _Game()
        scv, depot = game.observe(0, _scv(1, self._BUILD), _building_depot(2, 0.3))
        assert isinstance(scv, OwnUnit) and isinstance(depot, OwnUnit)
        assert (scv.construction, depot.builder) == (depot, scv)
        game.observe(16, _scv(1), _building_depot(2, 1.0))
        assert (scv.construction, depot.builder) == (None, None)
        assert not scv.is_dead

    def test_a_dead_scv_builds_nothing(self) -> None:
        """The structure it was building is halted, so it has no builder either."""
        game = _Game()
        scv, depot = game.observe(0, _scv(1, self._BUILD), _building_depot(2, 0.3))
        game.observe(16, _building_depot(2, 0.3), dead=(1,))
        assert isinstance(scv, OwnUnit) and isinstance(depot, OwnUnit)
        assert scv.is_dead
        assert (scv.construction, depot.builder) == (None, None)

    def test_a_halted_structure_has_no_builder(self) -> None:
        game = _Game()
        scv, depot = game.observe(0, _scv(1, self._BUILD), _building_depot(2, 0.3))
        game.observe(16, _scv(1), _building_depot(2, 0.3))
        assert isinstance(depot, OwnUnit)
        assert depot.builder is None
        assert not depot.is_complete

    def test_an_scv_resuming_a_structure_is_aimed_at_it_and_is_its_builder(self) -> None:
        resume = raw_pb2.UnitOrder(ability_id=AbilityId.SCV_BUILD_SUPPLY_DEPOT, target_unit_tag=2)
        game = _Game()
        game.observe(0, _building_depot(2, 0.3))
        scv, depot = game.observe(16, _scv(3, resume), _building_depot(2, 0.3))
        assert isinstance(scv, OwnUnit) and isinstance(depot, OwnUnit)
        assert (scv.construction, depot.builder) == (depot, scv)

    def test_an_scv_walking_to_build_is_building_nothing_yet(self) -> None:
        (scv,) = _Game().observe(0, _scv(1, self._BUILD))
        assert isinstance(scv, OwnUnit)
        assert scv.construction is None

    def test_an_scv_that_dies_while_building_leaves_the_structure_without_a_builder(self) -> None:
        game = _Game()
        _, depot = game.observe(0, _scv(1, self._BUILD), _building_depot(2, 0.3))
        game.observe(16, _building_depot(2, 0.3), dead=(1,))
        assert isinstance(depot, OwnUnit)
        assert depot.builder is None


class TestOwnUnits:
    @pytest.mark.parametrize(
        ("game_id", "check"),
        [
            (RawAbilityId.Move, "is_moving"),
            (RawAbilityId.Attack, "is_attacking"),
            (RawAbilityId.Scan_Move, "is_attacking"),
            (RawAbilityId.Harvest_Gather_SCV, "is_gathering"),
            (RawAbilityId.Harvest_Return_SCV, "is_returning"),
            (RawAbilityId.Effect_Repair_SCV, "is_repairing"),
        ],
        ids=lambda value: value.name if isinstance(value, RawAbilityId) else value,
    )
    def test_each_order_check_reads_its_current_order_alone(self, game_id: RawAbilityId, check: str) -> None:
        """The game's own id of each reads as the action's; an attack-move as an attack."""
        checks = ("is_moving", "is_attacking", "is_gathering", "is_returning", "is_repairing")
        scv = _one(make_unit(1, UnitTypeId.SCV, orders=[raw_pb2.UnitOrder(ability_id=game_id)]))
        assert isinstance(scv, OwnUnit)
        assert [getattr(scv, name) for name in checks] == [name == check for name in checks]

    def test_an_idle_unit_is_doing_none_of_the_order_checks(self) -> None:
        scv = _one(make_unit(1, UnitTypeId.SCV))
        assert isinstance(scv, OwnUnit)
        assert not (scv.is_moving or scv.is_attacking or scv.is_gathering or scv.is_returning or scv.is_repairing)

    def test_an_orders_known_target_is_none_where_the_game_reports_no_unit_under_its_tag(self) -> None:
        """`target` raises there, and reads None too for an order aimed at nothing."""
        aimed_at_a_tag = raw_pb2.UnitOrder(ability_id=RawAbilityId.Harvest_Gather_SCV, target_unit_tag=987)
        aimed_at_a_point = raw_pb2.UnitOrder(
            ability_id=AbilityId.MOVE, target_world_space_pos=common_pb2.Point(x=9.0, y=9.0)
        )
        aimed_at_nothing = raw_pb2.UnitOrder(ability_id=AbilityId.STOP)
        scv = _one(make_unit(1, UnitTypeId.SCV, orders=[aimed_at_a_tag, aimed_at_a_point, aimed_at_nothing]))
        assert isinstance(scv, OwnUnit)
        unreported, point, nothing = scv.orders
        with pytest.raises(NotReportedError):
            _ = unreported.target
        assert unreported.known_target is None
        assert point.known_target == point.target == Point((9.0, 9.0))
        assert nothing.known_target is nothing.target is None

    def test_a_players_own_unit_is_an_own_unit_and_anyone_elses_is_not(self) -> None:
        mine, theirs, neutral = _Game().observe(
            0, make_unit(1), make_unit(2, alliance=_ENEMY), make_unit(3, alliance=Alliance.NEUTRAL)
        )
        assert isinstance(mine, OwnUnit)
        assert type(theirs) is Unit
        assert type(neutral) is Unit
        assert not hasattr(theirs, "orders")

    def test_a_unit_changing_sides_changes_class_in_place(self) -> None:
        game = _Game()
        (marine,) = game.observe(0, make_unit(1, alliance=_ENEMY))
        game.observe(16, make_unit(1, alliance=Alliance.OWN))
        assert type(marine) is OwnUnit
        game.observe(32, make_unit(1, alliance=_ENEMY))
        assert type(marine) is Unit

    def test_an_order_names_its_ability_and_a_point_a_unit_or_nothing(self) -> None:
        orders = [
            raw_pb2.UnitOrder(ability_id=AbilityId.MOVE, target_world_space_pos=common_pb2.Point(x=5, y=6)),
            raw_pb2.UnitOrder(ability_id=AbilityId.ATTACK, target_unit_tag=9),
            raw_pb2.UnitOrder(ability_id=AbilityId.BARRACKS_TRAIN_MARINE, progress=0.5),
        ]
        game = _Game()
        marine, _ = game.observe(0, make_unit(1, orders=orders), make_unit(9, alliance=_ENEMY))
        assert isinstance(marine, OwnUnit)
        assert marine.orders == (
            UnitOrder(AbilityId.MOVE, Point((5.0, 6.0)), 0.0),
            UnitOrder(AbilityId.ATTACK, game.tracker.unit_tracker.present.by_id(400001), 0.0),
            UnitOrder(AbilityId.BARRACKS_TRAIN_MARINE, None, 0.5),
        )
        assert not marine.is_idle

    def test_an_order_aimed_by_a_tag_the_game_never_reports_raises_only_once_its_target_is_read(self) -> None:
        marine = _one(make_unit(1, orders=[raw_pb2.UnitOrder(ability_id=AbilityId.ATTACK, target_unit_tag=9)]))
        assert isinstance(marine, OwnUnit)
        (order,) = marine.orders
        assert order.ability is AbilityId.ATTACK
        with pytest.raises(NotReportedError, match="by no tag it reports"):
            _ = order.target

    def test_a_gather_aimed_by_a_tag_the_game_never_reports_is_aimed_at_the_field_its_last_gather_named(self) -> None:
        """An SCV walking back to a field out of sight shows its gather under a tag no observation reports, having
        returned its minerals in between (in game)."""
        field = make_unit(5, UnitTypeId.MINERAL_FIELD, alliance=Alliance.NEUTRAL)
        townhall = make_unit(7, UnitTypeId.COMMAND_CENTER)

        def scv(ability: RawAbilityId, target: int) -> raw_pb2.Unit:
            return make_unit(1, UnitTypeId.SCV, orders=[raw_pb2.UnitOrder(ability_id=ability, target_unit_tag=target)])

        game = _Game()
        worker, mined, _ = game.observe(0, scv(RawAbilityId.Harvest_Gather_SCV, 5), field, townhall)
        game.observe(16, scv(RawAbilityId.Harvest_Return_SCV, 7), field, townhall)
        game.observe(32, scv(RawAbilityId.Harvest_Gather_SCV, 99), field, townhall)

        assert isinstance(worker, OwnUnit)
        assert worker.orders[0].target is mined

    def test_a_gather_keeps_its_field_once_the_field_is_in_the_fog_under_another_tag(self) -> None:
        def scv(ability: RawAbilityId, target: int) -> raw_pb2.Unit:
            return make_unit(1, UnitTypeId.SCV, orders=[raw_pb2.UnitOrder(ability_id=ability, target_unit_tag=target)])

        def field(tag: int, visibility: Visibility) -> raw_pb2.Unit:
            neutral = Alliance.NEUTRAL
            return make_unit(tag, UnitTypeId.MINERAL_FIELD, at=(20.0, 20.0), alliance=neutral, visibility=visibility)

        game = _Game()
        worker, mined = game.observe(0, scv(RawAbilityId.Harvest_Gather_SCV, 5), field(5, Visibility.VISIBLE))
        game.observe(16, scv(RawAbilityId.Harvest_Gather_SCV, 5), field(5, Visibility.VISIBLE))
        game.observe(32, scv(RawAbilityId.Harvest_Gather_SCV, 5), field(50, Visibility.IN_FOG))
        game.observe(48, scv(RawAbilityId.Harvest_Return_SCV, 7), field(50, Visibility.IN_FOG))
        game.observe(64, scv(RawAbilityId.Harvest_Gather_SCV, 99), field(50, Visibility.IN_FOG))

        assert mined.tag == 50
        assert isinstance(worker, OwnUnit)
        assert worker.orders[0].target is mined

    def test_a_gather_aimed_by_a_tag_the_game_never_reports_reads_as_its_field_dead_since(self) -> None:
        """All of a main's fields killed in the fog, its SCVs still walked back to them and went idle there (in
        game)."""

        def scv(ability: RawAbilityId, target: int) -> raw_pb2.Unit:
            return make_unit(1, UnitTypeId.SCV, orders=[raw_pb2.UnitOrder(ability_id=ability, target_unit_tag=target)])

        field = make_unit(5, UnitTypeId.MINERAL_FIELD, alliance=Alliance.NEUTRAL)
        game = _Game()
        worker, mined = game.observe(0, scv(RawAbilityId.Harvest_Gather_SCV, 5), field)
        game.observe(16, scv(RawAbilityId.Harvest_Return_SCV, 7))
        game.observe(32, scv(RawAbilityId.Harvest_Gather_SCV, 99), dead=(5,))

        assert mined.is_dead
        assert isinstance(worker, OwnUnit)
        assert worker.orders[0].target is mined

    def test_a_rally_names_the_unit_it_is_onto_or_else_the_point(self) -> None:
        """A rally onto a mineral field that is mined out is left holding tag 2**32 (corpus)."""
        rallies = [
            raw_pb2.RallyTarget(point=common_pb2.Point(x=1, y=2)),
            raw_pb2.RallyTarget(point=common_pb2.Point(x=3, y=4), tag=5),
            raw_pb2.RallyTarget(point=common_pb2.Point(x=3, y=4), tag=1 << 32),
        ]
        center, field = _Game().observe(0, make_unit(1, rally_targets=rallies), make_unit(5, alliance=Alliance.NEUTRAL))
        assert isinstance(center, OwnUnit)
        assert center.rally_targets == (
            RallyTarget(Point((1.0, 2.0))),
            RallyTarget(field),
            RallyTarget(Point((3.0, 4.0))),
        )

    def test_a_rally_onto_a_tag_no_unit_was_reported_under_raises(self) -> None:
        """Rallied onto a field in the fog, or onto one that goes into the fog, a rally holds 2**32 (in game), never an
        unreported tag."""
        rally = raw_pb2.RallyTarget(point=common_pb2.Point(x=3, y=4), tag=99)
        center = _one(make_unit(1, rally_targets=[rally]))
        assert isinstance(center, OwnUnit)
        with pytest.raises(UnknownTagError, match="never reported a unit under tag 99"):
            _ = center.rally_targets

    def test_passengers_an_add_on_and_a_target_are_the_units_themselves(self) -> None:
        """A passenger is no longer in the observation, so it is the stale unit that boarded."""
        passenger = raw_pb2.PassengerUnit(tag=3, unit_type=UnitTypeId.MARINE, health=45, health_max=45)
        game = _Game()
        marine, lab, enemy = game.observe(
            0, make_unit(3), make_unit(4, UnitTypeId.TECH_LAB_BARRACKS), make_unit(6, alliance=_ENEMY)
        )
        loaded = make_unit(1, passengers=[passenger], cargo_space_taken=1, add_on_tag=4, engaged_target_tag=6)
        holder, _, _ = game.observe(
            16, loaded, make_unit(4, UnitTypeId.TECH_LAB_BARRACKS), make_unit(6, alliance=_ENEMY)
        )
        assert isinstance(holder, OwnUnit)
        assert holder.passengers == (Passenger(marine, UnitTypeId.MARINE, 45.0, 45.0, 0.0, 0.0, 0.0, 0.0),)
        assert marine.is_stale
        assert (holder.cargo_used, holder.cargo_max, holder.add_on, holder.engaged_target) == (
            1,
            0,
            lab,
            enemy,
        )

    def test_a_unit_named_in_the_step_it_dies_is_the_dead_unit(self) -> None:
        game = _Game()
        (enemy,) = game.observe(0, make_unit(9, alliance=_ENEMY))
        (marine,) = game.observe(16, make_unit(1, engaged_target_tag=9), dead=(9,))
        assert isinstance(marine, OwnUnit)
        assert marine.engaged_target is enemy
        assert enemy.is_dead

    def test_a_target_gone_out_of_the_observation_is_the_stale_unit(self) -> None:
        """As a roach that burrowed out of sight was, still named by the zealot attacking it (in game)."""
        game = _Game()
        _, roach = game.observe(0, make_unit(1, UnitTypeId.ZEALOT), make_unit(9, UnitTypeId.ROACH, alliance=_ENEMY))
        (zealot,) = game.observe(16, make_unit(1, UnitTypeId.ZEALOT, engaged_target_tag=9))
        assert isinstance(zealot, OwnUnit)
        assert zealot.engaged_target is roach
        assert roach.is_stale and not roach.is_dead

    def test_a_target_no_observation_listed_names_no_unit(self) -> None:
        """As a stalker's target created and killed within a step was (in game)."""
        game = _Game()
        (stalker,) = game.observe(
            0, make_unit(1, UnitTypeId.STALKER, engaged_target_tag=4485021725), dead=(4485021725,)
        )
        assert isinstance(stalker, OwnUnit)
        assert stalker.engaged_target is None

    def test_a_tag_the_game_leaves_at_zero_names_no_unit(self) -> None:
        marine = _one(make_unit(1))
        assert isinstance(marine, OwnUnit)
        assert (marine.add_on, marine.engaged_target) == (None, None)

    def test_the_weapon_cooldown_is_in_steps(self) -> None:
        marine = _one(make_unit(1, weapon_cooldown=11.0))
        assert isinstance(marine, OwnUnit)
        assert marine.weapon_cooldown.steps == 11.0

    def test_a_timed_life_is_read_as_the_game_reports_it_above_its_maximum_too(self) -> None:
        """A MULE's first listing read 1441 steps left against a maximum of 1440 (tool `sweep_timed_life`)."""
        mule = _one(make_unit(1, UnitTypeId.MULE, buff_duration_remain=1441, buff_duration_max=1440))
        assert isinstance(mule, OwnUnit)
        assert mule.buff_duration_remaining == GameTime(steps=1441)
        assert mule.buff_duration_max == GameTime(steps=1440)

    def test_a_timed_life_run_out_is_zero_left(self) -> None:
        mule = _one(make_unit(1, UnitTypeId.MULE, buff_duration_remain=0, buff_duration_max=1440))
        assert isinstance(mule, OwnUnit)
        assert mule.buff_duration_remaining == GameTime.ZERO

    def test_a_unit_with_nothing_timed_has_no_buff_duration(self) -> None:
        scv = _one(make_unit(1, UnitTypeId.SCV))
        assert isinstance(scv, OwnUnit)
        assert (scv.buff_duration_remaining, scv.buff_duration_max) == (None, None)

    def test_a_bot_can_key_its_own_data_by_unit(self) -> None:
        marine = _one(make_unit(1))
        notes = weakref.WeakKeyDictionary({marine: "stimmed at 120"})
        assert notes[marine] == "stimmed at 120"


class TestVelocity:
    def test_a_unit_first_observed_has_no_velocity(self) -> None:
        assert _one(make_unit(1)).velocity is None

    def test_velocity_is_the_distance_moved_per_second_between_its_last_two_observations(self) -> None:
        game = _Game()
        (marine,) = game.observe(0, make_unit(1, at=(10.0, 10.0)))
        game.observe(16, make_unit(1, at=(12.0, 9.0)))
        assert marine.velocity == pytest.approx(Point((2.0, -1.0)) * (STEPS_PER_SECOND / 16))

    def test_velocity_divides_by_the_steps_that_really_passed(self) -> None:
        """A realtime game skips steps, so turns are not all the same length."""
        game = _Game()
        (marine,) = game.observe(0, make_unit(1, at=(10.0, 10.0)))
        game.observe(16, make_unit(1, at=(11.0, 10.0)))
        game.observe(40, make_unit(1, at=(14.0, 10.0)))
        assert marine.velocity == pytest.approx(Point((3.0 * STEPS_PER_SECOND / 24, 0.0)))

    def test_observing_the_same_step_again_keeps_the_velocity(self) -> None:
        game = _Game()
        (marine,) = game.observe(0, make_unit(1, at=(10.0, 10.0)))
        game.observe(16, make_unit(1, at=(12.0, 10.0)))
        game.observe(16, make_unit(1, at=(12.0, 10.0)))
        assert marine.velocity == pytest.approx(Point((2.0 * STEPS_PER_SECOND / 16, 0.0)))

    def test_a_unit_that_comes_back_starts_its_velocity_again(self) -> None:
        game = _Game()
        (zergling,) = game.observe(0, make_unit(1, alliance=_ENEMY, at=(10.0, 10.0)))
        game.observe(16, make_unit(1, alliance=_ENEMY, at=(12.0, 10.0)))
        game.observe(32)
        assert zergling.velocity is not None
        game.observe(480, make_unit(1, alliance=_ENEMY, at=(40.0, 10.0)))
        assert zergling.velocity is None


class TestWhatChanged:
    def test_this_players_units_first_seen_are_created_the_first_observations_included(self) -> None:
        game = _Game()
        marine, _, _ = game.observe(
            0,
            make_unit(1),
            make_unit(2, alliance=_ENEMY),
            make_unit(3, UnitTypeId.MINERAL_FIELD, alliance=Alliance.NEUTRAL),
        )
        assert game.tracker.last_changes.own_units_created == [marine]
        (_, scv) = game.observe(16, make_unit(1), make_unit(4, UnitTypeId.SCV))
        assert game.tracker.last_changes.own_units_created == [scv]
        game.observe(32, make_unit(1), make_unit(4, UnitTypeId.SCV))
        assert not game.tracker.last_changes.own_units_created

    def test_an_enemy_unit_is_first_seen_once_in_sight_or_in_the_fog(self) -> None:
        game = _Game()
        (zergling,) = game.observe(0, make_unit(1, alliance=_ENEMY))
        assert game.tracker.last_changes.enemy_units_first_seen == [zergling]
        game.observe(16)
        game.observe(32, make_unit(1, alliance=_ENEMY))
        assert not game.tracker.last_changes.enemy_units_first_seen
        (_, remembered) = game.observe(48, make_unit(1, alliance=_ENEMY), _depot(900, visibility=_IN_FOG))
        assert game.tracker.last_changes.enemy_units_first_seen == [remembered]
        assert not game.tracker.last_changes.enemy_units_entered_sight

    def test_a_type_change_is_noted_once_with_the_type_it_was_and_never_on_creation(self) -> None:
        game = _Game()
        (tank,) = game.observe(0, make_unit(1, UnitTypeId.SIEGE_TANK, alliance=_ENEMY))
        assert not game.tracker.last_changes.units_type_changed
        game.observe(16, make_unit(1, UnitTypeId.SIEGE_TANK_SIEGED, alliance=_ENEMY))
        assert game.tracker.last_changes.units_type_changed == [(tank, UnitTypeId.SIEGE_TANK)]
        game.observe(32, make_unit(1, UnitTypeId.SIEGE_TANK_SIEGED, alliance=_ENEMY))
        assert not game.tracker.last_changes.units_type_changed

    def test_a_side_change_is_noted_with_the_alliance_it_left(self) -> None:
        game = _Game()
        (marine,) = game.observe(0, make_unit(1, alliance=_ENEMY))
        game.observe(16, make_unit(1, alliance=Alliance.OWN))
        assert game.tracker.last_changes.units_alliance_changed == [(marine, _ENEMY)]
        game.observe(32, make_unit(1, alliance=_ENEMY))
        assert game.tracker.last_changes.units_alliance_changed == [(marine, Alliance.OWN)]

    def test_a_report_changing_both_side_and_type_notes_each(self) -> None:
        game = _Game()
        (tank,) = game.observe(0, make_unit(1, UnitTypeId.SIEGE_TANK, alliance=_ENEMY))
        game.observe(16, make_unit(1, UnitTypeId.SIEGE_TANK_SIEGED, alliance=Alliance.OWN))
        assert game.tracker.last_changes.units_alliance_changed == [(tank, _ENEMY)]
        assert game.tracker.last_changes.units_type_changed == [(tank, UnitTypeId.SIEGE_TANK)]
        assert isinstance(tank, OwnUnit)

    def test_a_unit_first_seen_unfinished_finishes_once(self) -> None:
        game = _Game()
        barracks, zealot, marine = game.observe(
            0,
            make_unit(1, UnitTypeId.BARRACKS, build_progress=0.5),
            make_unit(2, UnitTypeId.ZEALOT, build_progress=0.25),
            make_unit(3, build_progress=1.0),
        )
        assert not game.tracker.last_changes.own_units_finished
        game.observe(
            16,
            make_unit(1, UnitTypeId.BARRACKS, build_progress=1.0),
            make_unit(2, UnitTypeId.ZEALOT, build_progress=1.0),
            make_unit(3, build_progress=1.0),
        )
        assert game.tracker.last_changes.own_units_finished == [barracks, zealot]
        assert marine not in game.tracker.last_changes.own_units_finished
        game.observe(32, make_unit(1, UnitTypeId.BARRACKS, build_progress=1.0))
        assert not game.tracker.last_changes.own_units_finished

    def test_a_unit_dying_unfinished_never_finishes(self) -> None:
        game = _Game()
        game.observe(0, _building_depot(1, 0.25))
        game.observe(16, dead=(1,))
        assert not game.tracker.last_changes.own_units_finished
        assert not game.tracker.unit_tracker._unfinished

    def test_upgrades_new_to_the_observation_come_in_the_order_of_their_ids(self) -> None:
        tracker = _Tracker(_TABLES, make_enemy())
        held = [UpgradeId.TERRAN_INFANTRY_WEAPONS_1, UpgradeId.STIMPACK]
        tracker.update(make_observation(0, upgrades=held).observation.raw_data, 0)
        assert tracker.last_changes.own_upgrades_finished == sorted(held)
        tracker.update(make_observation(16, upgrades=held).observation.raw_data, 16)
        assert not tracker.last_changes.own_upgrades_finished
        tracker.update(make_observation(32, upgrades=[*held, UpgradeId.COMBAT_SHIELD]).observation.raw_data, 32)
        assert tracker.last_changes.own_upgrades_finished == [UpgradeId.COMBAT_SHIELD]

    def test_a_structure_enters_and_leaves_sight_through_the_fog_both_ways(self) -> None:
        game = _Game()
        (depot,) = game.observe(0, _depot(1))
        assert game.tracker.last_changes.enemy_units_entered_sight == [depot]
        game.observe(16, _depot(900, visibility=_IN_FOG))
        assert (
            game.tracker.last_changes.enemy_units_entered_sight,
            game.tracker.last_changes.enemy_units_left_sight,
        ) == ([], [depot])
        game.observe(32, _depot(1))
        assert (
            game.tracker.last_changes.enemy_units_entered_sight,
            game.tracker.last_changes.enemy_units_left_sight,
        ) == ([depot], [])

    def test_a_unit_leaves_sight_as_it_leaves_the_observation_and_enters_it_coming_back(self) -> None:
        game = _Game()
        (zergling,) = game.observe(0, make_unit(1, alliance=_ENEMY))
        game.observe(16)
        assert game.tracker.last_changes.enemy_units_left_sight == [zergling]
        game.observe(32, make_unit(1, alliance=_ENEMY))
        assert game.tracker.last_changes.enemy_units_entered_sight == [zergling]

    def test_a_structure_seen_where_it_moved_to_enters_sight(self) -> None:
        game = _Game()
        (barracks,) = game.observe(0, make_unit(1, UnitTypeId.BARRACKS, at=(20.5, 20.5), alliance=_ENEMY))
        copy = make_unit(900, UnitTypeId.BARRACKS, at=(20.5, 20.5), alliance=_ENEMY, visibility=_IN_FOG)
        game.observe(16, copy)
        game.observe(32, copy, make_unit(1, UnitTypeId.BARRACKS, at=(60.5, 30.5), alliance=_ENEMY))
        assert game.tracker.last_changes.enemy_units_entered_sight == [barracks]

    def test_a_unit_cloaking_where_it_stands_stays_in_sight(self) -> None:
        game = _Game()
        game.observe(0, make_unit(1, alliance=_ENEMY))
        game.observe(16, make_unit(1, alliance=_ENEMY, visibility=_INVISIBLE))
        assert not game.tracker.last_changes.enemy_units_entered_sight
        assert not game.tracker.last_changes.enemy_units_left_sight

    def test_a_unit_that_dies_does_not_leave_sight(self) -> None:
        game = _Game()
        (zergling,) = game.observe(0, make_unit(1, alliance=_ENEMY))
        game.observe(16, dead=(1,))
        assert not game.tracker.last_changes.enemy_units_left_sight
        assert game.tracker.last_changes.units_died == [zergling]

    def test_this_players_units_and_neutral_ones_never_enter_or_leave_sight(self) -> None:
        game = _Game()
        field = make_unit(2, UnitTypeId.MINERAL_FIELD, alliance=Alliance.NEUTRAL)
        game.observe(0, make_unit(1), field)
        assert not game.tracker.last_changes.enemy_units_entered_sight
        game.observe(16)
        assert not game.tracker.last_changes.enemy_units_left_sight

    def test_ending_the_game_forgets_what_changed(self) -> None:
        game = _Game()
        game.observe(0, make_unit(1, build_progress=0.5), make_unit(2, alliance=_ENEMY))
        game.tracker.end()
        changes = game.tracker.last_changes
        assert not any(getattr(changes, name) for name in changes.__slots__ if name not in {"update", "own", "enemy"})
        assert not any(getattr(side, name) for side in (changes.own, changes.enemy) for name in side.__slots__)
        assert not game.tracker.unit_tracker._unfinished
        assert not game.tracker.unit_comparer._compared.value


class TestThroughTheApi:
    @staticmethod
    def _play(api: Api, *observations: sc2api_pb2.ResponseObservation) -> None:
        responses = [make_response(game_info=make_game_info()), make_response(data=sc2api_pb2.ResponseData())]
        for index, observation in enumerate(observations):
            final = index == len(observations) - 1
            responses.append(make_response(Status.ENDED if final else Status.IN_GAME, observation=observation))
            if not final:
                responses.append(make_response(step=sc2api_pb2.ResponseStep()))
        transport = FakeTransport(make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=1)), *responses)
        client = Client(transport)
        client.join_game(Race.TERRAN)
        api.play(client)

    def test_the_units_are_those_of_the_last_observation_and_the_known_units_those_not_dead(self) -> None:
        api = Api()
        first = [make_unit(1), make_unit(2, alliance=_ENEMY), make_unit(3, alliance=_ENEMY)]
        self._play(
            api, make_observation(0, units=first), make_observation(1, (1, Result.VICTORY), units=first[:1], dead=(3,))
        )
        assert api.units.ids == {100001}
        assert api.known_units.ids == {100001, 400001}

    def test_the_enemy_holds_what_it_is_assumed_to_have_and_is_new_for_each_game(self) -> None:
        api = Api()
        self._play(api, make_observation(0, (1, Result.VICTORY)))
        enemy = api.enemy
        assert not enemy.upgrades
        enemy.assume_upgrades(UpgradeId.STIMPACK)
        enemy.assume_upgrades(UpgradeId.COMBAT_SHIELD)
        enemy.forget_upgrades(UpgradeId.STIMPACK)
        assert enemy.upgrades == {UpgradeId.COMBAT_SHIELD}
        assert repr(enemy) == "Enemy(id=2, race=ZERG, requested_race=ZERG, name=None, ladder_id=None)"
        self._play(api, make_observation(0, (1, Result.VICTORY)))
        assert api.enemy is not enemy
        assert not api.enemy.upgrades

    def test_the_last_games_units_are_stale_once_the_next_game_starts(self) -> None:
        api = Api()
        self._play(api, make_observation(0, (1, Result.VICTORY), units=[make_unit(1)]))
        marine = api.units.by_id(100001)
        assert not marine.is_stale
        self._play(api, make_observation(0, (1, Result.VICTORY), units=[make_unit(1)]))
        assert marine.is_stale
        assert api.units.by_id(100001) is not marine


@pytest.mark.integration
def test_in_a_real_game_a_unit_keeps_its_object_and_id_through_everything_but_death() -> None:
    """Run with `pytest -m integration`. Plays a minute of a game."""
    try:
        game_map = MapFile.find("PylonAIE_v4")
    except MapNotFoundError as missing:
        pytest.skip(str(missing))

    with (
        GameProcess.launch(window=(640, 480)) as process,
        closing(Client(WebSocketTransport.connect(process.url))) as client,
    ):
        client.create_game(game_map.path, [Participant(), Computer(Race.ZERG, Difficulty.VERY_EASY)])
        game = RealGame(client, client.join_game(Race.TERRAN))
        enemy = 3 - game.player
        units = game.turn(1)
        home = units.own.of_type(UnitTypeId.COMMAND_CENTER)[0].position
        middle = game.map.playable_area.center
        out_there = home.towards(middle, 12)
        # Not the `tech_tree` cheat: it grants campaign upgrades no curated id names, and an observation holding one
        # raises (`docs/reference/game-behavior/cheats.md`). This test researches what it needs. Not `fast_build`
        # either: a structure under construction is one of the things read below.
        game.debug(debug_pb2.DebugCommand(game_state=debug_pb2.DebugGameState.free))

        # A morph keeps the object.
        game.debug(game.create(UnitTypeId.SIEGE_TANK, out_there))
        game.turn(2)
        tank = game.newest(UnitTypeId.SIEGE_TANK)
        game.order(RawAbilityId.SiegeMode_SiegeMode, tank)
        game.turn(90)
        assert tank.type_id is UnitTypeId.SIEGE_TANK_SIEGED
        assert game.tracker.unit_tracker.present.get(tank.id) is tank

        # A unit loaded into a transport is stale, and the same object once unloaded.
        game.debug(
            game.create(UnitTypeId.MARINE, out_there + (3, 0)), game.create(UnitTypeId.MEDIVAC, out_there + (3, 1))
        )
        game.turn(2)
        marine, medivac = game.newest(UnitTypeId.MARINE), game.newest(UnitTypeId.MEDIVAC)
        game.order(AbilityId.LOAD, medivac, target=marine)
        game.turn(40)
        assert marine.is_stale
        assert isinstance(medivac, OwnUnit)
        assert marine in {passenger.unit for passenger in medivac.passengers}
        game.order(AbilityId.UNLOAD_AT, medivac, target=medivac.position)
        game.turn(60)
        assert not marine.is_stale
        assert game.tracker.unit_tracker.present.get(marine.id) is marine
        # Moved out of the way, since both would shoot the enemy units created below.
        game.debug(game.kill(tank, marine))

        # A mineral field remembered from the start keeps its id when first seen.
        field = min(
            game.tracker.unit_tracker.present.neutral.filter(lambda unit: unit.visibility is Visibility.IN_FOG),
            key=lambda unit: abs(unit.position.distance_to(home) - 40),
        )
        spot = game.open_ground(field.position.towards(middle, 7))
        game.debug(
            game.create(UnitTypeId.MARINE, spot + (2, 0)), game.create(UnitTypeId.SUPPLY_DEPOT, spot, owner=enemy)
        )
        game.turn(4)
        assert field.visibility is Visibility.VISIBLE
        assert game.tracker.unit_tracker.present.get(field.id) is field
        spotter, depot = game.newest(UnitTypeId.MARINE), game.newest(UnitTypeId.SUPPLY_DEPOT)

        # A structure out of sight is remembered under its id, and back in sight under it again.
        health = depot.health
        game.debug(game.kill(spotter))
        game.turn(60)
        assert depot.visibility is Visibility.IN_FOG
        assert game.tracker.unit_tracker.present.get(depot.id) is depot
        assert depot.health == health
        game.debug(game.create(UnitTypeId.MARINE, spot + (2, 0)))
        game.turn(4)
        assert depot.visibility is Visibility.VISIBLE
        assert game.tracker.unit_tracker.present.get(depot.id) is depot

        # A structure that dies out of sight is found dead once its spot is in sight again.
        depot_tag = depot.tag
        game.debug(game.kill(game.newest(UnitTypeId.MARINE)))
        game.turn(90)
        assert depot.visibility is Visibility.IN_FOG
        game.debug(debug_pb2.DebugCommand(kill_unit=debug_pb2.DebugKillUnit(tag=[depot_tag])))
        game.turn(30)
        assert not depot.is_dead
        game.debug(game.create(UnitTypeId.MARINE, spot + (2, 0)))
        game.turn(4)
        assert depot.is_dead
        assert game.tracker.unit_tracker.known.get(depot.id) is None

        # A neural parasite takes a unit over and lets it go, changing its class both times.
        game.debug(game.create(UnitTypeId.INFESTATION_PIT, game.open_ground(out_there.towards(home, -8))))
        game.turn(2)
        game.order(AbilityId.INFESTATION_PIT_RESEARCH_NEURAL_PARASITE, game.newest(UnitTypeId.INFESTATION_PIT))
        for _ in range(100):
            if UpgradeId.NEURAL_PARASITE in game.state.upgrades:
                break
            game.turn(22)
        assert UpgradeId.NEURAL_PARASITE in game.state.upgrades
        game.debug(
            game.create(UnitTypeId.INFESTOR, out_there + (0, 4)),
            game.create(UnitTypeId.MARINE, out_there + (4, 4), owner=enemy),
        )
        game.turn(2)
        infestor = game.newest(UnitTypeId.INFESTOR)
        victim = game.tracker.unit_tracker.present.enemy.of_type(UnitTypeId.MARINE)[-1]
        energy = debug_pb2.DebugSetUnitValue(
            unit_value=debug_pb2.DebugSetUnitValue.Energy, value=200, unit_tag=infestor.tag
        )
        game.debug(debug_pb2.DebugCommand(unit_value=energy))
        game.turn(2)
        game.order(RawAbilityId.NeuralParasite_NeuralParasite, infestor, target=victim)
        game.turn(24)
        assert type(victim) is OwnUnit
        assert victim.id // 100_000 == Alliance.ENEMY
        game.debug(game.kill(infestor))
        game.turn(8)
        assert type(victim) is Unit

        # A unit lifted by a phoenix flies, though the game reports it as not flying.
        game.debug(
            game.create(UnitTypeId.PHOENIX, out_there + (0, -4)),
            game.create(UnitTypeId.QUEEN, out_there + (3, -4), owner=enemy),
        )
        game.turn(2)
        phoenix, queen = game.newest(UnitTypeId.PHOENIX), game.newest(UnitTypeId.QUEEN)
        energy = debug_pb2.DebugSetUnitValue(
            unit_value=debug_pb2.DebugSetUnitValue.Energy, value=200, unit_tag=phoenix.tag
        )
        game.debug(debug_pb2.DebugCommand(unit_value=energy))
        game.turn(2)
        game.order(AbilityId.PHOENIX_GRAVITON_BEAM, phoenix, target=queen)
        game.turn(24)
        assert BuffId.PHOENIX_GRAVITON_BEAM in queen.buffs
        assert not queen._latest_report.is_flying
        assert queen.is_flying
        game.debug(game.kill(phoenix, queen))

        # An SCV building a structure is its builder until the structure finishes.
        game.debug(debug_pb2.DebugCommand(game_state=debug_pb2.DebugGameState.minerals))
        scv = game.tracker.unit_tracker.present.own.of_type(UnitTypeId.SCV)[0]
        game.order(AbilityId.SCV_BUILD_SUPPLY_DEPOT, scv, target=game.open_ground(home.towards(middle, 8)))
        game.turn(120)
        (building,) = game.tracker.unit_tracker.present.own.of_type(UnitTypeId.SUPPLY_DEPOT)
        assert not building.is_complete
        assert (scv.construction, building.builder) == (building, scv)
        game.turn(600)
        assert building.is_complete
        assert (scv.construction, building.builder) == (None, None)

        # Only death lets go of a unit.
        game.debug(game.kill(medivac))
        game.turn(2)
        assert medivac.is_dead
        assert game.tracker.unit_tracker.known.get(medivac.id) is None
        client.leave_game()
        client.quit()


# The most steps waited for a dead marine's sight to lapse: it lingers 65 (tool `sweep_fog`).
_MOST_STEPS_SIGHT_LINGERS = 224
# The most steps waited for a marine made by a debug command to be listed: it takes 2 (tool `sweep_fog`).
_MOST_STEPS_TO_MAKE_THE_WATCHER = 16


@pytest.mark.integration
def test_in_a_real_game_a_structure_seen_and_lost_again_between_two_observations_stays_one_unit() -> None:
    """Run with `pytest -m integration`. An enemy bay seen and lost into the fog, then seen and lost again between
    two observations, as in tool `sweep_fog`'s `retag`, is the same unit, neither found dead nor first seen."""
    try:
        game_map = MapFile.find("PylonAIE_v4")
    except MapNotFoundError as missing:
        pytest.skip(str(missing))

    with (
        GameProcess.launch(window=(640, 480)) as process,
        closing(Client(WebSocketTransport.connect(process.url))) as client,
    ):
        client.create_game(game_map.path, [Participant(), Computer(Race.ZERG, Difficulty.VERY_EASY)])
        game = RealGame(client, client.join_game(Race.TERRAN))
        spot = game.open_ground(game.map.playable_area.center, size=3)
        watching_point = spot + (3, 0)
        game.debug(
            game.create(UnitTypeId.ENGINEERING_BAY, spot, owner=3 - game.player),
            game.create(UnitTypeId.MARINE, watching_point),
        )
        game.turn(8)
        bay = game.newest(UnitTypeId.ENGINEERING_BAY)
        game.debug(game.kill(game.newest(UnitTypeId.MARINE)))
        for _ in range(_MOST_STEPS_SIGHT_LINGERS):
            game.turn(1)
            if bay.visibility is Visibility.IN_FOG:
                break
        remembered_under = bay.tag
        assert bay.visibility is Visibility.IN_FOG

        # Between two observations of the tracker's: a marine shows the bay and is killed as soon as it is listed.
        game.debug(game.create(UnitTypeId.MARINE, watching_point))
        watcher_tags: list[int] = []
        for _ in range(_MOST_STEPS_TO_MAKE_THE_WATCHER):
            client.step(1)
            watcher_tags = [
                unit.tag
                for unit in client.observation().observation.raw_data.units
                if unit.owner == game.player and unit.unit_type == UnitTypeId.MARINE
            ]
            if watcher_tags:
                break
        assert watcher_tags, f"no marine was listed {_MOST_STEPS_TO_MAKE_THE_WATCHER} steps after it was made"
        game.debug(debug_pb2.DebugCommand(kill_unit=debug_pb2.DebugKillUnit(tag=watcher_tags)))
        game.turn(_MOST_STEPS_SIGHT_LINGERS)

        changes = game.tracker.last_changes
        assert game.tracker.unit_tracker.present.get(bay.id) is bay
        assert bay.visibility is Visibility.IN_FOG
        assert bay.tag != remembered_under
        assert not (changes.units_found_dead or changes.enemy_units_first_seen or changes.enemy_units_left_sight)
