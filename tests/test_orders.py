"""The orders a bot gives: what goes out in a turn's request, and what the game refuses of it."""

# Each `type: ignore` below marks a call the type checker must reject, so an unneeded one fails.
# pyright: reportUnnecessaryTypeIgnoreComment=true

import inspect
import random
from collections.abc import Sequence
from contextlib import closing
from functools import partial
from typing import Any, assert_type

import pytest
from s2clientprotocol import common_pb2, data_pb2, debug_pb2, error_pb2, raw_pb2, sc2api_pb2

from sc2nachos import Api, GameTime
from sc2nachos.events import TurnEvent
from sc2nachos.gamedata import Cost, GameData
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import POINT_PRECISION, Point, Point3D
from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId
from sc2nachos.ids.raw import RawAbilityId
from sc2nachos.match import Computer, Difficulty, Participant, Race
from sc2nachos.orders import IssuedOrder, OrderBook
from sc2nachos.orders._commands import unit_commands_giving
from sc2nachos.orders._targets import aimed_at, same_target, target_as_kept
from sc2nachos.protocol import Client, ProtocolError, WebSocketTransport
from sc2nachos.run import GameProcess, MapFile, MapNotFoundError
from sc2nachos.state import ActionFailure, ActionResult, UnknownActionResultError
from sc2nachos.testing import MadeUpUnits, UnitReport, load_game_data
from sc2nachos.units import Alliance, OwnUnit, Unit, Units, UnitType
from sc2nachos.units._tracking import _Tracker
from support import make_client, make_enemy, make_game_info, make_observation, make_response, make_tables, make_unit

_MOVE = AbilityId.MOVE
# The id a unit reports a move under, which reads as the move.
_MOVE_RUNS_AS = RawAbilityId.Move_Move
_ATTACK = AbilityId.ATTACK
_STIM = AbilityId.STIM
_HOLD_FIRE = AbilityId.HOLD_FIRE_ON
_CREEP_TUMOR = AbilityId.BUILD_CREEP_TUMOR
# An action that goes out with the turn's orders and belongs to none of them.
_CHAT = sc2api_pb2.Action(action_chat=sc2api_pb2.ActionChat(message="gg"))
_TRAIN_MARINE = AbilityId.BARRACKS_TRAIN_MARINE
_TRAIN_REAPER = AbilityId.BARRACKS_TRAIN_REAPER
_RALLY = AbilityId.RALLY_UNITS
_SMART = AbilityId.SMART
_UNLOAD = AbilityId.UNLOAD
_TRAIN_SCV = AbilityId.COMMAND_CENTER_TRAIN_SCV
_UNLOAD_AT = AbilityId.UNLOAD_AT
_SIEGE = AbilityId.SIEGE
_SIEGE_AT = AbilityId.LIBERATOR_SIEGE_AT
_UNSIEGE = AbilityId.UNSIEGE
_SIEGED = {UnitTypeId.SIEGE_TANK_SIEGED, UnitTypeId.LIBERATOR_SIEGED}
_SIEGE_FORMS = [UnitTypeId.SIEGE_TANK, UnitTypeId.LIBERATOR, *_SIEGED]
# An unset `target` reads as the enum's first value, the one for an ability aimed at nothing.
_AT_A_POINT_OR_UNIT = data_pb2.AbilityData.Target.PointOrUnit
_AT_A_POINT = data_pb2.AbilityData.Target.Point

_TABLES = make_tables(
    data_pb2.UnitTypeData(unit_id=UnitTypeId.BARRACKS, attributes=[data_pb2.Attribute.Structure]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.COMMAND_CENTER, attributes=[data_pb2.Attribute.Structure]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.MARINE, attributes=[data_pb2.Attribute.Biological]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.GHOST, attributes=[data_pb2.Attribute.Biological]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.LURKER_BURROWED, attributes=[data_pb2.Attribute.Biological]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.QUEEN, attributes=[data_pb2.Attribute.Biological]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.CREEP_TUMOR_BURROWED, attributes=[data_pb2.Attribute.Structure]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.MEDIVAC, attributes=[data_pb2.Attribute.Mechanical]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.LIBERATOR, attributes=[data_pb2.Attribute.Mechanical]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.LIBERATOR_SIEGED, attributes=[data_pb2.Attribute.Mechanical]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.SIEGE_TANK, attributes=[data_pb2.Attribute.Mechanical]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.SIEGE_TANK_SIEGED, attributes=[data_pb2.Attribute.Mechanical]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.BUNKER, attributes=[data_pb2.Attribute.Structure]),
    abilities=[
        data_pb2.AbilityData(ability_id=_MOVE, target=_AT_A_POINT_OR_UNIT),
        data_pb2.AbilityData(ability_id=_ATTACK, target=_AT_A_POINT_OR_UNIT),
        data_pb2.AbilityData(ability_id=_STIM),
        data_pb2.AbilityData(ability_id=_HOLD_FIRE),
        data_pb2.AbilityData(ability_id=_CREEP_TUMOR, target=_AT_A_POINT),
        data_pb2.AbilityData(ability_id=_TRAIN_MARINE),
        data_pb2.AbilityData(ability_id=_TRAIN_REAPER),
        data_pb2.AbilityData(ability_id=_RALLY, target=_AT_A_POINT_OR_UNIT),
        data_pb2.AbilityData(ability_id=_SMART, target=_AT_A_POINT_OR_UNIT),
        data_pb2.AbilityData(ability_id=_UNLOAD),
        data_pb2.AbilityData(ability_id=_TRAIN_SCV),
        data_pb2.AbilityData(ability_id=_UNLOAD_AT, target=_AT_A_POINT_OR_UNIT),
        data_pb2.AbilityData(ability_id=RawAbilityId.Morph_LiberatorAGMode, target=_AT_A_POINT),
        data_pb2.AbilityData(ability_id=RawAbilityId.SiegeMode_SiegeMode),
        data_pb2.AbilityData(ability_id=RawAbilityId.Morph_SurveillanceMode),
        data_pb2.AbilityData(ability_id=RawAbilityId.Morph_OversightMode),
        data_pb2.AbilityData(ability_id=RawAbilityId.Unsiege_Unsiege),
        data_pb2.AbilityData(ability_id=RawAbilityId.Morph_LiberatorAAMode),
        data_pb2.AbilityData(ability_id=RawAbilityId.Morph_ObserverMode),
        data_pb2.AbilityData(ability_id=RawAbilityId.Morph_OverseerMode),
    ],
)


def _verdict(result: ActionResult) -> error_pb2.ActionResult.ValueType:
    """An action result as the protocol spells it."""
    return error_pb2.ActionResult.ValueType(result)


class _Game:
    """An order book over a tracker, fed one observation at a time as `Api.play` feeds it."""

    def __init__(self, *verdicts: list[ActionResult], tables: GameData = _TABLES) -> None:
        """A game with `tables` that answers each flush with the next of `verdicts`, one result per action sent."""
        responses = [
            make_response(action=sc2api_pb2.ResponseAction(result=[_verdict(result) for result in verdict]))
            for verdict in verdicts
        ]
        self.client, self.transport = make_client(*responses)
        self.book = OrderBook(tables)
        self.tracker = _Tracker(tables, make_enemy(tables), order_book=self.book)
        self.map = GameMap(make_game_info(), start_location=Point((0.5, 0.5)))

    def observe(self, step: int, *units: raw_pb2.Unit, dead: tuple[int, ...] = ()) -> None:
        """Take in an observation of `units` at `step`, in which the units under the tags `dead` died."""
        observation = make_observation(step, units=units, dead=dead)
        self.tracker.update(observation.observation.raw_data, step)
        changes = self.tracker.last_changes
        changed_hands = [unit for unit, _ in changes.units_alliance_changed]
        lost = [*changes.units_died, *changes.units_found_dead, *changed_hands]
        self.book._observe(GameTime(steps=step), lost)

    def flush(self, *, then: Sequence[sc2api_pb2.Action] = ()) -> sc2api_pb2.RequestAction | None:
        """Send the turn's orders, and `then` after them, and return the request that went out, or `None` if none
        did."""
        before = len(self.transport.requests)
        self.book._send(self.client, then=then)
        sent = self.transport.requests[before:]
        return sent[0].action if sent else None

    def own(self, tag: int) -> OwnUnit[Any]:
        """This player's unit reported under `tag`."""
        unit = self.tracker.unit_tracker.by_tag(tag)
        assert isinstance(unit, OwnUnit)
        return unit

    def refused(self) -> list[tuple[int, AbilityId | None, ActionResult]]:
        """What the game refused of the turn last sent: the tag of each unit a refused command named, the ability
        ordered, and the game's answer."""
        refused: list[tuple[int, AbilityId | None, ActionResult]] = []
        for failure in self.book._refusals:
            assert failure.unit is not None
            refused.append((failure.unit.tag, failure.ability, failure.action_result))
        return refused


def _marine(tag: int, *orders: raw_pb2.UnitOrder, at: tuple[float, float] = (10.0, 10.0)) -> raw_pb2.Unit:
    """One of this player's marines, carrying out `orders`."""
    return make_unit(tag, UnitTypeId.MARINE, at=at, orders=orders)


def _barracks(tag: int, *orders: raw_pb2.UnitOrder) -> raw_pb2.Unit:
    """One of this player's barracks, carrying out `orders`."""
    return make_unit(tag, UnitTypeId.BARRACKS, at=(12.0, 12.0), orders=orders)


def _training(progress: float = 0.5) -> raw_pb2.UnitOrder:
    """The order a barracks shows while it makes a marine."""
    return raw_pb2.UnitOrder(ability_id=_TRAIN_MARINE, progress=progress)


def _moving(to: tuple[float, float] = (20.0, 20.0)) -> raw_pb2.UnitOrder:
    """The order a marine shows while moving to `to`, under the exact id a move runs as."""
    return raw_pb2.UnitOrder(ability_id=_MOVE_RUNS_AS, target_world_space_pos=common_pb2.Point(x=to[0], y=to[1]))


def _commands(request: sc2api_pb2.RequestAction | None) -> list[raw_pb2.ActionRawUnitCommand]:
    """The unit commands of a request that went out."""
    assert request is not None
    return [action.action_raw.unit_command for action in request.actions if action.action_raw.HasField("unit_command")]


class TestGivingAnOrder:
    def test_an_order_goes_out_as_one_command_naming_every_unit_it_was_given_to(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2))
        order = game.book.issue([game.own(1), game.own(2)], _MOVE, target=(20.0, 21.0))

        (command,) = _commands(game.flush())

        assert command.ability_id == _MOVE
        assert list(command.unit_tags) == [1, 2]
        assert (command.target_world_space_pos.x, command.target_world_space_pos.y) == (20.0, 21.0)
        assert not command.queue_command
        assert order.target == Point((20.0, 21.0))
        assert game.refused() == []

    def test_one_unit_is_given_an_order_without_a_collection(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        game.book.issue(game.own(1), _MOVE, target=Point((20.0, 21.0)))

        (command,) = _commands(game.flush())

        assert list(command.unit_tags) == [1]

    def test_an_order_is_aimed_at_a_unit_or_at_nothing(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2))
        game.book.issue(game.own(1), _ATTACK, target=game.own(2))
        game.book.issue(game.own(2), _STIM)

        attack, stim = _commands(game.flush())

        assert attack.target_unit_tag == 2
        assert stim.WhichOneof("target") is None

    def test_a_queued_order_says_so(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0), queued=True)

        (command,) = _commands(game.flush())

        assert command.queue_command

    def test_an_order_says_what_it_is_in_a_repr(self) -> None:
        game = _Game()
        game.observe(0, _marine(1), _marine(2))

        one = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        both = game.book.issue([game.own(1), game.own(2)], _MOVE, target=(20.0, 21.0))

        assert repr(one) == f"IssuedOrder(MOVE, {game.own(1)!r})"
        assert repr(both) == "IssuedOrder(MOVE, 2 units)"

    def test_an_ability_aimed_at_what_it_cannot_take_is_a_mistake(self) -> None:
        """The three cases the game was seen to answer `ERROR`, which NachOS now rejects itself."""
        game = _Game()
        game.observe(0, _marine(1))
        marine = game.own(1)

        with pytest.raises(TypeError, match="STIM takes no target"):
            game.book.issue(marine, _STIM, target=(20.0, 21.0))
        with pytest.raises(TypeError, match="MOVE takes a point or a unit"):
            game.book.issue(marine, _MOVE)
        assert game.flush() is None

    def test_an_order_to_no_unit_is_a_mistake(self) -> None:
        game = _Game()
        game.observe(0, _marine(1))

        with pytest.raises(ValueError, match="needs a unit"):
            game.book.issue([], _MOVE, target=(20.0, 21.0))

    def test_a_turn_that_orders_nothing_sends_nothing(self) -> None:
        game = _Game()
        game.observe(0, _marine(1))

        assert game.flush() is None
        assert game.transport.requests == []

    def test_actions_of_no_order_go_out_after_the_turns_commands(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        request = game.flush(then=[_CHAT])

        assert request is not None
        assert [(action.HasField("action_raw"), action.HasField("action_chat")) for action in request.actions] == [
            (True, False),
            (False, True),
        ]

    def test_actions_of_no_order_go_out_with_no_command_too(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))

        request = game.flush(then=[_CHAT])

        assert request is not None
        assert len(request.actions) == 1


class TestOrderingAUnitThroughItself:
    """`unit.issue(ability, ...)` is `api.orders.issue(unit, ability, ...)`."""

    def test_the_order_goes_into_the_book_as_the_book_would_have_taken_it(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        marine = game.own(1)

        order = marine.issue(_MOVE, target=(20.0, 21.0), queued=True)

        assert_type(order, IssuedOrder)
        assert game.book.pending == (order,)
        assert (order.ability, order.units, order.target, order.queued) == (
            _MOVE,
            Units([marine]),
            Point((20.0, 21.0)),
            True,
        )
        (command,) = _commands(game.flush())
        assert (command.ability_id, list(command.unit_tags), command.queue_command) == (_MOVE, [1], True)

    def test_an_order_given_only_an_ability_is_aimed_at_nothing_and_not_queued(self) -> None:
        game = _Game()
        game.observe(0, _marine(1))

        order = game.own(1).issue(_STIM)

        assert (order.target, order.queued) == (None, False)

    def test_force_sends_it_to_a_unit_already_carrying_it_out(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0)), _moving((30.0, 31.0))))

        game.own(1).issue(_MOVE, target=(20.0, 21.0), force=True)

        assert len(_commands(game.flush())) == 1

    def test_it_raises_as_the_book_does(self) -> None:
        game = _Game()
        game.observe(0, _marine(1))

        with pytest.raises(TypeError, match="STIM takes no target"):
            game.own(1).issue(_STIM, target=(20.0, 21.0))

    def test_it_takes_the_book_s_arguments_but_the_units(self) -> None:
        """So that an argument added to the book's `issue` is not left out of the unit's."""
        book_parameters = list(inspect.signature(OrderBook.issue).parameters.values())
        unit_parameters = list(inspect.signature(OwnUnit.issue).parameters.values())
        assert [parameter for parameter in book_parameters if parameter.name != "units"] == unit_parameters


def _made_up(*reports: UnitReport) -> tuple[MadeUpUnits, list[Any]]:
    """Units of `reports` made up on the game's data NachOS ships, with the book they are ordered into."""
    made_up = MadeUpUnits()
    return made_up, made_up.observe(*reports)


class TestTrainingBuildingAndResearching:
    """`train`, `build` and `research` order the ability that makes a unit type or researches an upgrade, as `issue`
    orders it, on the game's own data."""

    def test_a_structure_trains_a_unit_with_its_own_ability(self) -> None:
        made_up, (barracks, gateway, warp_gate) = _made_up(
            UnitReport(1, UnitTypeId.BARRACKS), UnitReport(2, UnitTypeId.GATEWAY), UnitReport(3, UnitTypeId.WARP_GATE)
        )

        marine = barracks.train(UnitTypeId.MARINE)
        zealot = gateway.train(UnitTypeId.ZEALOT)
        warped_in = warp_gate.train(UnitTypeId.ZEALOT, target=(20.0, 21.0))

        assert_type(made_up.orders.train(barracks, UnitTypeId.MARINE), IssuedOrder)
        assert (marine.ability, marine.units, marine.target) == (
            AbilityId.BARRACKS_TRAIN_MARINE,
            Units([barracks]),
            None,
        )
        assert zealot.ability is AbilityId.GATEWAY_TRAIN_ZEALOT
        assert (warped_in.ability, warped_in.target) == (AbilityId.WARP_GATE_WARP_IN_ZEALOT, Point((20.0, 21.0)))

    def test_a_larva_trains_what_it_morphs_into(self) -> None:
        _, (larva,) = _made_up(UnitReport(1, UnitTypeId.LARVA))

        assert larva.train(UnitTypeId.DRONE).ability is AbilityId.LARVA_MORPH_DRONE

    def test_several_structures_train_with_one_order_where_their_types_share_the_ability(self) -> None:
        made_up, (command_center, orbital_command) = _made_up(
            UnitReport(1, UnitTypeId.COMMAND_CENTER), UnitReport(2, UnitTypeId.ORBITAL_COMMAND)
        )

        order = made_up.orders.train([command_center, orbital_command], UnitTypeId.SCV)

        assert (order.ability, order.units) == (
            AbilityId.COMMAND_CENTER_TRAIN_SCV,
            Units([command_center, orbital_command]),
        )

    def test_types_making_it_with_different_abilities_are_ordered_apart(self) -> None:
        made_up, (gateway, warp_gate) = _made_up(UnitReport(1, UnitTypeId.GATEWAY), UnitReport(2, UnitTypeId.WARP_GATE))

        with pytest.raises(ValueError, match="a GATEWAY and a WARP_GATE make a ZEALOT with different abilities"):
            made_up.orders.train([gateway, warp_gate], UnitTypeId.ZEALOT)
        assert made_up.orders.pending == ()

    def test_a_unit_not_making_the_type_raises_saying_what_does(self) -> None:
        made_up, (barracks, high_templar) = _made_up(
            UnitReport(1, UnitTypeId.BARRACKS), UnitReport(2, UnitTypeId.HIGH_TEMPLAR)
        )

        with pytest.raises(ValueError, match="^a BARRACKS does not make a ZEALOT: a GATEWAY or a WARP_GATE does$"):
            barracks.train(UnitTypeId.ZEALOT)
        with pytest.raises(ValueError, match="^nothing makes a ARCHON$"):
            made_up.orders.train(high_templar, UnitTypeId.ARCHON)

    def test_what_another_method_orders_raises_naming_it(self) -> None:
        """A command center's orbital is a morph, which `issue` orders, and an SCV's depot a build."""
        made_up, (command_center, scv) = _made_up(
            UnitReport(1, UnitTypeId.COMMAND_CENTER), UnitReport(2, UnitTypeId.SCV)
        )

        with pytest.raises(
            ValueError, match="COMMAND_CENTER_MORPH_ORBITAL_COMMAND, which `issue` orders, not `train`$"
        ):
            command_center.train(UnitTypeId.ORBITAL_COMMAND)
        with pytest.raises(ValueError, match="SCV_BUILD_SUPPLY_DEPOT, which `build` orders, not `train`$"):
            made_up.orders.train(scv, UnitTypeId.SUPPLY_DEPOT)
        with pytest.raises(ValueError, match="COMMAND_CENTER_TRAIN_SCV, which `train` orders, not `build`$"):
            made_up.orders.build(command_center, UnitTypeId.SCV)

    def test_a_unit_that_trains_nothing_is_rejected_by_the_type_checker(self) -> None:
        _, (made_up_marine,) = _made_up(UnitReport(1, UnitTypeId.MARINE))
        marine: OwnUnit[UnitType.Marine] = made_up_marine

        with pytest.raises(ValueError, match="^a MARINE does not make a MARINE: a BARRACKS does$"):
            marine.train(UnitTypeId.MARINE)  # type: ignore

    def test_no_unit_to_order_raises_as_issue_does(self) -> None:
        made_up = MadeUpUnits()

        with pytest.raises(ValueError, match="an order needs a unit to give it to"):
            made_up.orders.train([], UnitTypeId.MARINE)

    def test_a_worker_builds_a_structure_at_a_site_and_a_structure_its_add_on(self) -> None:
        _, (scv, drone, geyser, barracks, flying_barracks) = _made_up(
            UnitReport(1, UnitTypeId.SCV),
            UnitReport(2, UnitTypeId.DRONE),
            UnitReport(3, UnitTypeId.VESPENE_GEYSER, alliance=Alliance.NEUTRAL, vespene_contents=2250),
            UnitReport(4, UnitTypeId.BARRACKS),
            UnitReport(5, UnitTypeId.BARRACKS_FLYING, is_flying=True),
        )

        depot = scv.build(UnitTypeId.SUPPLY_DEPOT, target=(30.0, 31.0))
        extractor = drone.build(UnitTypeId.EXTRACTOR, target=geyser)
        tech_lab = barracks.build(UnitTypeId.TECH_LAB_BARRACKS)
        landing_reactor = flying_barracks.build(UnitTypeId.REACTOR_BARRACKS, target=(40.5, 41.5))

        assert (depot.ability, depot.target) == (AbilityId.SCV_BUILD_SUPPLY_DEPOT, Point((30.0, 31.0)))
        assert (extractor.ability, extractor.target) == (AbilityId.DRONE_MORPH_EXTRACTOR, geyser)
        assert (tech_lab.ability, tech_lab.target) == (AbilityId.BUILD_TECH_LAB, None)
        assert (landing_reactor.ability, landing_reactor.target) == (AbilityId.BUILD_REACTOR, Point((40.5, 41.5)))

    def test_a_build_raises_as_issue_does(self) -> None:
        """Two workers at one site, and a build aimed at nothing."""
        made_up, (scv, other_scv) = _made_up(UnitReport(1, UnitTypeId.SCV), UnitReport(2, UnitTypeId.SCV))

        with pytest.raises(ValueError, match="only one of them can carry it out there"):
            made_up.orders.build([scv, other_scv], UnitTypeId.SUPPLY_DEPOT, target=(30.0, 31.0))
        with pytest.raises(TypeError, match="SCV_BUILD_SUPPLY_DEPOT"):
            scv.build(UnitTypeId.SUPPLY_DEPOT)

    def test_a_researcher_researches_an_upgrade_with_its_research_ability(self) -> None:
        made_up, (tech_lab, lair) = _made_up(
            UnitReport(1, UnitTypeId.TECH_LAB_BARRACKS), UnitReport(2, UnitTypeId.LAIR)
        )

        stimpack = tech_lab.research(UpgradeId.STIMPACK)
        burrow = made_up.orders.research(lair, UpgradeId.BURROW)

        assert (stimpack.ability, stimpack.units) == (AbilityId.BARRACKS_TECH_LAB_RESEARCH_STIMPACK, Units([tech_lab]))
        assert burrow.ability is AbilityId.LAIR_RESEARCH_BURROW

    def test_a_unit_not_researching_the_upgrade_raises_naming_what_does(self) -> None:
        """A barracks's upgrades are its tech lab's."""
        _, (barracks, spawning_pool) = _made_up(
            UnitReport(1, UnitTypeId.BARRACKS), UnitReport(2, UnitTypeId.SPAWNING_POOL)
        )

        with pytest.raises(ValueError, match="^STIMPACK is researched by a TECH_LAB_BARRACKS, not a BARRACKS$"):
            barracks.research(UpgradeId.STIMPACK)
        with pytest.raises(
            ValueError, match="^BURROW is researched by a HATCHERY, a HIVE or a LAIR, not a SPAWNING_POOL$"
        ):
            spawning_pool.research(UpgradeId.BURROW)

    def test_queued_and_force_are_passed_on(self) -> None:
        made_up, (barracks, tech_lab) = _made_up(
            UnitReport(1, UnitTypeId.BARRACKS), UnitReport(2, UnitTypeId.TECH_LAB_BARRACKS)
        )

        marine = barracks.train(UnitTypeId.MARINE, queued=True, force=True)
        stimpack = tech_lab.research(UpgradeId.STIMPACK, queued=True, force=True)

        assert marine.queued and stimpack.queued
        assert {marine, stimpack} <= made_up.orders._forced

    @pytest.mark.parametrize(("method", "made_through"), [("train", "units"), ("build", "units"), ("research", "unit")])
    def test_the_unit_s_method_takes_the_book_s_arguments_but_the_units(self, method: str, made_through: str) -> None:
        """So that an argument added to the book's method is not left out of the unit's."""
        book_parameters = list(inspect.signature(getattr(OrderBook, method)).parameters.values())[1:]
        unit_parameters = list(inspect.signature(getattr(OwnUnit, method)).parameters.values())[1:]
        assert [parameter for parameter in book_parameters if parameter.name != made_through] == unit_parameters

    def test_each_maker_makes_each_type_with_one_ability(self) -> None:
        """What the lookup `train` and `build` read rests on: no maker makes a type with two abilities."""
        products = [
            (maker, product)
            for row in load_game_data().abilities.values()
            for maker, product in row.products.items()
            if isinstance(product, UnitTypeId)
        ]
        assert len(products) == len(set(products))

    def test_train_orders_trains_warp_ins_and_a_larva_s_morphs_and_build_builds_and_add_ons(self) -> None:
        """What the two methods order, by the game's names for it, over every type the game makes."""
        book = MadeUpUnits().orders
        trained = {ability.name for ability in book._trained_abilities.values()}
        built = {ability.name for ability in book._built_abilities.values()}
        assert all(any(part in name for part in ("_TRAIN_", "_WARP_IN_", "LARVA_MORPH_")) for name in trained)
        assert all(name.startswith(("SCV_BUILD_", "PROBE_BUILD_", "DRONE_MORPH_", "BUILD_")) for name in built)
        assert {"BUILD_CREEP_TUMOR", "RAVEN_SPAWN_AUTO_TURRET", "NYDUS_NETWORK_BUILD_NYDUS_WORM"}.isdisjoint(built)
        assert (len(trained), len({*book._built_abilities})) == (53, 54)


class TestOneOrderAUnitATurn:
    def test_the_last_order_a_unit_was_given_is_the_one_sent(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.book.issue(game.own(1), _MOVE, target=(30.0, 31.0))

        (command,) = _commands(game.flush())

        assert command.target_world_space_pos.x == 30.0

    def test_an_ability_carried_out_at_once_neither_overrides_nor_is_overridden(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        game.book.issue(game.own(1), _STIM)
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        sent = [command.ability_id for command in _commands(game.flush())]

        assert sent == [_STIM, _MOVE]

    def test_an_order_keeps_the_units_a_later_order_did_not_take(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2))
        game.book.issue([game.own(1), game.own(2)], _MOVE, target=(20.0, 21.0))
        game.book.issue(game.own(2), _ATTACK, target=(30.0, 31.0))

        move, attack = _commands(game.flush())

        assert list(move.unit_tags) == [1]
        assert list(attack.unit_tags) == [2]

    def test_an_order_overridden_for_every_unit_is_never_sent(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2))
        game.book.issue([game.own(1), game.own(2)], _MOVE, target=(20.0, 21.0))
        game.book.issue([game.own(2), game.own(1)], _ATTACK, target=(30.0, 31.0))

        (command,) = _commands(game.flush())
        assert command.ability_id == _ATTACK

    def test_issued_to_says_what_the_turn_has_already_ordered_a_unit_and_is_empty_the_next(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2))
        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        assert game.book.issued_to(game.own(1)) == (order,)
        assert game.book.issued_to(game.own(2)) == ()
        assert game.book.pending == (order,)

        game.flush()
        game.observe(16, _marine(1), _marine(2))

        assert game.book.issued_to(game.own(1)) == ()
        assert game.book.pending == ()

    def test_include_sent_keeps_an_order_sent_while_the_observations_may_not_show_it(self) -> None:
        """The game may show an order an observation late, as on the ladder."""
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()
        game.observe(16, _marine(1))

        assert game.book.issued_to(game.own(1)) == ()
        assert game.book.issued_to(game.own(1), include_sent=True) == (order,)

        game.observe(32, _marine(1, _moving((20.0, 21.0))))

        assert game.book.issued_to(game.own(1), include_sent=True) == ()

    def test_include_sent_keeps_an_order_sent_queued_while_the_observations_may_not_show_it(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0))))
        order = game.book.issue(game.own(1), _MOVE, target=(30.0, 31.0), queued=True, force=True)
        game.flush()
        game.observe(16, _marine(1, _moving((20.0, 21.0))))

        assert game.book.issued_to(game.own(1), include_sent=True) == (order,)

        game.observe(32, _marine(1, _moving((20.0, 21.0)), _moving((30.0, 31.0))))

        assert game.book.issued_to(game.own(1), include_sent=True) == ()

    def test_include_sent_keeps_a_train_sent_while_the_observations_may_not_show_it(self) -> None:
        """A train goes behind what the structure makes, so a handler that trains while `issued_to` and the
        structure's orders show nothing would pay for a second on the ladder."""
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _barracks(1))
        order = game.book.issue(game.own(1), _TRAIN_MARINE)
        game.flush()
        game.observe(16, _barracks(1))

        assert game.book.issued_to(game.own(1), include_sent=True) == (order,)

        game.observe(32, _barracks(1, _training()))

        assert game.book.issued_to(game.own(1), include_sent=True) == ()

    def test_include_sent_leaves_out_what_a_unit_carries_out_at_once(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _barracks(1))
        game.book.issue(game.own(1), _RALLY, target=(20.0, 21.0))
        game.flush()
        game.observe(16, _barracks(1))

        assert game.book.issued_to(game.own(1), include_sent=True) == ()


class TestAnOrderAUnitIsAlreadyCarryingOut:
    """An unqueued order equal to a unit's first is answered `SUCCESS` and carries nothing out, but drops what the
    unit had queued (in game)."""

    def test_it_is_not_sent(self) -> None:
        game = _Game()
        game.observe(0, _marine(1, _moving((20.0, 21.0)), _moving((30.0, 31.0))))

        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        assert game.flush() is None

    def test_it_counts_as_issued_until_the_turn_is_sent(self) -> None:
        game = _Game()
        game.observe(0, _marine(1, _moving((20.0, 21.0))))

        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        assert game.book.issued_to(game.own(1)) == (order,)
        assert game.book.pending == (order,)

    def test_each_turn_s_call_is_an_order_of_its_own(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        first = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()
        game.observe(16, _marine(1, _moving((20.0, 21.0))))

        second = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        assert second is not first
        assert game.flush() is None

    def test_it_takes_its_unit_from_the_turn_s_earlier_orders(self) -> None:
        game = _Game()
        game.observe(0, _marine(1, _moving((20.0, 21.0))))
        game.book.issue(game.own(1), _ATTACK, target=(30.0, 31.0))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        assert game.flush() is None

    def test_a_later_order_to_its_unit_overrides_it(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0))))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.book.issue(game.own(1), _ATTACK, target=(30.0, 31.0))

        (command,) = _commands(game.flush())

        assert command.ability_id == _ATTACK

    def test_withdrawing_it_lets_the_turn_s_earlier_order_go_out(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0))))
        game.book.issue(game.own(1), _ATTACK, target=(30.0, 31.0))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0)).withdraw()

        (command,) = _commands(game.flush())

        assert command.ability_id == _ATTACK

    def test_only_the_units_already_carrying_it_out_are_left_out(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0))), _marine(2))
        game.book.issue([game.own(1), game.own(2)], _MOVE, target=(20.0, 21.0))

        (command,) = _commands(game.flush())

        assert list(command.unit_tags) == [2]

    def test_a_point_the_game_cut_down_to_its_own_lattice_is_the_same_point(self) -> None:
        game = _Game()
        game.observe(0, _marine(1, _moving((157.123291, 3.0))))

        game.book.issue(game.own(1), _MOVE, target=(157.123456, 3.0))

        assert game.flush() is None

    def test_a_point_with_a_height_is_the_same_point(self) -> None:
        game = _Game()
        game.observe(0, _marine(1, _moving((20.0, 21.0))))

        game.book.issue(game.own(1), _MOVE, target=Point3D((20.0, 21.0, 5.0)))

        assert game.flush() is None

    def test_an_order_aimed_elsewhere_is_sent(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0))))

        game.book.issue(game.own(1), _MOVE, target=(30.0, 31.0))

        assert len(_commands(game.flush())) == 1

    def test_a_queued_order_is_sent(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0))))

        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0), queued=True)

        assert len(_commands(game.flush())) == 1

    def test_the_order_sent_last_turn_stands_for_what_the_unit_is_doing_until_it_can_show(self) -> None:
        """On the ladder the observation after an order can still show what the unit did before (in game)."""
        game = _Game([ActionResult.SUCCESS], [ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0))))
        game.book.issue(game.own(1), _ATTACK, target=(30.0, 31.0))
        game.flush()
        game.observe(16, _marine(1, _moving((20.0, 21.0))))

        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        (command,) = _commands(game.flush())
        assert command.ability_id == _MOVE

    def test_a_repeat_of_the_order_sent_last_turn_is_redundant_though_it_does_not_show_yet(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.book.issue(game.own(1), _ATTACK, target=(30.0, 31.0), queued=True)
        game.flush()
        game.observe(16, _marine(1))

        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        assert game.flush() is None

    def test_an_order_finished_within_a_turn_is_still_taken_for_what_the_unit_is_doing_on_the_next(self) -> None:
        """In a stepped game the next observation already shows the unit idle, but on the ladder it might not."""
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()
        game.observe(16, _marine(1, at=(20.0, 21.0)))

        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        assert game.flush() is None

    def test_two_observations_on_the_unit_s_own_orders_decide(self) -> None:
        game = _Game([ActionResult.SUCCESS], [ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()
        game.observe(16, _marine(1, _moving((20.0, 21.0))))
        game.observe(32, _marine(1, at=(20.0, 21.0)))

        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        assert len(_commands(game.flush())) == 1

    def test_an_order_the_game_gave_decides_once_the_last_one_sent_could_show(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2, at=(30.0, 30.0)))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()
        game.observe(16, _marine(1, _moving((20.0, 21.0))), _marine(2, at=(30.0, 30.0)))
        attacking = raw_pb2.UnitOrder(ability_id=_ATTACK, target_unit_tag=2)
        game.observe(32, _marine(1, attacking), _marine(2, at=(30.0, 30.0)))

        game.book.issue(game.own(1), _ATTACK, target=game.own(2))

        assert game.flush() is None

    def test_a_unit_aimed_by_a_tag_the_game_never_reports_carries_out_an_order_at_its_last_target(self) -> None:
        """As an SCV walking back to a field out of sight does (in game)."""
        game = _Game([ActionResult.SUCCESS])
        attacking = raw_pb2.UnitOrder(ability_id=_ATTACK, target_unit_tag=2)
        game.observe(0, _marine(1, attacking), _marine(2, at=(30.0, 30.0)))
        unreported = raw_pb2.UnitOrder(ability_id=_ATTACK, target_unit_tag=99)
        game.observe(16, _marine(1, unreported), _marine(2, at=(30.0, 30.0)))
        game.observe(32, _marine(1, unreported), _marine(2, at=(30.0, 30.0)))

        game.book.issue(game.own(1), _ATTACK, target=game.own(2))

        assert game.flush() is None

    def test_a_dead_unit_s_last_order_is_forgotten(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()
        unit = game.own(1)

        game.observe(16, _marine(2), dead=(1,))

        assert unit.id not in game.book._last_sent

    def test_an_order_to_a_unit_the_observation_left_out_is_sent(self) -> None:
        """A marine in a transport is left out of the observation, though it is not dead."""
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0))), _marine(2))
        marine = game.own(1)
        game.observe(16, _marine(2))

        game.book.issue(marine, _MOVE, target=(20.0, 21.0))

        assert len(_commands(game.flush())) == 1
        assert not marine.is_dead


class TestSieging:
    """`SIEGE` goes out as each type's own siege, aimed at nothing: a tank's siege mode, an observer's surveillance
    mode, an overseer's oversight mode. A liberator's is `LIBERATOR_SIEGE_AT`, its defender mode aimed at its zone:
    ordered it at a point, it is a sieged liberator by the next observation, and reports
    `LiberatorMorphtoAG_LiberatorAGMode` aimed at itself while its zone forms; ordered it again, it is refused
    `NotSupported` (in game). `UNSIEGE` goes out as each of the four's own."""

    def test_a_sieged_liberator_reads_as_carrying_out_its_siege(self) -> None:
        game = _Game()
        game.observe(0, _sieged_liberator(1))

        (order,) = game.own(1).orders

        assert order.ability is _SIEGE_AT
        assert order.target is game.own(1)

    def test_the_siege_again_is_sent_since_it_was_aimed_at_a_point_and_shows_aimed_at_the_liberator(self) -> None:
        game = _Game([ActionResult.NOT_SUPPORTED])
        game.observe(0, _sieged_liberator(1))

        game.book.issue(game.own(1), _SIEGE_AT, target=(20.0, 21.0))

        (command,) = _commands(game.flush())
        assert command.ability_id == RawAbilityId.Morph_LiberatorAGMode
        assert game.refused() == [(1, _SIEGE_AT, ActionResult.NOT_SUPPORTED)]

    def test_a_liberator_takes_its_own_siege_at_a_point_and_not_the_siege(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.SIEGE_TANK), make_unit(2, UnitTypeId.LIBERATOR))

        with pytest.raises(ValueError, match="SIEGE has nothing to be sent as for LIBERATOR"):
            game.book.issue([game.own(1), game.own(2)], _SIEGE)
        game.book.issue(game.own(2), _SIEGE_AT, target=(20.0, 21.0))

        (command,) = _commands(game.flush())
        assert (command.ability_id, list(command.unit_tags)) == (RawAbilityId.Morph_LiberatorAGMode, [2])
        assert (command.target_world_space_pos.x, command.target_world_space_pos.y) == (20.0, 21.0)

    def test_two_liberators_are_given_a_siege_each_and_two_tanks_one_together(self) -> None:
        """One of two liberators given one siege at a point sieges, and both of two tanks (tool `sweep_orders`,
        `group-abilities-terran`)."""
        game = _Game([ActionResult.SUCCESS] * 3)
        game.observe(
            0,
            *(make_unit(tag, UnitTypeId.LIBERATOR) for tag in (1, 2)),
            *(make_unit(tag, UnitTypeId.SIEGE_TANK) for tag in (3, 4)),
        )

        game.book.issue([game.own(1), game.own(2)], _SIEGE_AT, target=(20.0, 21.0))
        game.book.issue([game.own(3), game.own(4)], _SIEGE)

        assert [list(command.unit_tags) for command in _commands(game.flush())] == [[1], [2], [3, 4]]

    def test_a_tank_alone_needs_no_point(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.SIEGE_TANK))

        game.book.issue(game.own(1), _SIEGE)

        (command,) = _commands(game.flush())
        assert command.ability_id == RawAbilityId.SiegeMode_SiegeMode

    def test_a_liberator_s_siege_needs_a_point(self) -> None:
        game = _Game()
        game.observe(0, make_unit(1, UnitTypeId.LIBERATOR))

        with pytest.raises(TypeError, match="LIBERATOR_SIEGE_AT"):
            game.book.issue(game.own(1), _SIEGE_AT)

    def test_a_type_with_no_siege_is_refused_at_the_call(self) -> None:
        game = _Game()
        game.observe(0, _marine(1))

        with pytest.raises(ValueError, match="SIEGE has nothing to be sent as for MARINE"):
            game.book.issue(game.own(1), _SIEGE)

    def test_a_game_whose_tables_lack_a_siege_refuses_it_at_the_call(self) -> None:
        game = _Game(tables=make_tables(data_pb2.UnitTypeData(unit_id=UnitTypeId.SIEGE_TANK)))
        game.observe(0, make_unit(1, UnitTypeId.SIEGE_TANK))

        with pytest.raises(ValueError, match="SIEGE has nothing in this game's tables to be sent as"):
            game.book.issue(game.own(1), _SIEGE)

    def test_the_unsiege_goes_out_as_each_type_s_own_aimed_at_nothing(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.SIEGE_TANK_SIEGED), _sieged_liberator(2))

        game.book.issue([game.own(1), game.own(2)], _UNSIEGE)

        sent = [(command.ability_id, list(command.unit_tags)) for command in _commands(game.flush())]
        assert sent == [(RawAbilityId.Unsiege_Unsiege, [1]), (RawAbilityId.Morph_LiberatorAAMode, [2])]


def _sieged_liberator(tag: int) -> raw_pb2.Unit:
    """One of this player's liberators, sieged, showing the order it reports while its zone forms."""
    siege = raw_pb2.UnitOrder(ability_id=RawAbilityId.LiberatorMorphtoAG_LiberatorAGMode, target_unit_tag=tag)
    return make_unit(tag, UnitTypeId.LIBERATOR_SIEGED, at=(18.0, 21.0), orders=(siege,))


class TestAGroupOfSeveralTypes:
    """Hold fire keeps a ghost's orders and replaces a burrowed lurker's (in game)."""

    def test_it_leaves_a_unit_it_keeps_the_orders_of_the_order_it_was_given_before(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.GHOST), make_unit(2, UnitTypeId.LURKER_BURROWED))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.book.issue([game.own(1), game.own(2)], _HOLD_FIRE)

        sent_move, sent_hold = _commands(game.flush())

        assert list(sent_move.unit_tags) == [1]
        assert list(sent_hold.unit_tags) == [1, 2]

    def test_it_takes_a_unit_it_replaces_the_orders_of_from_the_order_it_was_given_before(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.GHOST), make_unit(2, UnitTypeId.LURKER_BURROWED))
        game.book.issue(game.own(2), _ATTACK, target=(20.0, 21.0))
        game.book.issue([game.own(1), game.own(2)], _HOLD_FIRE)

        (sent,) = _commands(game.flush())

        assert sent.ability_id == _HOLD_FIRE

    def test_a_later_order_takes_only_the_units_whose_orders_it_replaces(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.GHOST), make_unit(2, UnitTypeId.LURKER_BURROWED))
        game.book.issue([game.own(1), game.own(2)], _ATTACK, target=(20.0, 21.0))
        game.book.issue([game.own(1), game.own(2)], _HOLD_FIRE)

        sent_attack, sent_hold = _commands(game.flush())

        assert list(sent_attack.unit_tags) == [1]
        assert list(sent_hold.unit_tags) == [1, 2]

    def test_a_queens_creep_tumor_replaces_her_orders_though_a_tumors_does_not(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.QUEEN))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.book.issue(game.own(1), _CREEP_TUMOR, target=(30.0, 31.0))

        (sent,) = _commands(game.flush())

        assert sent.ability_id == _CREEP_TUMOR


def _medivac(tag: int, *orders: raw_pb2.UnitOrder) -> raw_pb2.Unit:
    """One of this player's medivacs, carrying out `orders`."""
    return make_unit(tag, UnitTypeId.MEDIVAC, at=(14.0, 14.0), orders=orders)


class TestUnloadingWhereTheTransportIs:
    """`UNLOAD` puts everyone down where the transport is: a bunker takes it as it is, and a medivac, which
    answers it `Error`, as its unload at a point aimed at itself, which unloads where it is and keeps its move (in
    game)."""

    def test_a_medivac_is_sent_the_general_unload_at_aimed_at_itself(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _medivac(1))

        order = game.book.issue(game.own(1), _UNLOAD)

        (command,) = _commands(game.flush())
        assert command.ability_id == _UNLOAD_AT
        assert command.target_unit_tag == 1
        assert list(command.unit_tags) == [1]
        assert order.ability is _UNLOAD
        assert order.target is None

    def test_a_bunker_and_a_medivac_go_out_each_as_it_takes_it(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.BUNKER), _medivac(2))

        game.book.issue([game.own(1), game.own(2)], _UNLOAD)

        sent = [(c.ability_id, list(c.unit_tags), c.target_unit_tag) for c in _commands(game.flush())]
        assert sent == [(_UNLOAD, [1], 0), (_UNLOAD_AT, [2], 2)]

    def test_a_move_in_the_same_turn_goes_out_too(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _medivac(1))

        game.book.issue(game.own(1), _MOVE, target=(30.0, 31.0))
        game.book.issue(game.own(1), _UNLOAD)

        assert [command.ability_id for command in _commands(game.flush())] == [_MOVE, _UNLOAD_AT]

    def test_a_group_is_sent_one_command_a_transport_and_each_refusal_is_listed(self) -> None:
        game = _Game([ActionResult.ERROR, ActionResult.SUCCESS, ActionResult.ERROR])
        game.observe(0, _medivac(1), _medivac(2), _medivac(3))

        game.book.issue([game.own(1), game.own(2), game.own(3)], _UNLOAD)

        commands = _commands(game.flush())
        assert [(command.ability_id, list(command.unit_tags), command.target_unit_tag) for command in commands] == [
            (_UNLOAD_AT, [1], 1),
            (_UNLOAD_AT, [2], 2),
            (_UNLOAD_AT, [3], 3),
        ]
        assert game.refused() == [(1, _UNLOAD, ActionResult.ERROR), (3, _UNLOAD, ActionResult.ERROR)]

    def test_each_transport_that_refuses_is_listed_with_its_own_answer(self) -> None:
        game = _Game([ActionResult.NOT_SUPPORTED, ActionResult.ERROR])
        game.observe(0, _medivac(1), _medivac(2))

        game.book.issue([game.own(1), game.own(2)], _UNLOAD)
        game.flush()

        assert game.refused() == [(1, _UNLOAD, ActionResult.NOT_SUPPORTED), (2, _UNLOAD, ActionResult.ERROR)]

    def test_an_unload_at_a_sibling_transport_is_fine_for_a_transport_ordered_alone(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _medivac(1), _medivac(2))

        game.book.issue(game.own(1), _UNLOAD_AT, target=game.own(2))

        (command,) = _commands(game.flush())
        assert command.target_unit_tag == 2

    def test_an_order_after_it_is_answered_by_its_own_result(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS, ActionResult.NOT_SUPPORTED])
        game.observe(0, _medivac(1), _medivac(2), _marine(3))

        game.book.issue([game.own(1), game.own(2)], _UNLOAD)
        game.book.issue(game.own(3), _STIM)

        assert len(_commands(game.flush())) == 3
        assert game.refused() == [(3, _STIM, ActionResult.NOT_SUPPORTED)]

    def test_an_unload_at_aimed_at_the_transport_itself_is_refused_at_the_call(self) -> None:
        game = _Game()
        game.observe(0, _medivac(1), _medivac(2))

        with pytest.raises(TypeError, match="UNLOAD_AT aimed at the unit itself is UNLOAD"):
            game.book.issue([game.own(1), game.own(2)], _UNLOAD_AT, target=game.own(2))

    def test_an_unload_at_a_point_still_replaces_a_move(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _medivac(1))

        game.book.issue(game.own(1), _MOVE, target=(30.0, 31.0))
        game.book.issue(game.own(1), _UNLOAD_AT, target=(20.0, 21.0))

        (command,) = _commands(game.flush())
        assert command.ability_id == _UNLOAD_AT


class TestForcingAnOrder:
    """`force` sends an order to a unit already carrying it out, which drops what it has queued (in game)."""

    def test_an_order_the_unit_is_already_carrying_out_is_sent(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0)), _moving((30.0, 31.0))))

        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0), force=True)

        (command,) = _commands(game.flush())
        assert (command.target_world_space_pos.x, command.target_world_space_pos.y) == (20.0, 21.0)
        assert not command.queue_command

    def test_the_order_sent_last_turn_is_sent_again_though_it_does_not_show_yet(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS], [ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.book.issue(game.own(1), _MOVE, target=(30.0, 31.0), queued=True)
        game.flush()
        game.observe(16, _marine(1))

        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0), force=True)

        assert len(_commands(game.flush())) == 1

    def test_every_unit_of_a_group_is_sent_it(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0))), _marine(2))

        game.book.issue([game.own(1), game.own(2)], _MOVE, target=(20.0, 21.0), force=True)

        (command,) = _commands(game.flush())
        assert list(command.unit_tags) == [1, 2]

    def test_a_later_order_still_overrides_it(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0)), _moving((30.0, 31.0))))

        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0), force=True)
        game.book.issue(game.own(1), _ATTACK, target=(40.0, 41.0))

        (command,) = _commands(game.flush())
        assert command.ability_id == _ATTACK

    def test_it_is_sent_once_for_its_turn_only(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0))))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0), force=True)
        game.flush()
        game.observe(16, _marine(1, _moving((20.0, 21.0))))
        game.observe(32, _marine(1, _moving((20.0, 21.0))))

        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        assert game.flush() is None


class TestWhatTheGameRefused:
    def test_a_refused_order_is_listed_with_the_ability_ordered_and_the_game_s_answer(self) -> None:
        game = _Game([ActionResult.NOT_SUPPORTED])
        game.observe(16, _marine(1))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        game.flush()

        assert game.refused() == [(1, _MOVE, ActionResult.NOT_SUPPORTED)]
        (failure,) = game.book._refusals
        assert failure.time == GameTime(steps=16)

    def test_a_refused_command_is_listed_once_for_each_unit_it_named(self) -> None:
        game = _Game([ActionResult.ERROR])
        game.observe(0, _marine(1), _marine(2))
        game.book.issue([game.own(1), game.own(2)], _MOVE, target=(20.0, 21.0))

        game.flush()

        assert game.refused() == [(1, _MOVE, ActionResult.ERROR), (2, _MOVE, ActionResult.ERROR)]

    def test_refusals_last_until_the_next_turn_is_sent(self) -> None:
        game = _Game([ActionResult.NOT_SUPPORTED])
        game.observe(0, _marine(1))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()
        game.observe(16, _marine(1))

        assert len(game.refused()) == 1
        assert game.flush() is None
        assert game.refused() == []

    def test_an_action_of_no_order_the_game_refuses_is_no_order_s_refusal(self) -> None:
        game = _Game([ActionResult.ERROR])
        game.observe(0, _marine(1))

        game.flush(then=[_CHAT])

        assert game.refused() == []

    def test_a_refused_order_is_not_taken_for_what_its_unit_is_doing(self) -> None:
        game = _Game([ActionResult.NOT_SUPPORTED], [ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()
        game.observe(16, _marine(1))

        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        assert len(_commands(game.flush())) == 1

    def test_only_the_units_of_a_command_taken_are_doing_it(self) -> None:
        """A sieged tank and a sieged liberator are each sent their own unsiege, and here the game takes only the
        tank's."""
        game = _Game([ActionResult.SUCCESS, ActionResult.NOT_SUPPORTED], [ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.SIEGE_TANK_SIEGED), _sieged_liberator(2))
        game.book.issue([game.own(1), game.own(2)], _UNSIEGE)
        game.flush()
        game.observe(16, make_unit(1, UnitTypeId.SIEGE_TANK_SIEGED), _sieged_liberator(2))

        game.book.issue([game.own(1), game.own(2)], _UNSIEGE)

        (command,) = _commands(game.flush())
        assert list(command.unit_tags) == [2]


class TestOrdersThatQueue:
    """A train queues behind what a structure is making, so it is neither a duplicate nor a replacement (in game)."""

    def test_a_train_is_sent_though_the_structure_is_already_making_one(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _barracks(1, _training()))

        game.book.issue(game.own(1), _TRAIN_MARINE)

        (command,) = _commands(game.flush())
        assert command.ability_id == _TRAIN_MARINE


class TestATargetOffTheGround:
    def test_a_height_is_left_behind(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))

        order = game.book.issue(game.own(1), _MOVE, target=Point3D((20.0, 21.0, 5.0)))

        assert order.target == Point((20.0, 21.0))
        (command,) = _commands(game.flush())
        assert (command.target_world_space_pos.x, command.target_world_space_pos.y) == (20.0, 21.0)


class TestWhatAStructureDoesBesidesMaking:
    """A structure keeps making what it is making when given a rally or a cancel (in game)."""

    def test_an_unload_does_not_take_the_turn_from_a_train(self) -> None:
        """A command center is offered unload only while it carries something, so the tables offer its own unload to
        nobody; it keeps orders as a bunker's does."""
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.COMMAND_CENTER, at=(12.0, 12.0)))
        game.book.issue(game.own(1), _TRAIN_SCV)
        game.book.issue(game.own(1), _UNLOAD)

        sent = [command.ability_id for command in _commands(game.flush())]

        assert sent == [_TRAIN_SCV, _UNLOAD]

    def test_a_rally_does_not_take_the_turn_from_a_train(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _barracks(1))
        game.book.issue(game.own(1), _TRAIN_MARINE)
        game.book.issue(game.own(1), _RALLY, target=(20.0, 21.0))

        sent = [command.ability_id for command in _commands(game.flush())]

        assert sent == [_TRAIN_MARINE, _RALLY]

    def test_a_smart_does_not_take_the_turn_from_a_train(self) -> None:
        """A structure goes on training through a smart, which sets its rally (in game)."""
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _barracks(1))
        game.book.issue(game.own(1), _TRAIN_MARINE)
        game.book.issue(game.own(1), _SMART, target=(20.0, 21.0))

        sent = [command.ability_id for command in _commands(game.flush())]

        assert sent == [_TRAIN_MARINE, _SMART]


class TestAQueuedOrder:
    def test_a_queued_order_goes_out_beside_the_unqueued_one_it_follows(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.book.issue(game.own(1), _ATTACK, target=(30.0, 31.0), queued=True)

        first, second = _commands(game.flush())

        assert (first.ability_id, first.queue_command) == (_MOVE, False)
        assert (second.ability_id, second.queue_command) == (_ATTACK, True)

    def test_an_unqueued_order_after_a_queued_one_takes_the_unit_from_nothing(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        game.book.issue(game.own(1), _ATTACK, target=(30.0, 31.0), queued=True)
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        assert len(_commands(game.flush())) == 2


class TestAPointAsTheGameReadsIt:
    def test_a_target_is_kept_as_the_protocol_carries_it(self) -> None:
        """A coordinate goes out as a 32-bit float, so the point an order holds is the one the game reports back."""
        game = _Game()
        # Rounded to 1/4096 this reads one step lower than the 32-bit float the game is given.
        crossing = 157.123288015625
        game.observe(0, _marine(1))

        order = game.book.issue(game.own(1), _MOVE, target=(crossing, 3.0))

        assert order.target == Point((157.123291015625, 3.0))

    def test_two_targets_are_the_same_exactly_when_the_game_keeps_them_as_one(self) -> None:
        game = _Game()
        game.observe(0, _marine(1), _marine(2))
        points = [aimed_at((20.0 + shift, 21.0)) for shift in (0.0, POINT_PRECISION / 2, -POINT_PRECISION / 2, 0.5)]
        targets = [None, game.own(1), game.own(2), *points]

        for one in targets:
            for other in targets:
                assert same_target(one, other) == (target_as_kept(one) == target_as_kept(other)), (one, other)
        assert same_target(points[0], points[1])
        assert not same_target(points[0], points[2])

    def test_an_order_at_it_matches_the_point_the_game_reports(self) -> None:
        game = _Game()
        crossing = 157.123288015625
        game.observe(0, _marine(1, _moving((crossing, 3.0))))

        game.book.issue(game.own(1), _MOVE, target=(crossing, 3.0))

        assert game.flush() is None


class TestWhatAnOrderStopsCountingFor:
    def test_an_order_withdrawn_before_the_turn_ends_is_never_sent(self) -> None:
        game = _Game()
        game.observe(0, _marine(1))
        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        order.withdraw()

        assert game.flush() is None

    def test_a_withdrawn_order_speaks_for_no_unit(self) -> None:
        game = _Game()
        game.observe(0, _marine(1))
        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        assert game.book.issued_to(game.own(1)) == (order,)

        order.withdraw()

        assert game.book.issued_to(game.own(1)) == ()
        assert game.book.pending == ()


class TestTellingAStructureToMakeTwo:
    def test_a_second_train_is_sent_when_the_bot_asks_for_a_place_in_the_queue(self) -> None:
        """This is how a structure with a reactor is told to make two at once (in game)."""
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _barracks(1))
        game.book.issue(game.own(1), _TRAIN_MARINE)
        game.book.issue(game.own(1), _TRAIN_MARINE, queued=True)

        one, two = _commands(game.flush())

        assert (one.ability_id, one.queue_command) == (_TRAIN_MARINE, False)
        assert (two.ability_id, two.queue_command) == (_TRAIN_MARINE, True)

    def test_a_second_unqueued_train_takes_the_structure_from_the_first(self) -> None:
        """The game would queue it and pay for it from the step it was ordered."""
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _barracks(1))
        game.book.issue(game.own(1), _TRAIN_MARINE)
        game.book.issue(game.own(1), _TRAIN_REAPER)

        (command,) = _commands(game.flush())

        assert command.ability_id == _TRAIN_REAPER


# Orders that cost. These tables give them a cost, as the game's do.
_BUILD_DEPOT = AbilityId.SCV_BUILD_SUPPLY_DEPOT
_BUILD_REACTOR = AbilityId.BUILD_REACTOR
_DRONE = AbilityId.LARVA_MORPH_DRONE
_STORM = AbilityId.HIGH_TEMPLAR_STORM
_BUILD_REFINERY = AbilityId.SCV_BUILD_REFINERY
_RESEARCH = AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_1
_WARP_IN_ZEALOT = AbilityId.WARP_GATE_WARP_IN_ZEALOT
_SITE = (30.0, 30.0)


def _row(
    unit_type: UnitTypeId, *, structure: bool = False, minerals: int = 0, vespene: int = 0
) -> data_pb2.UnitTypeData:
    """A unit type's row, with what it costs."""
    attribute = data_pb2.Attribute.Structure if structure else data_pb2.Attribute.Mechanical
    return data_pb2.UnitTypeData(unit_id=unit_type, attributes=[attribute], mineral_cost=minerals, vespene_cost=vespene)


_COSTING_TABLES = make_tables(
    _row(UnitTypeId.SCV, minerals=50),
    _row(UnitTypeId.MARINE, minerals=50),
    _row(UnitTypeId.REAPER, minerals=50, vespene=50),
    _row(UnitTypeId.LARVA),
    _row(UnitTypeId.DRONE, minerals=50),
    _row(UnitTypeId.HATCHERY, structure=True, minerals=300),
    _row(UnitTypeId.SUPPLY_DEPOT, structure=True, minerals=100),
    _row(UnitTypeId.BARRACKS, structure=True, minerals=150),
    _row(UnitTypeId.BARRACKS_FLYING, structure=True, minerals=150),
    _row(UnitTypeId.REACTOR_BARRACKS, structure=True, minerals=50, vespene=50),
    abilities=[
        data_pb2.AbilityData(ability_id=_MOVE, target=_AT_A_POINT_OR_UNIT),
        data_pb2.AbilityData(ability_id=_BUILD_DEPOT, target=_AT_A_POINT, is_building=True),
        data_pb2.AbilityData(
            ability_id=_BUILD_REACTOR, target=data_pb2.AbilityData.Target.PointOrNone, is_building=True
        ),
        data_pb2.AbilityData(ability_id=_TRAIN_MARINE),
        data_pb2.AbilityData(ability_id=_TRAIN_REAPER),
        data_pb2.AbilityData(ability_id=_DRONE),
        data_pb2.AbilityData(ability_id=_STORM, target=_AT_A_POINT),
        data_pb2.AbilityData(ability_id=_BUILD_REFINERY, target=data_pb2.AbilityData.Target.Unit, is_building=True),
        data_pb2.AbilityData(ability_id=_RESEARCH),
        data_pb2.AbilityData(ability_id=AbilityId.LAND, target=_AT_A_POINT),
        data_pb2.AbilityData(ability_id=AbilityId.ROOT, target=_AT_A_POINT),
        data_pb2.AbilityData(ability_id=_WARP_IN_ZEALOT, target=_AT_A_POINT),
    ],
)


def _costing(*verdicts: list[ActionResult]) -> _Game:
    """A game whose tables give what is ordered a cost."""
    return _Game(*verdicts, tables=_COSTING_TABLES)


def _scv(tag: int, *orders: raw_pb2.UnitOrder, at: tuple[float, float] = (10.0, 10.0)) -> raw_pb2.Unit:
    """One of this player's SCVs, carrying out `orders`."""
    return make_unit(tag, UnitTypeId.SCV, at=at, orders=orders)


def _building(site: tuple[float, float] = _SITE) -> raw_pb2.UnitOrder:
    """The order an SCV shows while it goes to build a depot at `site`, or builds it."""
    return raw_pb2.UnitOrder(ability_id=_BUILD_DEPOT, target_world_space_pos=common_pb2.Point(x=site[0], y=site[1]))


def _templar(tag: int) -> raw_pb2.Unit:
    """One of this player's high templar."""
    return make_unit(tag, UnitTypeId.HIGH_TEMPLAR, at=(15.0, 15.0))


def _flying_barracks(tag: int, at: tuple[float, float] = (20.0, 20.0)) -> raw_pb2.Unit:
    """One of this player's barracks, lifted."""
    return make_unit(tag, UnitTypeId.BARRACKS_FLYING, at=at, is_flying=True)


def _sent(request: sc2api_pb2.RequestAction | None) -> list[tuple[int, list[int], tuple[float, float] | None, bool]]:
    """Each unit command of a request that went out: its ability, its units, the point it is aimed at, and whether it
    is queued."""
    sent: list[tuple[int, list[int], tuple[float, float] | None, bool]] = []
    for command in _commands(request):
        point = command.target_world_space_pos if command.HasField("target_world_space_pos") else None
        at = None if point is None else (point.x, point.y)
        sent.append((command.ability_id, list(command.unit_tags), at, command.queue_command))
    return sent


class TestAnOrderThatCosts:
    """Every order goes out in the turn it was given, as given, though the game charges it then (in game)."""

    def test_a_build_goes_out_at_once_however_far_its_worker_is_from_the_site(self) -> None:
        game = _costing([ActionResult.SUCCESS])
        game.observe(0, _scv(1))

        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)

        assert _sent(game.flush()) == [(_BUILD_DEPOT, [1], _SITE, False)]

    def test_a_flying_barracks_given_an_add_on_at_a_point_is_sent_it_as_given(self) -> None:
        """The game lands it there and builds the add-on (in game)."""
        game = _costing([ActionResult.SUCCESS])
        game.observe(0, _flying_barracks(1))

        game.book.issue(game.own(1), _BUILD_REACTOR, target=_SITE)

        assert _sent(game.flush()) == [(_BUILD_REACTOR, [1], _SITE, False)]

    def test_an_add_on_with_no_point_to_a_flying_barracks_is_a_mistake(self) -> None:
        game = _costing()
        game.observe(0, _flying_barracks(1))

        with pytest.raises(TypeError, match="BUILD_REACTOR takes a point for a flying BARRACKS_FLYING"):
            game.book.issue(game.own(1), _BUILD_REACTOR)

    def test_a_train_goes_out_to_a_structure_making_one(self) -> None:
        game = _costing([ActionResult.SUCCESS])
        game.observe(0, _barracks(1, _training()))

        game.book.issue(game.own(1), _TRAIN_MARINE)

        assert _sent(game.flush()) == [(_TRAIN_MARINE, [1], None, False)]

    def test_a_build_its_worker_is_already_carrying_out_is_not_sent_again(self) -> None:
        """Sending it again would drop what the worker has queued behind it (in game)."""
        game = _costing()
        game.observe(0, _scv(1, _building(), _moving((5.0, 5.0)), at=(29.0, 29.0)))

        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)

        assert game.flush() is None

    def test_a_forced_build_is_sent_though_its_worker_is_carrying_it_out(self) -> None:
        game = _costing([ActionResult.SUCCESS])
        game.observe(0, _scv(1, _building(), _moving((5.0, 5.0)), at=(29.0, 29.0)))

        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE, force=True)

        assert _sent(game.flush()) == [(_BUILD_DEPOT, [1], _SITE, False)]


class TestAnOrderToSeveralUnits:
    """One command naming several units has only one of them carry out a build, a train, a research, a morph or a
    spell, and each unit given such an order in a command of its own carries it out where there is room for each
    (tool `sweep_orders`, `one-command-many-makers` and `one-command-each`)."""

    @pytest.mark.parametrize(
        ("make", "ability", "target"),
        [(_barracks, _TRAIN_MARINE, None), (_templar, _STORM, _SITE), (_barracks, _BUILD_REACTOR, None)],
        ids=["a train", "a spell", "an add-on where each stands"],
    )
    def test_an_order_one_unit_of_a_command_carries_out_goes_to_each_in_a_command_of_its_own(
        self, make: Any, ability: AbilityId, target: tuple[float, float] | None
    ) -> None:
        game = _costing([ActionResult.SUCCESS] * 3)
        game.observe(0, make(1), make(2), make(3))

        game.book.issue([game.own(1), game.own(2), game.own(3)], ability, target=target)

        assert _sent(game.flush()) == [(ability, [tag], target, False) for tag in (1, 2, 3)]

    def test_an_order_whose_ability_this_game_s_tables_lack_is_sent_as_its_ability_goes(self) -> None:
        """A move goes out as one command naming them all, and a train as a command to each, row or not."""
        game = _Game([ActionResult.SUCCESS] * 3, tables=make_tables())
        game.observe(0, _marine(1), _marine(2), _barracks(3), _barracks(4))

        game.book.issue([game.own(1), game.own(2)], _MOVE, target=_SITE)
        game.book.issue([game.own(3), game.own(4)], _TRAIN_MARINE)

        assert _sent(game.flush()) == [
            (_MOVE, [1, 2], _SITE, False),
            (_TRAIN_MARINE, [3], None, False),
            (_TRAIN_MARINE, [4], None, False),
        ]

    def test_a_move_to_several_goes_out_as_one_command(self) -> None:
        game = _costing([ActionResult.SUCCESS])
        game.observe(0, _scv(1), _scv(2))

        game.book.issue([game.own(1), game.own(2)], _MOVE, target=_SITE)

        assert _sent(game.flush()) == [(_MOVE, [1, 2], _SITE, False)]

    def test_a_unit_given_twice_counts_once(self) -> None:
        game = _costing([ActionResult.SUCCESS] * 2)
        game.observe(0, _barracks(1), _scv(2), _scv(3))

        train = game.book.issue([game.own(1), game.own(1)], _TRAIN_MARINE)
        game.book.issue([game.own(2), game.own(3), game.own(2)], _MOVE, target=_SITE)

        assert train.units == Units([game.own(1)])
        assert _sent(game.flush()) == [(_TRAIN_MARINE, [1], None, False), (_MOVE, [2, 3], _SITE, False)]

    def test_what_the_game_refuses_of_a_command_each_is_listed_for_its_unit(self) -> None:
        game = _costing([ActionResult.SUCCESS, ActionResult.NOT_ENOUGH_MINERALS, ActionResult.SUCCESS])
        game.observe(0, _barracks(1), _barracks(2), _barracks(3))

        game.book.issue([game.own(1), game.own(2), game.own(3)], _TRAIN_MARINE)
        game.flush()

        assert game.refused() == [(2, _TRAIN_MARINE, ActionResult.NOT_ENOUGH_MINERALS)]

    @pytest.mark.parametrize(
        ("make", "ability", "target", "reason"),
        [
            (_scv, _BUILD_DEPOT, _SITE, "carry it out there"),
            (_flying_barracks, _BUILD_REACTOR, _SITE, "carry it out there"),
            (_scv, _BUILD_REFINERY, 9, "carry it out there"),
            (partial(make_unit, unit_type=UnitTypeId.ENGINEERING_BAY), _RESEARCH, None, "research it"),
            (_flying_barracks, AbilityId.LAND, _SITE, "carry it out there"),
            (
                partial(make_unit, unit_type=UnitTypeId.SPINE_CRAWLER_UPROOTED),
                AbilityId.ROOT,
                _SITE,
                "carry it out there",
            ),
            (partial(make_unit, unit_type=UnitTypeId.WARP_GATE), _WARP_IN_ZEALOT, _SITE, "carry it out there"),
        ],
        ids=[
            "a build at a point",
            "an add-on at a point",
            "a build on a geyser",
            "a research",
            "a landing",
            "a rooting",
            "a warp-in",
        ],
    )
    def test_an_order_only_one_of_them_can_carry_out_given_to_several_raises(
        self, make: Any, ability: AbilityId, target: tuple[float, float] | int | None, reason: str
    ) -> None:
        """Two engineering bays given the same research in a command each research it once, two SCVs given a depot at
        one site are both charged until the second gives up, and of two flying barracks given a landing, two spine
        crawlers a rooting and two warp gates a warp-in, each at one point, one carries it out (tool `sweep_orders`,
        `one-command-each`)."""
        game = _costing()
        geyser = make_unit(9, UnitTypeId.VESPENE_GEYSER, at=(40.0, 40.0), alliance=Alliance.NEUTRAL)
        game.observe(0, make(1), make(2), geyser)
        aimed = game.tracker.unit_tracker.by_tag(target) if isinstance(target, int) else target

        with pytest.raises(ValueError, match=f"give {ability.name} to one unit, not 2: only one of them can {reason}"):
            game.book.issue([game.own(1), game.own(2)], ability, target=aimed)

        assert game.book.pending == ()

    def test_an_order_that_costs_nothing_still_goes_to_the_one_not_doing_it(self) -> None:
        game = _costing([ActionResult.SUCCESS])
        game.observe(0, _scv(1, _moving(_SITE)), _scv(2, at=(40.0, 40.0)))

        game.book.issue([game.own(1), game.own(2)], _MOVE, target=_SITE)

        assert _sent(game.flush()) == [(_MOVE, [2], _SITE, False)]

    def test_a_larva_morph_given_to_a_hatchery_goes_out_as_given(self) -> None:
        """The game refuses it `NotSupported` (in game); NachOS passes it to no larva."""
        game = _costing([ActionResult.NOT_SUPPORTED])
        game.observe(0, make_unit(1, UnitTypeId.HATCHERY, at=(50.0, 50.0)), make_unit(2, UnitTypeId.LARVA))

        game.book.issue(game.own(1), _DRONE)

        assert _sent(game.flush()) == [(_DRONE, [1], None, False)]
        assert [failure.action_result for failure in game.book._refusals] == [ActionResult.NOT_SUPPORTED]


class TestOrdersGivenApart:
    """Orders of an ability every unit of a command carries out, aimed at one target and queued alike, go out as one
    command, as if one order had named all their units, unless that would send a unit's commands out of the order it
    was given them."""

    def test_two_units_ordered_apart_to_one_point_go_out_in_one_command(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2))

        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.book.issue(game.own(2), _MOVE, target=(20.0, 21.0))

        assert _sent(game.flush()) == [(_MOVE, [1, 2], (20.0, 21.0), False)]
        assert game.refused() == []

    def test_points_the_game_keeps_as_one_are_one_target(self) -> None:
        """The game cuts a point down to `POINT_PRECISION`, so it carries out both moves at the first point."""
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2))

        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.book.issue(game.own(2), _MOVE, target=(20.0 + POINT_PRECISION / 2, 21.0))

        assert _sent(game.flush()) == [(_MOVE, [1, 2], (20.0, 21.0), False)]

    def test_two_units_ordered_apart_at_one_unit_go_out_in_one_command(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2), make_unit(9, at=(20.0, 21.0), alliance=Alliance.ENEMY))
        enemy = game.tracker.unit_tracker.by_tag(9)

        game.book.issue(game.own(1), _ATTACK, target=enemy)
        game.book.issue(game.own(2), _ATTACK, target=enemy)

        (command,) = _commands(game.flush())
        assert (list(command.unit_tags), command.target_unit_tag) == ([1, 2], 9)

    @pytest.mark.parametrize(
        ("ability", "target", "queued"),
        [(_MOVE, (20.0, 22.0), False), (_MOVE, (20.0, 21.0), True), (_ATTACK, (20.0, 21.0), False)],
        ids=["at another point", "queued", "of another ability"],
    )
    def test_an_order_that_differs_goes_out_in_a_command_of_its_own(
        self, ability: AbilityId, target: tuple[float, float], queued: bool
    ) -> None:
        game = _Game([ActionResult.SUCCESS] * 2)
        game.observe(0, _marine(1), _marine(2))

        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.book.issue(game.own(2), ability, target=target, queued=queued)

        assert _sent(game.flush()) == [(_MOVE, [1], (20.0, 21.0), False), (ability, [2], target, queued)]

    def test_orders_at_two_units_go_out_apart(self) -> None:
        game = _Game([ActionResult.SUCCESS] * 2)
        game.observe(0, _marine(1), _marine(2), *(make_unit(tag, alliance=Alliance.ENEMY) for tag in (8, 9)))

        game.book.issue(game.own(1), _ATTACK, target=game.tracker.unit_tracker.by_tag(8))
        game.book.issue(game.own(2), _ATTACK, target=game.tracker.unit_tracker.by_tag(9))

        assert [(list(command.unit_tags), command.target_unit_tag) for command in _commands(game.flush())] == [
            ([1], 8),
            ([2], 9),
        ]

    @pytest.mark.parametrize(
        ("make", "ability", "target"),
        [(_barracks, _TRAIN_MARINE, None), (_templar, _STORM, _SITE)],
        ids=["a train", "a spell at one point"],
    )
    def test_an_ability_one_unit_of_a_command_carries_out_goes_to_each_in_a_command_of_its_own(
        self, make: Any, ability: AbilityId, target: tuple[float, float] | None
    ) -> None:
        game = _costing([ActionResult.SUCCESS] * 2)
        game.observe(0, make(1), make(2))

        game.book.issue(game.own(1), ability, target=target)
        game.book.issue(game.own(2), ability, target=target)

        assert _sent(game.flush()) == [(ability, [1], target, False), (ability, [2], target, False)]

    def test_two_tanks_sieged_apart_go_out_in_one_siege_mode(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.SIEGE_TANK), make_unit(2, UnitTypeId.SIEGE_TANK))

        game.book.issue(game.own(1), _SIEGE)
        game.book.issue(game.own(2), _SIEGE)

        assert _sent(game.flush()) == [(RawAbilityId.SiegeMode_SiegeMode, [1, 2], None, False)]

    def test_types_sent_the_ability_as_two_of_the_game_s_go_out_apart(self) -> None:
        game = _Game([ActionResult.SUCCESS] * 2)
        game.observe(0, make_unit(1, UnitTypeId.SIEGE_TANK_SIEGED), _sieged_liberator(2))

        game.book.issue(game.own(1), _UNSIEGE)
        game.book.issue(game.own(2), _UNSIEGE)

        assert _sent(game.flush()) == [
            (RawAbilityId.Unsiege_Unsiege, [1], None, False),
            (RawAbilityId.Morph_LiberatorAAMode, [2], None, False),
        ]

    def test_transports_unloading_where_each_stands_go_out_apart(self) -> None:
        """Each is sent its unload at a point aimed at itself."""
        game = _Game([ActionResult.SUCCESS] * 2)
        game.observe(0, _medivac(1), _medivac(2))

        game.book.issue(game.own(1), _UNLOAD)
        game.book.issue(game.own(2), _UNLOAD)

        sent = [
            (command.ability_id, list(command.unit_tags), command.target_unit_tag)
            for command in _commands(game.flush())
        ]
        assert sent == [(_UNLOAD_AT, [1], 1), (_UNLOAD_AT, [2], 2)]

    def test_an_archon_merge_joins_no_other_order(self) -> None:
        """Each merges the two templar it names (in game)."""
        game = _Game([ActionResult.SUCCESS] * 2)
        game.observe(0, *(_templar(tag) for tag in (1, 2, 3, 4)))

        game.book.issue([game.own(1), game.own(2)], AbilityId.MORPH_ARCHON)
        game.book.issue([game.own(3), game.own(4)], AbilityId.MORPH_ARCHON)

        assert [list(command.unit_tags) for command in _commands(game.flush())] == [[1, 2], [3, 4]]

    def test_an_order_joins_a_command_it_does_not_follow_directly(self) -> None:
        game = _Game([ActionResult.SUCCESS] * 2)
        game.observe(0, _marine(1), _marine(2))

        for tag in (1, 2):
            game.book.issue(game.own(tag), _MOVE, target=(20.0, 21.0))
            game.book.issue(game.own(tag), _ATTACK, target=(30.0, 31.0), queued=True)

        assert _sent(game.flush()) == [(_MOVE, [1, 2], (20.0, 21.0), False), (_ATTACK, [1, 2], (30.0, 31.0), True)]

    def test_a_unit_s_queued_order_stays_behind_its_unqueued_one(self) -> None:
        """Joining the first command would send the second marine's queued move ahead of its move, which would then
        replace it."""
        game = _Game([ActionResult.SUCCESS] * 3)
        game.observe(0, _marine(1), _marine(2))

        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0), queued=True)
        game.book.issue(game.own(2), _MOVE, target=(30.0, 31.0))
        game.book.issue(game.own(2), _MOVE, target=(20.0, 21.0), queued=True)

        assert _sent(game.flush()) == [
            (_MOVE, [1], (20.0, 21.0), True),
            (_MOVE, [2], (30.0, 31.0), False),
            (_MOVE, [2], (20.0, 21.0), True),
        ]

    def test_a_command_names_a_unit_once(self) -> None:
        """A stim leaves a marine's orders as they are, so both of its stims go out, in a command each; the game would
        count its tag once in one command (tool `sweep_orders`)."""
        game = _Game([ActionResult.SUCCESS] * 2)
        game.observe(0, _marine(1), _marine(2))

        game.book.issue(game.own(1), _STIM)
        game.book.issue(game.own(2), _STIM)
        game.book.issue(game.own(1), _STIM)

        assert [list(command.unit_tags) for command in _commands(game.flush())] == [[1, 2], [1]]

    def test_an_unload_at_a_transport_joins_none_of_that_transport_s_own_unload(self) -> None:
        """The medivac unloading where it stands is sent its unload at a point aimed at itself, which one command with
        the other's would aim at one of its own units, as `issue` refuses for one order."""
        for unload_first in (False, True):
            game = _Game([ActionResult.SUCCESS] * 2)
            game.observe(0, _medivac(1), _medivac(2))

            if unload_first:
                game.book.issue(game.own(2), _UNLOAD)
            game.book.issue(game.own(1), _UNLOAD_AT, target=game.own(2))
            if not unload_first:
                game.book.issue(game.own(2), _UNLOAD)

            sent = [
                (command.ability_id, list(command.unit_tags), command.target_unit_tag)
                for command in _commands(game.flush())
            ]
            assert sorted(sent) == [(_UNLOAD_AT, [1], 2), (_UNLOAD_AT, [2], 2)]

    def test_a_command_aimed_at_one_of_its_own_units_joins_nothing(self) -> None:
        game = _Game([ActionResult.SUCCESS] * 3)
        game.observe(0, _marine(1), _marine(2), _marine(3), _marine(4))

        game.book.issue(game.own(3), _MOVE, target=game.own(2))
        game.book.issue([game.own(1), game.own(2)], _MOVE, target=game.own(2))
        game.book.issue(game.own(4), _MOVE, target=game.own(2))

        assert [(list(command.unit_tags), command.target_unit_tag) for command in _commands(game.flush())] == [
            ([3, 4], 2),
            ([1, 2], 2),
        ]

    def test_a_load_all_joins_no_other_order(self) -> None:
        """One command naming two command centers took in the workers of one of them in one run and of both in the
        next (tool `sweep_orders`)."""
        game = _Game([ActionResult.SUCCESS] * 2)
        game.observe(0, *(make_unit(tag, UnitTypeId.COMMAND_CENTER, at=(12.0 * tag, 12.0)) for tag in (1, 2)))

        game.book.issue(game.own(1), AbilityId.LOAD_ALL)
        game.book.issue(game.own(2), AbilityId.LOAD_ALL)

        assert [list(command.unit_tags) for command in _commands(game.flush())] == [[1], [2]]

    def test_no_unit_s_orders_go_out_in_another_order_than_it_was_given_them(self) -> None:
        """Turns of orders to three marines, two medivacs and a barracks, made up at random: a move or an attack at
        one of two points, at an enemy or at a marine, a stim, an unload where each medivac stands or at the other
        medivac, and a train, queued or not."""
        rng = random.Random(7)
        joined = 0
        for _ in range(500):
            game = _Game()
            enemy = make_unit(9, at=(40.0, 40.0), alliance=Alliance.ENEMY)
            game.observe(0, _marine(1), _marine(2), _marine(3), _medivac(4), _medivac(5), _barracks(6), enemy)
            marines = [game.own(tag) for tag in (1, 2, 3)]
            medivacs = [game.own(4), game.own(5)]
            targets = [(20.0, 21.0), (30.0, 31.0), game.tracker.unit_tracker.by_tag(9), *marines]
            for _ in range(rng.randint(1, 10)):
                queued = rng.random() < 0.5
                match rng.choice(["move", "attack", "stim", "unload", "unload at", "train"]):
                    case "move" | "attack" as kind:
                        units = rng.sample([*marines, *medivacs], rng.randint(1, 3))
                        ability = _MOVE if kind == "move" else _ATTACK
                        game.book.issue(units, ability, target=rng.choice(targets), queued=queued)
                    case "stim":
                        game.book.issue(rng.sample(marines, rng.randint(1, 2)), _STIM, queued=queued)
                    case "unload":
                        game.book.issue(rng.sample(medivacs, rng.randint(1, 2)), _UNLOAD, queued=queued)
                    case "unload at":
                        one, other = rng.sample(medivacs, 2)
                        game.book.issue(one, _UNLOAD_AT, target=other, queued=queued)
                    case _:
                        game.book.issue(game.own(6), _TRAIN_MARINE, queued=queued)
            sent = list(game.book._orders_to_send(game.book._issued, game.book._forced))

            unit_commands = unit_commands_giving(sent)

            for unit in (*marines, *medivacs, game.own(6)):
                given = [order for order, units in sent if unit in units]
                named_in = [order for command in unit_commands for order, units in command.parts if unit in units]
                assert named_in == given
            for unit_command in unit_commands:
                named = [unit.id for _, units in unit_command.parts for unit in units]
                assert len(named) == len(set(named))
                if isinstance(unit_command.target, Unit) and unit_command.target.id in named:
                    assert len(unit_command.parts) == 1
            joined += sum(len(unit_command.parts) - 1 for unit_command in unit_commands)
        assert joined > 0

    def test_a_refused_command_is_listed_once_for_each_unit_of_each_order_it_carried(self) -> None:
        game = _Game([ActionResult.ERROR, ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2), _marine(3), _marine(4))

        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.book.issue(game.own(4), _STIM)
        game.book.issue([game.own(2), game.own(3)], _MOVE, target=(20.0, 21.0))

        assert _sent(game.flush()) == [(_MOVE, [1, 2, 3], (20.0, 21.0), False), (_STIM, [4], None, False)]
        assert game.refused() == [
            (1, _MOVE, ActionResult.ERROR),
            (2, _MOVE, ActionResult.ERROR),
            (3, _MOVE, ActionResult.ERROR),
        ]

    def test_each_order_of_a_command_the_game_took_is_sent_to_its_own_units(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2), _marine(3))
        first = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        second = game.book.issue([game.own(2), game.own(3)], _MOVE, target=(20.0, 21.0))

        game.flush()
        game.observe(16, _marine(1), _marine(2), _marine(3))

        assert game.book.issued_to(game.own(1), include_sent=True) == (first,)
        assert game.book.issued_to(game.own(2), include_sent=True) == (second,)
        assert game.book.issued_to(game.own(3), include_sent=True) == (second,)

    def test_no_order_of_a_refused_command_is_taken_for_what_its_units_are_doing(self) -> None:
        game = _Game([ActionResult.ERROR], [ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.book.issue(game.own(2), _MOVE, target=(20.0, 21.0))
        game.flush()
        game.observe(16, _marine(1), _marine(2))

        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.book.issue(game.own(2), _MOVE, target=(20.0, 21.0))

        assert _sent(game.flush()) == [(_MOVE, [1, 2], (20.0, 21.0), False)]

    def test_a_game_answering_fewer_commands_than_it_was_sent_is_a_protocol_error(self) -> None:
        game = _Game([])
        game.observe(0, _marine(1), _marine(2))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.book.issue(game.own(2), _MOVE, target=(20.0, 21.0))

        with pytest.raises(ProtocolError, match="answered 0 of the 1 commands"):
            game.flush()


class TestWhatTheTurnsOrdersCost:
    def test_pending_cost_is_what_the_turn_s_orders_cost(self) -> None:
        game = _costing()
        game.observe(0, _scv(1), _barracks(2))

        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)
        game.book.issue(game.own(2), _TRAIN_REAPER)

        assert game.book.pending_cost == Cost(150, 50)

    def test_pending_cost_counts_only_the_last_of_orders_that_replace_one_another(self) -> None:
        game = _costing()
        game.observe(0, _scv(1), _barracks(2))
        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)
        game.book.issue(game.own(1), _BUILD_DEPOT, target=(35.0, 35.0))
        game.book.issue(game.own(2), _TRAIN_REAPER)
        game.book.issue(game.own(2), _TRAIN_MARINE)

        assert game.book.pending_cost == Cost(150, 0)

    def test_pending_cost_counts_an_order_to_several_once_for_each(self) -> None:
        game = _costing()
        game.observe(0, _barracks(1), _barracks(2), _barracks(3, _training()))

        game.book.issue([game.own(1), game.own(2), game.own(3)], _TRAIN_MARINE)

        assert game.book.pending_cost == Cost(150, 0)

    def test_pending_cost_counts_each_queued_train(self) -> None:
        game = _costing()
        game.observe(0, _barracks(1))
        game.book.issue(game.own(1), _TRAIN_MARINE)
        game.book.issue(game.own(1), _TRAIN_MARINE, queued=True)

        assert game.book.pending_cost == Cost(100, 0)

    def test_pending_cost_leaves_out_a_build_its_worker_is_already_carrying_out(self) -> None:
        """A build repeated every turn, once the game has it: the send leaves it out, and the game charged it."""
        game = _costing()
        game.observe(0, _scv(1, _building(), at=(29.0, 29.0)))

        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)

        assert game.book.pending_cost == Cost(0, 0)
        assert game.book.pending

    def test_pending_cost_leaves_out_a_withdrawn_order_and_what_went_out(self) -> None:
        game = _costing([ActionResult.SUCCESS])
        game.observe(0, _scv(1), _barracks(2))
        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)
        game.book.issue(game.own(2), _TRAIN_MARINE).withdraw()

        assert game.book.pending_cost == Cost(100, 0)

        game.flush()

        assert game.book.pending_cost == Cost(0, 0)


class TestAVerdictNachosCannotName:
    def test_it_says_which_result_the_game_answered_with(self) -> None:
        """The enum is generated from the protocol package, so a newer game could answer with a result it lacks. The
        protocol package itself refuses to carry one, so the reading is tested on its own."""
        assert ActionResult.read(int(ActionResult.QUEUE_IS_FULL)) is ActionResult.QUEUE_IS_FULL

        with pytest.raises(UnknownActionResultError, match="no member for the result 250"):
            ActionResult.read(250)


class _OrderingBot:
    """A bot that gives a few orders and keeps each, to see what the real game makes of them."""

    def __init__(self, api: Api, player: int) -> None:
        self.api = api
        self.player = player
        self.moved: IssuedOrder | None = None
        self.stim: IssuedOrder | None = None
        self.stimmed_move: IssuedOrder | None = None
        self.at_a_dead_tag: IssuedOrder | None = None
        self.killed: OwnUnit[Any] | None = None

    def turn(self, event: TurnEvent) -> None:
        """Give the next order not yet given, one per turn."""
        api = self.api
        marines = api.units.own.of_type(UnitTypeId.GHOST)
        middle = api.map.playable_area.center
        if not marines:
            if event.time.steps < 64:
                api.client.debug([_create(UnitTypeId.GHOST, middle, self.player, quantity=2)])
            return
        if self.moved is None and len(marines) >= 2:
            first, second = marines[0], marines[1]
            self.moved = api.orders.issue(first, _MOVE, target=middle + (6.0, 0.0))
            self.stim = api.orders.issue(second, _HOLD_FIRE)
            self.stimmed_move = api.orders.issue(second, _MOVE, target=middle + (0.0, 6.0))
            return
        if self.moved is None or self.stim is None:
            return
        if self.killed is None:
            self.killed = marines[-1]
            api.client.debug([debug_pb2.DebugCommand(kill_unit=debug_pb2.DebugKillUnit(tag=[self.killed.tag]))])
            return
        if self.at_a_dead_tag is None and self.killed.is_dead:
            self.at_a_dead_tag = api.orders.issue(self.killed, _MOVE, target=middle)


class _UnloadingBot:
    """A bot that loads two medivacs, then in one turn moves them on and unloads them where they are, the move given
    first unless `unload_first`."""

    unload_first = False

    def __init__(self, api: Api, player: int) -> None:
        self.api = api
        self.player = player
        self.loads: list[IssuedOrder] = []
        self.move: IssuedOrder | None = None
        self.unload: IssuedOrder | None = None
        self.unloaded_at: dict[int, Point] = {}
        self.reported: list[tuple[float, list[str]]] = []

    def turn(self, event: TurnEvent) -> None:
        api = self.api
        medivacs = api.units.own.of_type(UnitTypeId.MEDIVAC)
        marines = api.units.own.of_type(UnitTypeId.MARINE)
        middle = api.map.playable_area.center
        if self.unload is not None:
            self.reported.append((api.step, [order.ability.name for medivac in medivacs for order in medivac.orders]))
            return
        if len(medivacs) < 2:
            if event.time.steps < 64:
                api.client.debug(
                    [
                        _create(UnitTypeId.MEDIVAC, middle, self.player, quantity=2),
                        _create(UnitTypeId.MARINE, middle, self.player, quantity=2),
                    ]
                )
            return
        if not self.loads:
            if len(marines) >= 2:
                self.loads = [
                    api.orders.issue(medivac, AbilityId.LOAD, target=marine)
                    for medivac, marine in zip(medivacs, marines, strict=False)
                ]
            return
        if all(medivac.cargo_used > 0 for medivac in medivacs):
            self.unloaded_at = {medivac.tag: medivac.position for medivac in medivacs}
            if self.unload_first:
                self.unload = api.orders.issue(medivacs, _UNLOAD)
            self.move = api.orders.issue(medivacs, _MOVE, target=middle + (12.0, 0.0))
            if not self.unload_first:
                self.unload = api.orders.issue(medivacs, _UNLOAD)


class _SiegingBot:
    """A bot that sieges a liberator, then orders the same siege again on two turns from 32 steps later."""

    def __init__(self, api: Api, player: int) -> None:
        self.api = api
        self.player = player
        self.zone: Point | None = None
        self.sieges: list[IssuedOrder] = []
        self.reported: list[tuple[int, UnitTypeId, list[tuple[AbilityId, bool]]]] = []

    def turn(self, event: TurnEvent) -> None:
        api = self.api
        liberators = api.units.own.of_type([UnitTypeId.LIBERATOR, UnitTypeId.LIBERATOR_SIEGED])
        if not liberators:
            if event.time.steps < 64:
                api.client.debug([_create(UnitTypeId.LIBERATOR, api.map.playable_area.center, self.player, quantity=1)])
            return
        liberator = liberators[0]
        orders = [(order.ability, order.target is liberator) for order in liberator.orders]
        self.reported.append((api.step, liberator.type_id, orders))
        if self.zone is None:
            self.zone = liberator.position + (4.0, 0.0)
        if self._siege_due(api.step):
            self.sieges.append(api.orders.issue(liberator, _SIEGE_AT, target=self.zone))

    def _siege_due(self, step: int) -> bool:
        """Whether to order the siege now: at once, then on two turns from 32 steps later, while the liberator still
        shows the first."""
        return not self.sieges or (len(self.sieges) < 3 and step >= self.sieges[0].issued_time.steps + 32)


class _GroupSiegingBot:
    """A bot that sieges a tank, and a liberator at its zone, each by its own siege, and once both are sieged,
    unsieges them in one order."""

    def __init__(self, api: Api, player: int) -> None:
        self.api = api
        self.player = player
        self.siege: IssuedOrder | None = None
        self.unsiege: IssuedOrder | None = None
        self.sieged: list[UnitTypeId] = []

    def turn(self, event: TurnEvent) -> None:
        api = self.api
        units = api.units.own.of_type(_SIEGE_FORMS)
        middle = api.map.playable_area.center
        if len(units) < 2:
            if event.time.steps < 64:
                api.client.debug(
                    [
                        _create(UnitTypeId.SIEGE_TANK, middle, self.player, quantity=1),
                        _create(UnitTypeId.LIBERATOR, middle + (0.0, 3.0), self.player, quantity=1),
                    ]
                )
            return
        if self.siege is None:
            tank = next(unit for unit in units if unit.type_id is UnitTypeId.SIEGE_TANK)
            liberator = next(unit for unit in units if unit.type_id is UnitTypeId.LIBERATOR)
            self.siege = api.orders.issue(tank, _SIEGE)
            api.orders.issue(liberator, _SIEGE_AT, target=liberator.position + (4.0, 0.0))
        elif self.unsiege is None and {unit.type_id for unit in units} == _SIEGED:
            self.sieged = sorted(unit.type_id for unit in units)
            self.unsiege = api.orders.issue(units, _UNSIEGE)


class _GroupUnloadingBot:
    """A bot that loads a bunker and a medivac with a marine each, then unloads both in one order."""

    def __init__(self, api: Api, player: int) -> None:
        self.api = api
        self.player = player
        self.loads: list[IssuedOrder] = []
        self.unload: IssuedOrder | None = None
        self.marines_left_out = 0

    def turn(self, event: TurnEvent) -> None:
        api = self.api
        transports = api.units.own.of_type([UnitTypeId.BUNKER, UnitTypeId.MEDIVAC])
        marines = api.units.own.of_type(UnitTypeId.MARINE)
        middle = api.map.playable_area.center
        if len(transports) < 2:
            if event.time.steps < 64:
                api.client.debug(
                    [
                        _create(UnitTypeId.BUNKER, middle, self.player, quantity=1),
                        _create(UnitTypeId.MEDIVAC, middle + (6.0, 0.0), self.player, quantity=1),
                        _create(UnitTypeId.MARINE, middle + (0.0, 3.0), self.player, quantity=2),
                    ]
                )
            return
        if not self.loads:
            if len(marines) >= 2:
                self.loads = [
                    api.orders.issue(transport, AbilityId.LOAD, target=marine)
                    for transport, marine in zip(transports, marines, strict=False)
                ]
            return
        if self.unload is None and all(transport.cargo_used > 0 for transport in transports):
            # Units made by debug show up a turn or two later, so there may be more marines than were loaded.
            self.marines_left_out = len(marines)
            self.unload = api.orders.issue(transports, _UNLOAD)


class _ProductionBot:
    """A terran bot that raises a barracks and an engineering bay by debug command under cheats for supply and
    resources, gives the barracks a marine and a second queued behind it and the bay infantry weapons 1, and records
    each turn after what is in production: the marines, the research's progress, and the first marine's, with the
    step the steps the research and the first of two marines have left, and the types of what makes the marines and
    whether any is read as a unit already."""

    race = Race.TERRAN

    def __init__(self, api: Api, player: int) -> None:
        self.api = api
        self.player = player
        self.made = False
        self.ordered = False
        self.reads: list[tuple[int, float | None, float | None]] = []
        self.times_left: list[tuple[int, float]] = []
        self.marine_times_left: list[tuple[int, float]] = []
        self.marine_producers: set[UnitTypeId | None] = set()
        self.marine_read_as_a_unit = False

    def turn(self, event: TurnEvent) -> None:
        api = self.api
        barracks = api.units.own.of_type(UnitType.Barracks).complete
        bays = api.units.own.of_type(UnitType.EngineeringBay).complete
        if not self.made:
            cheats = (debug_pb2.DebugGameState.Value(name) for name in ("food", "all_resources"))
            middle = api.map.playable_area.center
            site = api.map.start_location.towards(middle, 12.0)
            creates = [
                _create(UnitTypeId.BARRACKS, site, self.player, quantity=1),
                _create(UnitTypeId.ENGINEERING_BAY, site + (0.0, 6.0), self.player, quantity=1),
            ]
            api.client.debug([*(debug_pb2.DebugCommand(game_state=cheat) for cheat in cheats), *creates])
            self.made = True
        elif not self.ordered and barracks and bays:
            api.orders.issue(barracks[0], _TRAIN_MARINE)
            api.orders.issue(barracks[0], _TRAIN_MARINE, queued=True)
            api.orders.issue(bays[0], AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_1)
            self.ordered = True
        elif self.ordered:
            research = api.researches.get(UpgradeId.TERRAN_INFANTRY_WEAPONS_1)
            progress = None if research is None else research.progress
            marines = api.in_production(UnitType.Marine)
            self.reads.append((len(marines), progress, marines[0].progress if marines else None))
            if research is not None:
                self.times_left.append((api.step, research.time_left.steps))
            if len(marines) == 2 and marines[0].time_left is not None:
                self.marine_times_left.append((api.step, marines[0].time_left.steps))
            self.marine_producers |= {
                None if marine.producer is None else marine.producer.type_id for marine in marines
            }
            self.marine_read_as_a_unit |= any(marine.unit is not None for marine in marines)


class _GroupTrainingBot:
    """A terran bot that raises three barracks by debug command under cheats for supply and resources, gives all three
    a marine in one order, and records what the turn's orders cost then and each turn after how many marines are in
    production."""

    race = Race.TERRAN

    def __init__(self, api: Api, player: int) -> None:
        self.api = api
        self.player = player
        self.made = False
        self.cost: Cost | None = None
        self.in_production: list[int] = []

    def turn(self, event: TurnEvent) -> None:
        api = self.api
        barracks = api.units.own.of_type(UnitType.Barracks).complete
        if not self.made:
            cheats = (debug_pb2.DebugGameState.Value(name) for name in ("food", "all_resources"))
            site = api.map.start_location.towards(api.map.playable_area.center, 12.0)
            api.client.debug(
                [
                    *(debug_pb2.DebugCommand(game_state=cheat) for cheat in cheats),
                    _create(UnitTypeId.BARRACKS, site, self.player, quantity=3),
                ]
            )
            self.made = True
        elif self.cost is None and len(barracks) == 3:
            api.orders.issue(barracks, _TRAIN_MARINE)
            self.cost = api.orders.pending_cost
        elif self.cost is not None:
            self.in_production.append(len(api.in_production(UnitType.Marine)))


class _UnloadingBotUnloadingFirst(_UnloadingBot):
    unload_first = True


def _create(unit_type: UnitTypeId, at: Point, owner: int, *, quantity: int) -> debug_pb2.DebugCommand:
    """A debug command that creates `quantity` units of `unit_type` at `at`."""
    position = common_pb2.Point2D(x=at.x, y=at.y)
    unit = debug_pb2.DebugCreateUnit(unit_type=unit_type, owner=owner, pos=position, quantity=quantity)
    return debug_pb2.DebugCommand(create_unit=unit)


@pytest.mark.integration
class TestAgainstTheRealGame:
    """Run with `pytest -m integration`. Plays a minute of a game, giving orders as a bot would."""

    def test_orders_go_out_each_turn_and_what_the_game_refuses_is_listed(self) -> None:
        bot, failures = _play_a_minute(_OrderingBot)

        assert bot.moved is not None
        # Hold fire and a move to the same ghost both go out; neither overrides the other.
        assert bot.stim is not None and bot.stimmed_move is not None
        # An order to a dead unit's tag is refused (in game), and nothing else is.
        assert bot.at_a_dead_tag is not None
        refused = [(failure.unit, failure.ability, failure.action_result) for failure in failures]
        assert refused == [(bot.killed, _MOVE, ActionResult.ERROR)]

    @pytest.mark.parametrize("unload_first", [False, True], ids=["move first", "unload first"])
    def test_transports_unload_where_they_are_and_move_on_in_one_turn(self, unload_first: bool) -> None:
        bot, failures = _play_a_minute(_UnloadingBotUnloadingFirst if unload_first else _UnloadingBot)

        print("refused or given up:", failures)
        print("orders the medivacs showed after:", bot.reported[:6])
        assert bot.move is not None and bot.unload is not None
        assert not [failure for failure in failures if failure.ability in (_MOVE, _UNLOAD)]
        api = bot.api
        marines = api.units.own.of_type(UnitTypeId.MARINE)
        assert len(marines) == 2
        for marine in marines:
            assert min(marine.position.distance_to(at) for at in bot.unloaded_at.values()) < 3.0
        for medivac in api.units.own.of_type(UnitTypeId.MEDIVAC):
            assert medivac.cargo_used == 0
            assert medivac.position.distance_to(bot.unloaded_at[medivac.tag]) > 6.0

    def test_a_liberator_sieged_at_once_shows_the_siege_aimed_at_itself_and_refuses_it_again(self) -> None:
        bot, failures = _play_a_minute(_SiegingBot)

        print("the liberator, turn by turn:", bot.reported[:12])
        print("sieges:", [order.issued_time.steps for order in bot.sieges], "refused or given up:", failures)
        refused = [(failure.time.steps, failure.ability, failure.action_result) for failure in failures]
        assert refused == [(order.issued_time.steps, _SIEGE_AT, ActionResult.NOT_SUPPORTED) for order in bot.sieges[1:]]
        # Each order as its ability and whether it is aimed at the liberator itself.
        (first, *_) = (orders for _, unit_type, orders in bot.reported if unit_type is UnitTypeId.LIBERATOR_SIEGED)
        assert first == [(_SIEGE_AT, True)]

    def test_a_tank_and_a_liberator_siege_by_their_own_orders_and_unsiege_in_one(self) -> None:
        bot, failures = _play_a_minute(_GroupSiegingBot)

        assert bot.siege is not None and bot.unsiege is not None
        print("refused or given up:", failures)
        assert bot.sieged == sorted(_SIEGED)
        assert not [failure for failure in failures if failure.ability in (_SIEGE, _SIEGE_AT, _UNSIEGE)]
        units = bot.api.units.own.of_type(_SIEGE_FORMS)
        assert {unit.type_id for unit in units} == {UnitTypeId.SIEGE_TANK, UnitTypeId.LIBERATOR}

    def test_a_bunker_and_a_medivac_unload_in_one_order(self) -> None:
        bot, failures = _play_a_minute(_GroupUnloadingBot)

        print("refused or given up:", failures)
        assert bot.unload is not None
        assert not [failure for failure in failures if failure.ability is _UNLOAD]
        transports = bot.api.units.own.of_type([UnitTypeId.BUNKER, UnitTypeId.MEDIVAC])
        assert [transport.cargo_used for transport in transports] == [0, 0]
        assert len(bot.api.units.own.of_type(UnitTypeId.MARINE)) == bot.marines_left_out + 2

    def test_what_is_in_production_is_what_the_game_makes(self) -> None:
        bot, failures = _play_a_minute(_ProductionBot)

        print("(marines in production, research progress, marine progress), turn by turn:", bot.reads)
        print("refused:", failures)
        assert bot.reads
        assert max(count for count, _, _ in bot.reads) == 2
        progress = [each for _, each, _ in bot.reads if each is not None]
        assert progress == sorted(progress)
        assert 0.0 < progress[-1] < 1.0
        # Every read of the steps a research has left names one step for it to finish at.
        assert len(bot.times_left) > 1
        assert len({step + left for step, left in bot.times_left}) == 1
        # So does every read of the steps the first of two marines has left, the second waiting behind it.
        assert len(bot.marine_times_left) > 1
        assert len({step + left for step, left in bot.marine_times_left}) == 1
        assert bot.marine_producers == {UnitTypeId.BARRACKS}
        assert not bot.marine_read_as_a_unit
        assert all(marine is not None for count, _, marine in bot.reads if count)
        marine = [each for count, _, each in bot.reads if count == 2 and each is not None]
        assert marine == sorted(marine)
        assert marine[-1] > marine[0]

    def test_three_barracks_given_one_train_each_train_a_marine(self) -> None:
        bot, failures = _play_a_minute(_GroupTrainingBot)

        print("marines in production, turn by turn:", bot.in_production)
        print("refused:", failures)
        # The three trains' steps added up, as `pending_cost` adds them: a marine takes 400 in the tables (in game).
        assert bot.cost == Cost(150, 0, supply=3, time=GameTime(steps=1200))
        assert max(bot.in_production) == 3
        assert not [failure for failure in failures if failure.ability is _TRAIN_MARINE]


def _play_a_minute[
    BotT: (
        _OrderingBot,
        _UnloadingBot,
        _SiegingBot,
        _GroupSiegingBot,
        _GroupUnloadingBot,
        _ProductionBot,
        _GroupTrainingBot,
    )
](
    make_bot: type[BotT],
) -> tuple[BotT, list[ActionFailure]]:
    """Play a minute of a game against a very easy computer with the bot `make_bot` makes, as terran unless it names a
    race, and return the bot and what `api.action_failures` listed turn by turn."""
    try:
        game_map = MapFile.find("PylonAIE_v4")
    except MapNotFoundError as missing:
        pytest.skip(str(missing))
    with (
        GameProcess.launch(window=(640, 480)) as process,
        closing(Client(WebSocketTransport.connect(process.url))) as client,
    ):
        client.create_game(game_map.path, [Participant(), Computer(Race.ZERG, Difficulty.VERY_EASY)])
        player = client.join_game(getattr(make_bot, "race", Race.TERRAN))
        api = Api()
        bot = make_bot(api, player)
        failures: list[ActionFailure] = []
        api.on(TurnEvent)(lambda _: failures.extend(api.action_failures))
        api.on(TurnEvent)(bot.turn)
        api.play(client, steps_per_turn=8, time_limit=GameTime(seconds=60))
    return bot, failures
