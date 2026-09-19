"""A game's units, read from observations written here: ids, the fog, staleness, death, and what each read answers."""

import weakref
from contextlib import closing
from typing import Any

import pytest
from s2clientprotocol import common_pb2, data_pb2, debug_pb2, raw_pb2, sc2api_pb2

from sc2nachos import Api, NachOSError
from sc2nachos.constants import STEPS_PER_SECOND
from sc2nachos.enemy import Enemy
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, BuffId, UncuratedIdError, UnitTypeId, UpgradeId
from sc2nachos.ids.raw import RawAbilityId, RawBuffId, RawUnitTypeId
from sc2nachos.launch import GameProcess, Map, MapNotFoundError
from sc2nachos.match import Computer, Difficulty, Participant, Race, Result
from sc2nachos.protocol import Client, Status, WebSocketTransport
from sc2nachos.units import (
    Alliance,
    CloakState,
    NotReportedError,
    Order,
    OwnUnit,
    Passenger,
    RallyTarget,
    Unit,
    UnknownTagError,
    Visibility,
)
from sc2nachos.units._tracking import _Tracker
from sc2nachos.units._tracking._unit_tracker import _MOVABLE_UNIT_TYPE_IDS
from support import FakeTransport, RealGame, make_game_info, make_observation, make_response, make_tables, make_unit

_TABLES = make_tables(
    data_pb2.UnitTypeData(unit_id=UnitTypeId.BARRACKS, attributes=[data_pb2.Attribute.Structure]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.MARINE, attributes=[data_pb2.Attribute.Biological]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.SPINE_CRAWLER, ability_id=AbilityId.DRONE_MORPH_SPINE_CRAWLER),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.EXTRACTOR, ability_id=AbilityId.DRONE_MORPH_EXTRACTOR),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.SUPPLY_DEPOT, ability_id=AbilityId.SCV_BUILD_SUPPLY_DEPOT),
)
_ENEMY = Alliance.ENEMY
_IN_FOG = Visibility.IN_FOG
_INVISIBLE = Visibility.INVISIBLE


class _Game:
    """A tracker fed one observation after another, as `Api.play` feeds it."""

    def __init__(self) -> None:
        self.tracker = _Tracker(_TABLES, Enemy())

    def observe(self, step: int, *units: raw_pb2.Unit, dead: tuple[int, ...] = ()) -> list[Unit[Any]]:
        """Observe `units` at `step`, and answer the unit each one listed was read into, in the order given."""
        self.tracker.update(make_observation(step, units=units, dead=dead).observation.raw_data, step)
        by_tag = {unit.tag: unit for unit in self.tracker.units.present}
        return [by_tag[unit.tag] for unit in units if unit.tag in by_tag]


def _one(proto: raw_pb2.Unit) -> Unit[Any]:
    """`proto` observed once."""
    return _Game().observe(0, proto)[0]


def _depot(tag: int, *, visibility: Visibility = Visibility.IN_VISION, **fields: Any) -> raw_pb2.Unit:
    """An enemy supply depot at one spot, as the game reports it in sight or remembered."""
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

    def test_running_out_of_ids_raises(self) -> None:
        game = _Game()
        game.tracker.units._units_seen_per_alliance[Alliance.OWN] = 99_998
        game.observe(0, make_unit(1))
        with pytest.raises(NachOSError, match="all its ids"):
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
        assert (zergling.position, zergling.health, zergling.last_seen) == (Point((5.0, 6.0)), 35.0, 0)
        assert not game.tracker.units.present
        assert list(game.tracker.units.known) == [zergling]

    def test_a_stale_unit_that_comes_back_is_the_same_object(self) -> None:
        game = _Game()
        (zergling,) = game.observe(0, make_unit(1, alliance=_ENEMY))
        game.observe(16)
        (back,) = game.observe(32, make_unit(1, alliance=_ENEMY, at=(20.0, 20.0)))
        assert back is zergling
        assert not zergling.is_stale
        assert zergling.position == Point((20.0, 20.0))
        assert list(game.tracker.units.known) == [zergling]

    def test_a_unit_the_game_reports_dead_is_dead_and_let_go(self) -> None:
        game = _Game()
        (marine,) = game.observe(0, make_unit(1, health=45.0))
        game.observe(16, dead=(1,))
        assert marine.is_dead
        assert marine.is_stale
        assert marine.health == 45.0
        assert not game.tracker.units.known
        assert repr(marine) == "OwnUnit(MARINE, id=100001, at (10.00, 10.00), dead)"

    def test_the_dead_units_are_those_the_last_observation_reported_dead(self) -> None:
        """The game also reports deaths under tags it never reported a unit under (corpus), which name no unit."""
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
        assert [unit.tag for unit in game.tracker.units.present] == [1]

    def test_units_are_listed_in_the_order_the_game_reported_them_new_ones_included(self) -> None:
        game = _Game()
        game.observe(0, make_unit(3), make_unit(1))
        game.observe(16, make_unit(2), make_unit(3), make_unit(4), make_unit(1))
        assert [unit.tag for unit in game.tracker.units.present] == [2, 3, 4, 1]

    def test_ending_the_game_leaves_every_unit_stale(self) -> None:
        game = _Game()
        (marine,) = game.observe(0, make_unit(1))
        game.tracker.end()
        assert marine.is_stale
        assert not game.tracker.units.present


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
        assert depot.last_seen == 0
        (seen,) = game.observe(32, _depot(1, health=300.0))
        assert seen is depot
        assert (depot.tag, depot.visibility, depot.health, depot.last_seen) == (1, Visibility.IN_VISION, 300.0, 32)
        (again,) = game.observe(48, _depot(901, visibility=_IN_FOG))
        assert again is depot
        assert [unit.id for unit in game.tracker.units.known] == [400001]

    def test_a_mineral_field_never_seen_keeps_the_id_it_had_when_it_is_first_seen(self) -> None:
        field = {"at": (60.0, 20.5), "alliance": Alliance.NEUTRAL}
        game = _Game()
        (remembered,) = game.observe(0, make_unit(700, UnitTypeId.MINERAL_FIELD, visibility=_IN_FOG, **field))
        with pytest.raises(NotReportedError, match="never showed .* in sight, so it never reported its minerals"):
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

    def test_a_unit_arriving_where_another_left_is_not_it(self) -> None:
        """Only a swap between sight and memory is one unit; a visible unit replacing a visible one is not."""
        game = _Game()
        (first,) = game.observe(0, _depot(1))
        (second,) = game.observe(16, _depot(2))
        assert second is not first

    def test_a_structure_missing_from_its_spot_is_dead(self) -> None:
        """Tested in game: a depot burned down or killed out of sight is never reported dead. Its remembered copy
        goes once the spot is in sight again, with nothing in its place."""
        game = _Game()
        (depot,) = game.observe(0, _depot(1))
        game.observe(16, _depot(900, visibility=_IN_FOG))
        game.observe(32)
        assert depot.is_dead
        assert not game.tracker.units.known
        assert game.tracker.last_changes.units_found_dead == [depot]
        assert not game.tracker.last_changes.units_died

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
            UnitTypeId.VIKING,
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
            assert list(game.tracker.units.present) == [unit]
            assert (unit.position, unit.visibility, unit.health) == (Point((60.5, 30.5)), Visibility.IN_VISION, 900.0)
        game.observe(64, landed)
        assert list(game.tracker.units.known) == [unit]
        assert not unit.is_stale


class TestWhatAUnitReads:
    def test_where_and_how_it_is_seen_read_now_and_the_rest_as_last_seen(self) -> None:
        """A banshee seen, then cloaked out of detection, moves on while its health is what was last seen."""
        game = _Game()
        (banshee,) = game.observe(0, make_unit(1, alliance=_ENEMY, at=(5.0, 5.0), health=140.0, owner=2))
        game.observe(16, make_unit(1, alliance=_ENEMY, at=(9.0, 5.0), visibility=_INVISIBLE, cloak=raw_pb2.Cloaked))
        assert (banshee.position, banshee.visibility, banshee.cloak) == (
            Point((9.0, 5.0)),
            _INVISIBLE,
            CloakState.CLOAKED,
        )
        assert (banshee.health, banshee.owner_id, banshee.last_seen) == (140.0, 2, 0)

    def test_what_was_never_shown_in_sight_raises(self) -> None:
        mine = _one(make_unit(1, alliance=_ENEMY, visibility=_INVISIBLE, is_burrowed=True))
        assert mine.is_burrowed
        assert mine.last_seen is None
        for name in ("health", "owner_id", "build_progress", "buffs", "facing", "energy_fraction"):
            with pytest.raises(NotReportedError):
                getattr(mine, name)

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

    def test_an_uncurated_type_raises_as_the_observation_is_taken_in_and_names_the_id(self) -> None:
        """A type the game reports belongs among the curated ids, so one missing is a mistake to fix."""
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
    """This player's drone at one spot, carrying out `ability` aimed at `target`."""
    order = raw_pb2.UnitOrder(ability_id=ability, **target)
    return make_unit(tag, UnitTypeId.DRONE, at=(20.0, 20.0), orders=[order])


def _spine(tag: int, progress: float) -> raw_pb2.Unit:
    return make_unit(tag, UnitTypeId.SPINE_CRAWLER, at=(24.0, 20.0), build_progress=progress)


class TestUnitsThatBecomeStructures:
    """Tested in game: a drone that morphs into a structure leaves the observation with no death reported, the
    structure appearing under a new tag, is reported dead as the structure finishes or is killed, or a step later, and
    comes back under its own tag when the structure is cancelled."""

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
        assert drone.id not in game.tracker.units.known.ids
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

    def test_a_structure_far_from_where_the_drone_was_sent_is_not_what_it_became(self) -> None:
        game = _Game()
        (drone,) = game.observe(0, _drone_building(1, AbilityId.DRONE_MORPH_SPINE_CRAWLER, **self._AIMED))
        game.observe(16, make_unit(2, UnitTypeId.SPINE_CRAWLER, at=(40.0, 20.0), build_progress=1.0))
        assert drone.is_stale
        assert not drone.is_dead


def _scv(tag: int, *orders: raw_pb2.UnitOrder) -> raw_pb2.Unit:
    """One of our SCVs beside the depot `_building_depot` builds."""
    return make_unit(tag, UnitTypeId.SCV, at=(20.0, 20.0), orders=orders)


def _building_depot(tag: int, progress: float) -> raw_pb2.Unit:
    """One of our supply depots, `progress` of the way built."""
    return make_unit(tag, UnitTypeId.SUPPLY_DEPOT, at=(24.0, 20.0), build_progress=progress)


class TestConstruction:
    """Tested in game: an SCV's build order is aimed at the structure's snapped center once construction starts, at the
    structure itself when another SCV resumes it, and the SCV has no orders once construction is halted."""

    _BUILD = raw_pb2.UnitOrder(
        ability_id=AbilityId.SCV_BUILD_SUPPLY_DEPOT, target_world_space_pos=common_pb2.Point(x=24.3, y=19.8)
    )

    def test_an_scv_building_a_structure_is_its_builder_until_it_finishes(self) -> None:
        game = _Game()
        scv, depot = game.observe(0, _scv(1, self._BUILD), _building_depot(2, 0.3))
        assert isinstance(scv, OwnUnit) and isinstance(depot, OwnUnit)
        assert (scv.construction, depot.builder) == (depot, scv)
        game.observe(16, _scv(1), _building_depot(2, 1.0))
        assert (scv.construction, depot.builder) == (None, None)
        assert not scv.is_dead

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
            raw_pb2.UnitOrder(ability_id=AbilityId.GENERAL_MOVE, target_world_space_pos=common_pb2.Point(x=5, y=6)),
            raw_pb2.UnitOrder(ability_id=AbilityId.GENERAL_ATTACK, target_unit_tag=9),
            raw_pb2.UnitOrder(ability_id=AbilityId.BARRACKS_TRAIN_MARINE, progress=0.5),
        ]
        game = _Game()
        marine, _ = game.observe(0, make_unit(1, orders=orders), make_unit(9, alliance=_ENEMY))
        assert isinstance(marine, OwnUnit)
        assert marine.orders == (
            Order(AbilityId.GENERAL_MOVE, Point((5.0, 6.0)), 0.0),
            Order(AbilityId.GENERAL_ATTACK, game.tracker.units.present.by_id(400001), 0.0),
            Order(AbilityId.BARRACKS_TRAIN_MARINE, None, 0.5),
        )
        assert not marine.is_idle

    def test_a_tag_no_unit_was_reported_under_raises(self) -> None:
        marine = _one(make_unit(1, orders=[raw_pb2.UnitOrder(ability_id=AbilityId.GENERAL_ATTACK, target_unit_tag=9)]))
        assert isinstance(marine, OwnUnit)
        with pytest.raises(UnknownTagError, match="never reported a unit under tag 9"):
            _ = marine.orders

    def test_a_rally_names_the_unit_it_is_onto_or_else_the_point(self) -> None:
        """Seen in the corpus: a rally onto a mineral field that is mined out is left holding tag 2**32."""
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

    def test_passengers_an_add_on_and_a_target_are_the_units_themselves(self) -> None:
        """A passenger has left the observation, so it is the stale unit that went in."""
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

    def test_a_tag_the_game_leaves_at_zero_names_no_unit(self) -> None:
        marine = _one(make_unit(1))
        assert isinstance(marine, OwnUnit)
        assert (marine.add_on, marine.engaged_target) == (None, None)

    def test_the_weapon_cooldown_is_in_steps(self) -> None:
        marine = _one(make_unit(1, weapon_cooldown=11.0))
        assert isinstance(marine, OwnUnit)
        assert marine.weapon_cooldown_steps == 11.0

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
        """A realtime game skips steps, so a turn is not always as long as the last."""
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
        assert not game.tracker.units._unfinished

    def test_upgrades_new_to_the_observation_come_in_the_order_of_their_ids(self) -> None:
        tracker = _Tracker(_TABLES, Enemy())
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
        assert not any(getattr(changes, name) for name in changes.__slots__ if name not in {"own", "enemy"})
        assert not any(getattr(side, name) for side in (changes.own, changes.enemy) for name in side.__slots__)
        assert not game.tracker.units._unfinished
        assert not game.tracker.comparer._compared


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
        api.enemy.assume_upgrades(UpgradeId.STIMPACK)
        api.enemy.assume_upgrades(UpgradeId.COMBAT_SHIELD)
        api.enemy.forget_upgrades(UpgradeId.STIMPACK)
        assert api.enemy.upgrades == {UpgradeId.COMBAT_SHIELD}
        assert repr(api.enemy) == "Enemy(upgrades={COMBAT_SHIELD})"
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
    """Run with `pytest -m integration`. Starts the game and plays a minute of it."""
    try:
        game_map = Map.find("PylonAIE_v4")
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
        # Not the `tech_tree` cheat, which grants campaign upgrades no curated id names, and an observation holding one
        # raises (`docs/cheats.md`). What this needs researched, it researches.
        # Not `fast_build` either: a structure going up is one of the things read below.
        game.debug(debug_pb2.DebugCommand(game_state=debug_pb2.DebugGameState.free))

        # A morph keeps the object.
        game.debug(game.create(UnitTypeId.SIEGE_TANK, out_there))
        game.turn(2)
        tank = game.newest(UnitTypeId.SIEGE_TANK)
        game.order(AbilityId.SIEGE_TANK_SIEGE, tank)
        game.turn(90)
        assert tank.type_id is UnitTypeId.SIEGE_TANK_SIEGED
        assert game.tracker.units.present.get(tank.id) is tank

        # A unit loaded into a transport is stale, and the same object once unloaded.
        game.debug(
            game.create(UnitTypeId.MARINE, out_there + (3, 0)), game.create(UnitTypeId.MEDIVAC, out_there + (3, 1))
        )
        game.turn(2)
        marine, medivac = game.newest(UnitTypeId.MARINE), game.newest(UnitTypeId.MEDIVAC)
        game.order(AbilityId.MEDIVAC_LOAD, medivac, target=marine)
        game.turn(40)
        assert marine.is_stale
        assert isinstance(medivac, OwnUnit)
        assert marine in {passenger.unit for passenger in medivac.passengers}
        game.order(AbilityId.MEDIVAC_UNLOAD_AT, medivac, target=medivac.position)
        game.turn(60)
        assert not marine.is_stale
        assert game.tracker.units.present.get(marine.id) is marine
        # Out of the way, since both would shoot the enemy units made below.
        game.debug(game.kill(tank, marine))

        # A mineral field remembered from the start keeps its id when first seen.
        field = min(
            game.tracker.units.present.neutral.filter(lambda unit: unit.visibility is Visibility.IN_FOG),
            key=lambda unit: abs(unit.position.distance_to(home) - 40),
        )
        spot = game.open_ground(field.position.towards(middle, 7))
        game.debug(
            game.create(UnitTypeId.MARINE, spot + (2, 0)), game.create(UnitTypeId.SUPPLY_DEPOT, spot, owner=enemy)
        )
        game.turn(4)
        assert field.visibility is Visibility.IN_VISION
        assert game.tracker.units.present.get(field.id) is field
        spotter, depot = game.newest(UnitTypeId.MARINE), game.newest(UnitTypeId.SUPPLY_DEPOT)

        # A structure out of sight is remembered under its id, and back in sight under it again.
        health = depot.health
        game.debug(game.kill(spotter))
        game.turn(60)
        assert depot.visibility is Visibility.IN_FOG
        assert game.tracker.units.present.get(depot.id) is depot
        assert depot.health == health
        game.debug(game.create(UnitTypeId.MARINE, spot + (2, 0)))
        game.turn(4)
        assert depot.visibility is Visibility.IN_VISION
        assert game.tracker.units.present.get(depot.id) is depot

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
        assert game.tracker.units.known.get(depot.id) is None

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
        victim = game.tracker.units.present.enemy.of_type(UnitTypeId.MARINE)[-1]
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

        # A unit a phoenix holds up flies, though the game reports it as not flying.
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
        assert not queen._latest_data.is_flying
        assert queen.is_flying
        game.debug(game.kill(phoenix, queen))

        # An SCV building a structure is its builder until the structure finishes.
        game.debug(debug_pb2.DebugCommand(game_state=debug_pb2.DebugGameState.minerals))
        scv = game.tracker.units.present.own.of_type(UnitTypeId.SCV)[0]
        game.order(AbilityId.SCV_BUILD_SUPPLY_DEPOT, scv, target=game.open_ground(home.towards(middle, 8)))
        game.turn(120)
        (building,) = game.tracker.units.present.own.of_type(UnitTypeId.SUPPLY_DEPOT)
        assert not building.is_complete
        assert (scv.construction, building.builder) == (building, scv)
        game.turn(600)
        assert building.is_complete
        assert (scv.construction, building.builder) == (None, None)

        # Only death lets go of a unit.
        game.debug(game.kill(medivac))
        game.turn(2)
        assert medivac.is_dead
        assert game.tracker.units.known.get(medivac.id) is None
        client.leave_game()
        client.quit()
