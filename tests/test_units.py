"""A game's units, read from observations written here: ids, the fog, staleness, death, and what each read answers."""

import weakref
from contextlib import closing
from typing import Any

import pytest
from s2clientprotocol import common_pb2, data_pb2, debug_pb2, raw_pb2, sc2api_pb2

from sc2nachos import Api, NachOSError
from sc2nachos.constants import STEPS_PER_SECOND
from sc2nachos.gamedata import GameData
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, BuffId, UncuratedIdError, UnitTypeId
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
    Units,
    UnknownTagError,
    Visibility,
)
from sc2nachos.units._tracker import _MOVABLE_UNIT_TYPE_IDS, _UnitTracker
from support import FakeTransport, make_game_info, make_observation, make_response, make_tables, make_unit

_TABLES = make_tables(
    data_pb2.UnitTypeData(unit_id=UnitTypeId.BARRACKS, attributes=[data_pb2.Attribute.Structure]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.MARINE, attributes=[data_pb2.Attribute.Biological]),
)
_ENEMY = Alliance.ENEMY
_IN_FOG = Visibility.IN_FOG
_INVISIBLE = Visibility.INVISIBLE


class _Game:
    """A tracker fed one observation after another, as `Api.play` feeds it."""

    def __init__(self) -> None:
        self.tracker = _UnitTracker(_TABLES)

    def observe(self, step: int, *units: raw_pb2.Unit, dead: tuple[int, ...] = ()) -> list[Unit[Any]]:
        """Observe `units` at `step`, and answer the unit each one listed was read into, in the order given."""
        self.tracker.update(make_observation(step, units=units, dead=dead).observation.raw_data, step)
        by_tag = {unit.tag: unit for unit in self.tracker.present_units}
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
        game.tracker._units_seen_per_alliance[Alliance.OWN] = 99_998
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
        assert not game.tracker.present_units
        assert list(game.tracker.known_units) == [zergling]

    def test_a_stale_unit_that_comes_back_is_the_same_object(self) -> None:
        game = _Game()
        (zergling,) = game.observe(0, make_unit(1, alliance=_ENEMY))
        game.observe(16)
        (back,) = game.observe(32, make_unit(1, alliance=_ENEMY, at=(20.0, 20.0)))
        assert back is zergling
        assert not zergling.is_stale
        assert zergling.position == Point((20.0, 20.0))
        assert list(game.tracker.known_units) == [zergling]

    def test_a_unit_the_game_reports_dead_is_dead_and_let_go(self) -> None:
        game = _Game()
        (marine,) = game.observe(0, make_unit(1, health=45.0))
        game.observe(16, dead=(1,))
        assert marine.is_dead
        assert marine.is_stale
        assert marine.health == 45.0
        assert not game.tracker.known_units
        assert repr(marine) == "OwnUnit(MARINE, id=100001, at (10.00, 10.00), dead)"

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
        assert [unit.tag for unit in game.tracker.present_units] == [1]

    def test_units_are_listed_in_the_order_the_game_reported_them_new_ones_included(self) -> None:
        game = _Game()
        game.observe(0, make_unit(3), make_unit(1))
        game.observe(16, make_unit(2), make_unit(3), make_unit(4), make_unit(1))
        assert [unit.tag for unit in game.tracker.present_units] == [2, 3, 4, 1]

    def test_ending_the_game_leaves_every_unit_stale(self) -> None:
        game = _Game()
        (marine,) = game.observe(0, make_unit(1))
        game.tracker.end()
        assert marine.is_stale
        assert not game.tracker.present_units


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
        assert [unit.id for unit in game.tracker.known_units] == [400001]

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
        assert not game.tracker.known_units

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
            assert list(game.tracker.present_units) == [unit]
            assert (unit.position, unit.visibility, unit.health) == (Point((60.5, 30.5)), Visibility.IN_VISION, 900.0)
        game.observe(64, landed)
        assert list(game.tracker.known_units) == [unit]
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

    def test_an_uncurated_type_raises_when_read_and_names_the_id(self) -> None:
        unit = _one(make_unit(1, RawUnitTypeId.Viking))
        assert unit.tag == 1
        with pytest.raises(UncuratedIdError, match=r"UnitTypeId has no member for id \d+ \(RawUnitTypeId.Viking\)"):
            _ = unit.type_id
        assert repr(unit).startswith("OwnUnit(uncurated type")

    def test_an_uncurated_buff_raises_when_read(self) -> None:
        with pytest.raises(UncuratedIdError, match="DutchMarauderSlow"):
            _ = _one(make_unit(1, buff_ids=[RawBuffId.DutchMarauderSlow])).buffs

    def test_the_errors_are_the_librarys_own_and_the_built_ins_they_are(self) -> None:
        assert issubclass(NotReportedError, NachOSError) and issubclass(NotReportedError, LookupError)
        assert issubclass(UnknownTagError, NachOSError) and issubclass(UnknownTagError, LookupError)
        assert issubclass(UncuratedIdError, NachOSError) and issubclass(UncuratedIdError, ValueError)


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
        marine, _ = _Game().observe(0, make_unit(1, orders=orders), make_unit(9, alliance=_ENEMY))
        assert isinstance(marine, OwnUnit)
        assert marine.orders == (
            Order(AbilityId.GENERAL_MOVE, Point((5.0, 6.0)), 0.0),
            Order(AbilityId.GENERAL_ATTACK, 400001, 0.0),
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
        center, _ = _Game().observe(0, make_unit(1, rally_targets=rallies), make_unit(5, alliance=Alliance.NEUTRAL))
        assert isinstance(center, OwnUnit)
        assert center.rally_targets == (
            RallyTarget(Point((1.0, 2.0))),
            RallyTarget(300001),
            RallyTarget(Point((3.0, 4.0))),
        )

    def test_passengers_an_add_on_and_a_target_are_named_by_id(self) -> None:
        """A passenger has left the observation, so it is named by the id it had outside."""
        passenger = raw_pb2.PassengerUnit(tag=3, unit_type=UnitTypeId.MARINE, health=45, health_max=45)
        game = _Game()
        game.observe(0, make_unit(3), make_unit(4, UnitTypeId.TECH_LAB_BARRACKS), make_unit(6, alliance=_ENEMY))
        loaded = make_unit(1, passengers=[passenger], cargo_space_taken=1, add_on_tag=4, engaged_target_tag=6)
        holder, _, _ = game.observe(
            16, loaded, make_unit(4, UnitTypeId.TECH_LAB_BARRACKS), make_unit(6, alliance=_ENEMY)
        )
        assert isinstance(holder, OwnUnit)
        assert holder.passengers == (Passenger(100001, UnitTypeId.MARINE, 45.0, 45.0, 0.0, 0.0, 0.0, 0.0),)
        assert (holder.cargo_used, holder.cargo_max, holder.add_on_id, holder.engaged_target_id) == (
            1,
            0,
            100002,
            400001,
        )

    def test_a_tag_the_game_leaves_at_zero_names_no_unit(self) -> None:
        marine = _one(make_unit(1))
        assert isinstance(marine, OwnUnit)
        assert (marine.add_on_id, marine.engaged_target_id) == (None, None)

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

    def test_the_last_games_units_are_stale_once_the_next_game_starts(self) -> None:
        api = Api()
        self._play(api, make_observation(0, (1, Result.VICTORY), units=[make_unit(1)]))
        marine = api.units.by_id(100001)
        assert not marine.is_stale
        self._play(api, make_observation(0, (1, Result.VICTORY), units=[make_unit(1)]))
        assert marine.is_stale
        assert api.units.by_id(100001) is not marine


class _RealGame:
    """A game against the computer, played a step at a time by hand, with its units tracked."""

    def __init__(self, client: Client, player: int) -> None:
        self.client = client
        self.player = player
        self.map = GameMap(client.game_info())
        self.tracker = _UnitTracker(GameData(client.game_data()))

    def turn(self, steps: int) -> Units[Unit[Any]]:
        """Let `steps` pass, then observe."""
        self.client.step(steps)
        observation = self.client.observation().observation
        self.tracker.update(observation.raw_data, observation.game_loop)
        return self.tracker.present_units

    def debug(self, *commands: debug_pb2.DebugCommand) -> None:
        self.client.debug(commands)

    def create(self, unit_type: UnitTypeId, at: Point, *, owner: int | None = None) -> debug_pb2.DebugCommand:
        """The command creating a unit of `unit_type` at `at`, the player's own unless another `owner` is given."""
        position = common_pb2.Point2D(x=at.x, y=at.y)
        unit = debug_pb2.DebugCreateUnit(unit_type=unit_type, owner=owner or self.player, pos=position, quantity=1)
        return debug_pb2.DebugCommand(create_unit=unit)

    def kill(self, *units: Unit[Any]) -> debug_pb2.DebugCommand:
        return debug_pb2.DebugCommand(kill_unit=debug_pb2.DebugKillUnit(tag=[unit.tag for unit in units]))

    def order(self, ability: int, unit: Unit[Any], *, target: Unit[Any] | Point | None = None) -> None:
        command = raw_pb2.ActionRawUnitCommand(ability_id=ability, unit_tags=[unit.tag])
        if isinstance(target, Point):
            command.target_world_space_pos.x, command.target_world_space_pos.y = target
        elif target is not None:
            command.target_unit_tag = target.tag
        self.client.act([sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(unit_command=command))])

    def newest(self, unit_type: UnitTypeId) -> Unit[Any]:
        """The unit of `unit_type` first seen last."""
        return max(self.tracker.present_units.of_type(unit_type), key=lambda unit: unit.id)

    def open_ground(self, near: Point) -> Point:
        """The corner nearest to `near` that the four tiles around it can be built on."""
        placement = self.map.placement
        corner = near.snapped(step=1)
        for reach in range(12):
            for dx in range(-reach, reach + 1):
                for dy in range(-reach, reach + 1):
                    spot = corner + (dx, dy)
                    tiles = [spot + offset for offset in ((-0.5, -0.5), (0.5, -0.5), (-0.5, 0.5), (0.5, 0.5))]
                    if all(placement[tile] for tile in tiles):
                        return spot
        raise AssertionError(f"no open ground near {near}")


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
        game = _RealGame(client, client.join_game(Race.TERRAN))
        enemy = 3 - game.player
        units = game.turn(1)
        home = units.own.of_type(UnitTypeId.COMMAND_CENTER)[0].position
        middle = game.map.playable_area.center
        out_there = home.towards(middle, 12)
        game.debug(debug_pb2.DebugCommand(game_state=debug_pb2.DebugGameState.tech_tree))

        # A morph keeps the object.
        game.debug(game.create(UnitTypeId.SIEGE_TANK, out_there))
        game.turn(2)
        tank = game.newest(UnitTypeId.SIEGE_TANK)
        game.order(AbilityId.SIEGE_TANK_SIEGE, tank)
        game.turn(90)
        assert tank.type_id is UnitTypeId.SIEGE_TANK_SIEGED
        assert game.tracker.present_units.get(tank.id) is tank

        # A unit loaded into a transport is stale, and the same object once unloaded.
        game.debug(
            game.create(UnitTypeId.MARINE, out_there + (3, 0)), game.create(UnitTypeId.MEDIVAC, out_there + (3, 1))
        )
        game.turn(2)
        marine, medivac = game.newest(UnitTypeId.MARINE), game.newest(UnitTypeId.MEDIVAC)
        game.order(AbilityId.MEDIVAC_LOAD, medivac, target=marine)
        game.turn(40)
        assert marine.is_stale
        assert marine.id in {passenger.id for passenger in medivac.passengers}  # pyright: ignore[reportAttributeAccessIssue]
        game.order(AbilityId.MEDIVAC_UNLOAD_AT, medivac, target=medivac.position)
        game.turn(60)
        assert not marine.is_stale
        assert game.tracker.present_units.get(marine.id) is marine

        # A mineral field remembered from the start keeps its id when first seen.
        field = min(
            game.tracker.present_units.neutral.filter(lambda unit: unit.visibility is Visibility.IN_FOG),
            key=lambda unit: abs(unit.position.distance_to(home) - 40),
        )
        spot = game.open_ground(field.position.towards(middle, 7))
        game.debug(
            game.create(UnitTypeId.MARINE, spot + (2, 0)), game.create(UnitTypeId.SUPPLY_DEPOT, spot, owner=enemy)
        )
        game.turn(4)
        assert field.visibility is Visibility.IN_VISION
        assert game.tracker.present_units.get(field.id) is field
        spotter, depot = game.newest(UnitTypeId.MARINE), game.newest(UnitTypeId.SUPPLY_DEPOT)

        # A structure out of sight is remembered under its id, and back in sight under it again.
        health = depot.health
        game.debug(game.kill(spotter))
        game.turn(60)
        assert depot.visibility is Visibility.IN_FOG
        assert game.tracker.present_units.get(depot.id) is depot
        assert depot.health == health
        game.debug(game.create(UnitTypeId.MARINE, spot + (2, 0)))
        game.turn(4)
        assert depot.visibility is Visibility.IN_VISION
        assert game.tracker.present_units.get(depot.id) is depot

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
        assert game.tracker.known_units.get(depot.id) is None

        # A neural parasite takes a unit over and lets it go, changing its class both times.
        game.debug(
            game.create(UnitTypeId.INFESTOR, out_there + (0, 4)),
            game.create(UnitTypeId.MARINE, out_there + (4, 4), owner=enemy),
        )
        game.turn(2)
        infestor = game.newest(UnitTypeId.INFESTOR)
        victim = game.tracker.present_units.enemy.of_type(UnitTypeId.MARINE)[-1]
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

        # Only death lets go of a unit.
        game.debug(game.kill(tank))
        game.turn(2)
        assert tank.is_dead
        assert game.tracker.known_units.get(tank.id) is None
        client.leave_game()
        client.quit()
