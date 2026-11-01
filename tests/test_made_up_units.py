"""`sc2nachos.testing`: the units a bot's tests make from `UnitReport`s through `MadeUpUnits`, on the game's data
NachOS ships."""

import dataclasses
import lzma
from importlib import resources

import pytest
from s2clientprotocol import data_pb2

from sc2nachos import GameTime
from sc2nachos.gamedata._techtree import TECH_TREE
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, BuffId, UnitTypeId
from sc2nachos.testing import MadeUpUnits, UnitReport, load_game_data
from sc2nachos.units import Alliance, CloakState, OwnUnit, Unit, Visibility
from support import load_tool, make_tables


class TestUnitReport:
    """What a report reads back as, once taken in."""

    def test_by_default_it_is_this_players_finished_unit_in_sight(self):
        """The unit is an `OwnUnit` of player 1, complete and visible, half a tile in radius, doing nothing."""
        (marine,) = MadeUpUnits().observe(UnitReport(1, UnitTypeId.MARINE))
        assert isinstance(marine, OwnUnit)
        assert (marine.type_id, marine.owner_id, marine.position, marine.visibility) == (
            UnitTypeId.MARINE,
            1,
            Point((10.0, 10.0)),
            Visibility.VISIBLE,
        )
        assert (marine.is_complete, marine.radius, marine.orders, marine.cloak_state) == (
            True,
            0.5,
            (),
            CloakState.NOT_CLOAKED,
        )

    def test_an_enemy_unit_is_player_twos_and_a_neutral_one_player_sixteens(self):
        """Units of the other sides are plain `Unit`s, of the players the game numbers them."""
        zergling, mineral_field = MadeUpUnits().observe(
            UnitReport(1, UnitTypeId.ZERGLING, alliance=Alliance.ENEMY),
            UnitReport(2, UnitTypeId.MINERAL_FIELD, alliance=Alliance.NEUTRAL, mineral_contents=1800),
        )
        assert not isinstance(zergling, OwnUnit) and isinstance(zergling, Unit)
        assert (zergling.alliance, zergling.owner_id) == (Alliance.ENEMY, 2)
        assert (mineral_field.alliance, mineral_field.owner_id, mineral_field.mineral_contents) == (
            Alliance.NEUTRAL,
            16,
            1800,
        )

    def test_a_maximum_not_given_reads_as_its_value(self):
        """A marine given 30 health reads 30 of 30; one given its maximum too reads 30 of 45."""
        unhurt_marine, hurt_marine = MadeUpUnits().observe(
            UnitReport(1, UnitTypeId.MARINE, health=30), UnitReport(2, UnitTypeId.MARINE, health=30, health_max=45)
        )
        assert (unhurt_marine.health, unhurt_marine.health_max) == (30, 30)
        assert (hurt_marine.health, hurt_marine.health_max, hurt_marine.health_fraction) == (30, 45, 30 / 45)

    @pytest.mark.parametrize(
        ("field_name", "given", "read_back"),
        [
            ("height", 11.0, 11.0),
            ("visibility", Visibility.INVISIBLE, Visibility.INVISIBLE),
            ("build_progress", 0.5, 0.5),
            ("shield", 5.0, 5.0),
            ("shield_max", 40.0, 40.0),
            ("energy", 50.0, 50.0),
            ("energy_max", 200.0, 200.0),
            ("radius", 0.375, 0.375),
            ("facing", 1.5, 1.5),
            ("cloak_state", CloakState.CLOAKED, CloakState.CLOAKED),
            ("buffs", (BuffId.MARINE_STIMMED,), frozenset({BuffId.MARINE_STIMMED})),
            ("is_selected", True, True),
            ("is_flying", True, True),
            ("is_burrowed", True, True),
            ("is_hallucination", True, True),
            ("is_powered", True, True),
            ("is_active", True, True),
            ("detect_range", 11.0, 11.0),
            ("radar_range", 30.0, 30.0),
            ("attack_upgrade_level", 1, 1),
            ("armor_upgrade_level", 2, 2),
            ("shield_upgrade_level", 3, 3),
            ("mineral_contents", 1800, 1800),
            ("vespene_contents", 2250, 2250),
            ("assigned_harvesters", 14, 14),
            ("ideal_harvesters", 16, 16),
            ("cargo_used", 2, 2),
            ("cargo_max", 8, 8),
            ("weapon_cooldown", GameTime(steps=6), GameTime(steps=6)),
            ("buff_duration_max", GameTime(steps=1344), GameTime(steps=1344)),
        ],
    )
    def test_each_field_reads_back_as_the_property_of_its_name(self, field_name, given, read_back):
        """A field given reads back as given, through the unit's property of the same name."""
        (marine,) = MadeUpUnits().observe(UnitReport(1, UnitTypeId.MARINE, **{field_name: given}))
        assert getattr(marine, field_name) == read_back

    def test_at_reads_back_as_the_position(self):
        """The one field named otherwise."""
        (marine,) = MadeUpUnits().observe(UnitReport(1, UnitTypeId.MARINE, at=(20.5, 30.25)))
        assert marine.position == Point((20.5, 30.25))

    def test_what_is_left_of_a_timed_life_reads_back_beside_its_length(self):
        """A MULE with 100 steps left of 1344 reads both; a length of a fraction of a step reads the nearest."""
        (mule,) = MadeUpUnits().observe(
            UnitReport(
                1,
                UnitTypeId.MULE,
                buff_duration_max=GameTime(steps=1343.5),
                buff_duration_remaining=GameTime(steps=100),
            )
        )
        assert (mule.buff_duration_max, mule.buff_duration_remaining) == (GameTime(steps=1344), GameTime(steps=100))

    def test_an_engaged_target_is_named_by_its_report(self):
        """A marine attacking a zergling reads the zergling as the unit it is engaged with."""
        zergling_report = UnitReport(2, UnitTypeId.ZERGLING, at=(12.0, 10.0), alliance=Alliance.ENEMY)
        marine, zergling = MadeUpUnits().observe(
            UnitReport(1, UnitTypeId.MARINE, engaged_target=zergling_report), zergling_report
        )
        assert marine.engaged_target is zergling

    def test_an_add_on_is_named_by_its_report_or_its_tag(self):
        """A barracks reported with its tech lab's report reads the tech lab as its add-on."""
        tech_lab_report = UnitReport(2, UnitTypeId.TECH_LAB_BARRACKS, at=(12.5, 9.5))
        barracks, _ = MadeUpUnits().observe(
            UnitReport(1, UnitTypeId.BARRACKS, at=(10.0, 10.0), add_on=tech_lab_report), tech_lab_report
        )
        assert barracks.add_on is not None and barracks.add_on.tag == 2
        assert barracks.has_tech_lab

    def test_an_ally_raises(self):
        """The game made up has two players."""
        with pytest.raises(ValueError, match="no ALLY units"):
            UnitReport(1, UnitTypeId.MARINE, alliance=Alliance.ALLY)

    def test_a_point_with_a_height_raises(self):
        """A unit's height is a field of its own, so a point of three coordinates is not taken for one."""
        with pytest.raises(ValueError, match="give its height as height"):
            UnitReport(1, UnitTypeId.MARINE, at=(10.0, 10.0, 11.0))


class TestOrders:
    """A unit's orders, each aimed at a point, a unit's report, a tag or nothing."""

    def test_an_order_aimed_at_a_point(self):
        """A move to (20, 10) reads back as the move, aimed at that point."""
        (marine,) = MadeUpUnits().observe(UnitReport(1, UnitTypeId.MARINE, orders=[(AbilityId.MOVE, (20.0, 10.0))]))
        assert [(order.ability, order.target, order.progress) for order in marine.orders] == [
            (AbilityId.MOVE, Point((20.0, 10.0)), 0.0)
        ]

    def test_an_order_aimed_at_a_units_report(self):
        """An SCV ordered to gather from a mineral field's report reads the mineral field as the order's target."""
        mineral_field_report = UnitReport(2, UnitTypeId.MINERAL_FIELD, alliance=Alliance.NEUTRAL, at=(14.0, 10.0))
        scv, mineral_field = MadeUpUnits().observe(
            UnitReport(1, UnitTypeId.SCV, orders=[(AbilityId.GATHER, mineral_field_report)]),
            mineral_field_report,
        )
        assert scv.current_order.target is mineral_field

    def test_an_order_aimed_at_a_tag_never_listed_has_no_known_target(self):
        """As the game reports a gather sent to a mineral field in the fog by its snapshot's tag."""
        (scv,) = MadeUpUnits().observe(UnitReport(1, UnitTypeId.SCV, orders=[(AbilityId.GATHER, 99)]))
        assert (scv.current_order.ability, scv.current_order.known_target) == (AbilityId.GATHER, None)

    def test_an_order_with_progress_and_no_target(self):
        """A barracks training a marine, halfway, reads the train and its progress."""
        (barracks,) = MadeUpUnits().observe(
            UnitReport(1, UnitTypeId.BARRACKS, orders=[(AbilityId.BARRACKS_TRAIN_MARINE, None, 0.5)])
        )
        assert [(order.ability, order.target, order.progress) for order in barracks.orders] == [
            (AbilityId.BARRACKS_TRAIN_MARINE, None, 0.5)
        ]


class TestObservations:
    """One observation after another, as a game gives them."""

    def test_the_units_come_back_in_the_order_of_their_reports(self):
        """Whatever their sides."""
        units = MadeUpUnits().observe(
            UnitReport(3, UnitTypeId.ZERGLING, alliance=Alliance.ENEMY),
            UnitReport(1, UnitTypeId.MARINE),
            UnitReport(2, UnitTypeId.SCV),
        )
        assert [unit.tag for unit in units] == [3, 1, 2]

    def test_a_tag_reported_again_is_the_same_unit_read_anew(self):
        """A marine moved two tiles in eight steps is the same object, at its new point, at 5.6 tiles a second."""
        made_up_units = MadeUpUnits()
        marine_report = UnitReport(1, UnitTypeId.MARINE, at=(10.0, 10.0))
        (marine,) = made_up_units.observe(marine_report)
        (moved_marine,) = made_up_units.observe(dataclasses.replace(marine_report, at=(12.0, 10.0)), step=8)
        assert moved_marine is marine
        assert (marine.position, marine.velocity) == (Point((12.0, 10.0)), Point((5.6, 0.0)))

    def test_a_unit_not_reported_again_is_no_longer_listed(self):
        """The marine left out of the second observation is unlisted, and `units` holds only the SCV."""
        made_up_units = MadeUpUnits()
        marine, scv = made_up_units.observe(UnitReport(1, UnitTypeId.MARINE), UnitReport(2, UnitTypeId.SCV))
        made_up_units.observe(UnitReport(2, UnitTypeId.SCV))
        assert (marine.is_listed, scv.is_listed) == (False, True)
        assert list(made_up_units.units) == [scv]

    def test_a_unit_reported_dead_by_its_report_or_tag_is_dead(self):
        """Both marines die, one named by its report and one by its tag."""
        made_up_units = MadeUpUnits()
        first_marine_report = UnitReport(1, UnitTypeId.MARINE)
        first_marine, second_marine = made_up_units.observe(first_marine_report, UnitReport(2, UnitTypeId.MARINE))
        made_up_units.observe(dead=[first_marine_report, 2])
        assert (first_marine.is_dead, second_marine.is_dead) == (True, True)

    def test_the_step_goes_on_by_one_unless_given_and_never_back(self):
        """Observations at 0, then 1 by default, then 10 as given; 10 again raises."""
        made_up_units = MadeUpUnits()
        marine_report = UnitReport(1, UnitTypeId.MARINE)
        (marine,) = made_up_units.observe(marine_report)
        assert marine.first_seen == GameTime(steps=0)
        made_up_units.observe(dataclasses.replace(marine_report, at=(11.0, 10.0)))
        assert marine.velocity == Point((22.4, 0.0))
        made_up_units.observe(marine_report, step=10)
        with pytest.raises(ValueError, match="at step 10 cannot follow one at step 10"):
            made_up_units.observe(marine_report, step=10)


class TestGameData:
    """The game's data the units read their types from."""

    def test_the_shipped_game_data_is_the_first_corpus_games_named_for_the_tech_trees_build(self):
        """Recording the corpus again needs `tools/write_shipped_game_data.py` run again."""
        shipped_file = resources.files("sc2nachos.testing").joinpath(f"game_data_{TECH_TREE.base_build}.binpb.xz")
        corpus_game_data = load_tool("write_shipped_game_data").first_corpus_game_data()
        assert lzma.decompress(shipped_file.read_bytes()) == corpus_game_data.SerializeToString(deterministic=True)

    def test_the_game_data_of_the_build_shipped_describes_the_units(self):
        """Build 75689's data holds a marine's row: 50 minerals."""
        assert load_game_data(base_build=75689).units[UnitTypeId.MARINE].cost.minerals == 50

    def test_a_build_not_shipped_raises(self):
        """A test that names its build fails at once once NachOS ships the data of another."""
        with pytest.raises(ValueError, match="no game data of build 12345, only of 75689"):
            load_game_data(base_build=12345)

    def test_by_default_the_units_read_the_newest_build_shipped(self):
        """A made-up marine reads its type's row of build 75689's data: a weapon of range 5."""
        (marine,) = MadeUpUnits().observe(UnitReport(1, UnitTypeId.MARINE))
        assert marine.ground_range == 5

    def test_game_data_given_is_read_in_its_place(self):
        """A test may make units on game data of its own."""
        own_game_data = make_tables(data_pb2.UnitTypeData(unit_id=UnitTypeId.MARINE))
        assert MadeUpUnits(own_game_data).game_data is own_game_data
