"""The orders a bot gives: what goes out in a turn's request, and what the game refuses of it."""

# Each `type: ignore` below marks a call the type checker must reject, so an unneeded one fails.
# pyright: reportUnnecessaryTypeIgnoreComment=true

import itertools
from contextlib import closing
from typing import Any

import pytest
from s2clientprotocol import common_pb2, data_pb2, debug_pb2, error_pb2, raw_pb2, sc2api_pb2

from sc2nachos import Api
from sc2nachos.enemy import Enemy
from sc2nachos.events import TurnEvent
from sc2nachos.gamedata import GameData, OrderBehavior
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point, Point3D
from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId
from sc2nachos.ids.raw import RawAbilityId
from sc2nachos.launch import GameProcess, MapFile, MapNotFoundError
from sc2nachos.match import Computer, Difficulty, Participant, Race
from sc2nachos.orders import Order, OrderBook
from sc2nachos.protocol import Client, WebSocketTransport
from sc2nachos.state import ActionFailure, ActionResult, UnknownActionResultError
from sc2nachos.units import Alliance, OwnUnit, Unit
from sc2nachos.units._tracking import _Tracker
from support import make_client, make_game_info, make_observation, make_response, make_tables, make_unit

_MOVE = AbilityId.MOVE
# The id a unit reports a move under, which reads as the move.
_MOVE_RUNS_AS = RawAbilityId.Move_Move
_ATTACK = AbilityId.ATTACK
_STIM = AbilityId.STIM
_HOLD_FIRE = AbilityId.HOLD_FIRE_ON
_CREEP_TUMOR = AbilityId.BUILD_CREEP_TUMOR
_TRAIN_MARINE = AbilityId.BARRACKS_TRAIN_MARINE
_TRAIN_REAPER = AbilityId.BARRACKS_TRAIN_REAPER
_RALLY = AbilityId.RALLY_UNITS
_SMART = AbilityId.SMART
_UNLOAD = AbilityId.UNLOAD
_TRAIN_SCV = AbilityId.COMMAND_CENTER_TRAIN_SCV
_UNLOAD_AT = AbilityId.UNLOAD_AT
_SIEGE = AbilityId.SIEGE
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

    def __init__(self, *verdicts: list[ActionResult], tables: GameData = _TABLES, build_reach: float = 2.5) -> None:
        """A game with `tables` that answers each flush with the next of `verdicts`, one result per action sent, and
        gives a held build once its worker is within `build_reach`."""
        responses = [
            make_response(action=sc2api_pb2.ResponseAction(result=[_verdict(result) for result in verdict]))
            for verdict in verdicts
        ]
        self.client, self.transport = make_client(*responses)
        self.tracker = _Tracker(tables, Enemy())
        self.map = GameMap(make_game_info())
        self.book = OrderBook(tables, build_reach=build_reach)

    def observe(self, step: int, *units: raw_pb2.Unit, dead: tuple[int, ...] = ()) -> None:
        """Take in an observation of `units` at `step`, in which the units under the tags `dead` died."""
        observation = make_observation(step, units=units, dead=dead)
        self.tracker.update(observation.observation.raw_data, step)
        changes = self.tracker.last_changes
        changed_hands = [unit for unit, _ in changes.units_alliance_changed]
        self.book._observe(step, [*changes.units_died, *changes.units_found_dead, *changed_hands])

    def flush(self) -> sc2api_pb2.RequestAction | None:
        """Send the turn's orders and return the request that went out, or `None` if none did."""
        before = len(self.transport.requests)
        self.book._send(self.client)
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

    def test_an_order_carries_the_bots_own_data(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))

        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0), data="scouting")
        plain = game.book.issue(game.own(1), _STIM)

        assert order.data == "scouting"
        assert order.data.upper() == "SCOUTING"  # An `Order[str]`, so the type checker knows what `data` is.
        assert plain.data is None
        _: Order[None] = plain
        _wrong: Order[int] = order  # type: ignore

    def test_an_order_says_what_it_is_in_a_repr(self) -> None:
        game = _Game()
        game.observe(0, _marine(1), _marine(2))

        one = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        both = game.book.issue([game.own(1), game.own(2)], _MOVE, target=(20.0, 21.0))

        assert repr(one) == f"Order(MOVE, {game.own(1)!r})"
        assert repr(both) == "Order(MOVE, 2 units)"

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

    def test_the_camera_goes_out_with_the_turns_orders_and_only_its_last_move(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.book.camera((5.0, 6.0))
        game.book.camera((7.0, 8.0))

        request = game.flush()

        assert request is not None
        raw = [action.action_raw for action in request.actions]
        moves = [action.camera_move for action in raw if action.HasField("camera_move")]
        assert [(move.center_world_space.x, move.center_world_space.y) for move in moves] == [(7.0, 8.0)]

    def test_a_camera_move_is_kept_as_the_protocol_carries_it(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        game.book.camera((157.123288015625, 3.0))

        request = game.flush()

        assert request is not None
        (move,) = [action.action_raw.camera_move for action in request.actions]
        assert move.center_world_space.x == 157.123291015625

    def test_a_camera_move_alone_is_sent_too(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        game.book.camera((5.0, 6.0))

        request = game.flush()

        assert request is not None
        assert len(request.actions) == 1

    def test_an_order_knows_what_its_ability_does_to_a_unit(self) -> None:
        game = _Game()
        game.observe(0, _marine(1))

        assert game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0)).order_behavior is OrderBehavior.REPLACES
        assert game.book.issue(game.own(1), _STIM).order_behavior is OrderBehavior.KEEPS_ORDERS


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


class TestAnOrderAUnitIsAlreadyCarryingOut:
    """An unqueued order equal to a unit's first is answered `SUCCESS` and carries nothing out, but drops what the
    unit had queued (in game)."""

    def test_it_is_not_sent(self) -> None:
        game = _Game()
        game.observe(0, _marine(1, _moving((20.0, 21.0)), _moving((30.0, 31.0))))

        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0), data="again")

        assert game.flush() is None
        assert order.data == "again"

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
    """`SIEGE` goes out as each type's own siege: a tank's siege mode aimed at nothing, a liberator's defender
    mode aimed at its zone. A liberator ordered it at a point is a sieged liberator by the next observation, and reports
    `LiberatorMorphtoAG_LiberatorAGMode` aimed at itself while its zone forms; ordered the siege again, it is refused
    `NotSupported` (in game)."""

    def test_a_sieged_liberator_reads_as_carrying_out_the_siege(self) -> None:
        game = _Game()
        game.observe(0, _sieged_liberator(1))

        (order,) = game.own(1).orders

        assert order.ability is _SIEGE
        assert order.target is game.own(1)

    def test_the_siege_again_is_sent_since_it_was_aimed_at_a_point_and_shows_aimed_at_the_liberator(self) -> None:
        game = _Game([ActionResult.NOT_SUPPORTED])
        game.observe(0, _sieged_liberator(1))

        game.book.issue(game.own(1), _SIEGE, target=(20.0, 21.0))

        (command,) = _commands(game.flush())
        assert command.ability_id == RawAbilityId.Morph_LiberatorAGMode
        assert game.refused() == [(1, _SIEGE, ActionResult.NOT_SUPPORTED)]

    def test_a_tank_and_a_liberator_go_out_each_as_its_own_siege(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.SIEGE_TANK), make_unit(2, UnitTypeId.LIBERATOR))

        game.book.issue([game.own(1), game.own(2)], _SIEGE, target=(20.0, 21.0))

        tank, liberator = _commands(game.flush())
        assert (tank.ability_id, list(tank.unit_tags)) == (RawAbilityId.SiegeMode_SiegeMode, [1])
        assert not tank.HasField("target_world_space_pos")
        assert (liberator.ability_id, list(liberator.unit_tags)) == (RawAbilityId.Morph_LiberatorAGMode, [2])
        assert (liberator.target_world_space_pos.x, liberator.target_world_space_pos.y) == (20.0, 21.0)

    def test_a_tank_alone_needs_no_point(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.SIEGE_TANK))

        game.book.issue(game.own(1), _SIEGE)

        (command,) = _commands(game.flush())
        assert command.ability_id == RawAbilityId.SiegeMode_SiegeMode

    def test_a_liberator_needs_a_point(self) -> None:
        game = _Game()
        game.observe(0, make_unit(1, UnitTypeId.SIEGE_TANK), make_unit(2, UnitTypeId.LIBERATOR))

        with pytest.raises(TypeError, match="SIEGE takes a point for LIBERATOR"):
            game.book.issue([game.own(1), game.own(2)], _SIEGE)

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
        hold = game.book.issue([game.own(1), game.own(2)], _HOLD_FIRE)

        sent_move, sent_hold = _commands(game.flush())

        assert hold.order_behavior is OrderBehavior.REPLACES
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
        tumor = game.book.issue(game.own(1), _CREEP_TUMOR, target=(30.0, 31.0))

        (sent,) = _commands(game.flush())

        assert sent.ability_id == _CREEP_TUMOR
        assert tumor.order_behavior is OrderBehavior.REPLACES


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
        assert failure.step == 16

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

    def test_a_camera_move_the_game_refuses_is_no_order_s_refusal(self) -> None:
        game = _Game([ActionResult.ERROR])
        game.observe(0, _marine(1))
        game.book.camera((5.0, 6.0))

        game.flush()

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
        """A tank and a liberator are each sent their own siege, and here the game takes only the tank's."""
        game = _Game([ActionResult.SUCCESS, ActionResult.NOT_SUPPORTED], [ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.SIEGE_TANK), make_unit(2, UnitTypeId.LIBERATOR))
        game.book.issue([game.own(1), game.own(2)], _SIEGE, target=(20.0, 21.0))
        game.flush()
        game.observe(16, make_unit(1, UnitTypeId.SIEGE_TANK), make_unit(2, UnitTypeId.LIBERATOR))

        game.book.issue([game.own(1), game.own(2)], _SIEGE, target=(20.0, 21.0))

        (command,) = _commands(game.flush())
        assert list(command.unit_tags) == [2]


class TestOrdersThatQueue:
    """A train queues behind what a structure is making, so it is neither a duplicate nor a replacement (in game)."""

    def test_a_train_is_sent_though_the_structure_is_already_making_one(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _barracks(1, _training()))

        order = game.book.issue(game.own(1), _TRAIN_MARINE)

        (command,) = _commands(game.flush())
        assert command.ability_id == _TRAIN_MARINE
        assert order.order_behavior is OrderBehavior.QUEUES


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
        unload = game.book.issue(game.own(1), _UNLOAD)

        sent = [command.ability_id for command in _commands(game.flush())]

        assert sent == [_TRAIN_SCV, _UNLOAD]
        assert unload.order_behavior is OrderBehavior.KEEPS_ORDERS

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


class TestTwoTrainsInOneTurn:
    """A train goes behind what a structure is making, queued or not (in game), so it competes with nothing. These
    trains cost nothing, so none is held."""

    def test_a_queued_second_train_goes_out_queued(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _barracks(1))
        game.book.issue(game.own(1), _TRAIN_MARINE)
        game.book.issue(game.own(1), _TRAIN_MARINE, queued=True)

        one, two = _commands(game.flush())

        assert (one.ability_id, one.queue_command) == (_TRAIN_MARINE, False)
        assert (two.ability_id, two.queue_command) == (_TRAIN_MARINE, True)

    def test_an_unqueued_second_train_goes_out_too(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _barracks(1))
        game.book.issue(game.own(1), _TRAIN_MARINE)
        game.book.issue(game.own(1), _TRAIN_REAPER)

        assert [command.ability_id for command in _commands(game.flush())] == [_TRAIN_MARINE, _TRAIN_REAPER]


# Orders NachOS holds until a unit can start them. These tables give what is held a cost and a time, as the game's do.
_BUILD_DEPOT = AbilityId.SCV_BUILD_SUPPLY_DEPOT
_BUILD_REFINERY = AbilityId.SCV_BUILD_REFINERY
_BUILD_REACTOR = AbilityId.BUILD_REACTOR
_LAND = AbilityId.LAND
_ORBITAL = AbilityId.COMMAND_CENTER_MORPH_ORBITAL_COMMAND
_WEAPONS_1 = AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_1
_WEAPONS_2 = AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_2
_WARP_IN_ADEPT = AbilityId.WARP_GATE_WARP_IN_ADEPT
_SITE = (30.0, 30.0)


def _row(
    unit_type: UnitTypeId,
    *,
    structure: bool = False,
    minerals: int = 0,
    vespene: int = 0,
    steps: float = 0.0,
    speed: float = 0.0,
    alias: tuple[UnitTypeId, ...] = (),
) -> data_pb2.UnitTypeData:
    """A unit type's row: what it costs, the steps it takes to make, and its speed at Normal, as the game gives it."""
    attribute = data_pb2.Attribute.Structure if structure else data_pb2.Attribute.Mechanical
    return data_pb2.UnitTypeData(
        unit_id=unit_type,
        attributes=[attribute],
        mineral_cost=minerals,
        vespene_cost=vespene,
        build_time=steps,
        movement_speed=speed,
        tech_alias=alias,
    )


_HOLDING_TABLES = make_tables(
    _row(UnitTypeId.SCV, minerals=50, steps=272.0, speed=2.8125),
    _row(UnitTypeId.MARINE, minerals=50, steps=403.0),
    _row(UnitTypeId.REAPER, minerals=50, vespene=50, steps=716.0),
    _row(UnitTypeId.ADEPT, minerals=100, vespene=25, steps=448.0),
    _row(UnitTypeId.WARP_GATE, structure=True, minerals=150),
    _row(UnitTypeId.SUPPLY_DEPOT, structure=True, minerals=100, steps=470.0),
    _row(UnitTypeId.REFINERY, structure=True, minerals=75, steps=470.0),
    _row(UnitTypeId.BARRACKS, structure=True, minerals=150, steps=1030.0),
    _row(UnitTypeId.BARRACKS_FLYING, structure=True, minerals=150, speed=0.9375),
    _row(UnitTypeId.COMMAND_CENTER, structure=True, minerals=400, steps=1590.0),
    _row(UnitTypeId.COMMAND_CENTER_FLYING, structure=True, minerals=400, speed=0.9375),
    _row(UnitTypeId.ORBITAL_COMMAND, structure=True, minerals=550, steps=560.0),
    _row(UnitTypeId.ENGINEERING_BAY, structure=True, minerals=125, steps=560.0),
    *(
        _row(reactor, structure=True, minerals=50, vespene=50, steps=806.0, alias=(UnitTypeId.REACTOR,))
        for reactor in (UnitTypeId.REACTOR_BARRACKS, UnitTypeId.REACTOR_FACTORY, UnitTypeId.REACTOR_STARPORT)
    ),
    abilities=[
        data_pb2.AbilityData(ability_id=_MOVE, target=_AT_A_POINT_OR_UNIT),
        data_pb2.AbilityData(ability_id=_LAND, target=_AT_A_POINT),
        data_pb2.AbilityData(ability_id=_RALLY, target=_AT_A_POINT_OR_UNIT),
        data_pb2.AbilityData(ability_id=_BUILD_DEPOT, target=_AT_A_POINT, is_building=True),
        data_pb2.AbilityData(ability_id=_BUILD_REFINERY, target=data_pb2.AbilityData.Target.Unit, is_building=True),
        data_pb2.AbilityData(
            ability_id=_BUILD_REACTOR, target=data_pb2.AbilityData.Target.PointOrNone, is_building=True
        ),
        data_pb2.AbilityData(ability_id=_TRAIN_MARINE),
        data_pb2.AbilityData(ability_id=_TRAIN_REAPER),
        data_pb2.AbilityData(ability_id=_TRAIN_SCV),
        data_pb2.AbilityData(ability_id=_ORBITAL),
        data_pb2.AbilityData(ability_id=_WEAPONS_1),
        data_pb2.AbilityData(ability_id=_WEAPONS_2),
        data_pb2.AbilityData(ability_id=_WARP_IN_ADEPT, target=_AT_A_POINT),
    ],
    upgrades=[
        data_pb2.UpgradeData(upgrade_id=UpgradeId.TERRAN_INFANTRY_WEAPONS_1, mineral_cost=100, vespene_cost=100),
        data_pb2.UpgradeData(upgrade_id=UpgradeId.TERRAN_INFANTRY_WEAPONS_2, mineral_cost=175, vespene_cost=175),
    ],
)


def _holding(*verdicts: list[ActionResult], build_reach: float = 2.5) -> _Game:
    """A game whose tables give what is held a cost and a time."""
    return _Game(*verdicts, tables=_HOLDING_TABLES, build_reach=build_reach)


def _scv(tag: int, *orders: raw_pb2.UnitOrder, at: tuple[float, float] = (10.0, 10.0)) -> raw_pb2.Unit:
    """One of this player's SCVs, carrying out `orders`."""
    return make_unit(tag, UnitTypeId.SCV, at=at, orders=orders)


def _building(site: tuple[float, float] = _SITE) -> raw_pb2.UnitOrder:
    """The order an SCV shows while it goes to build a depot at `site`, or builds it."""
    return raw_pb2.UnitOrder(ability_id=_BUILD_DEPOT, target_world_space_pos=common_pb2.Point(x=site[0], y=site[1]))


def _flying_barracks(tag: int, *orders: raw_pb2.UnitOrder, at: tuple[float, float] = (20.0, 20.0)) -> raw_pb2.Unit:
    """One of this player's barracks, lifted, carrying out `orders`."""
    return make_unit(tag, UnitTypeId.BARRACKS_FLYING, at=at, is_flying=True, orders=orders)


def _landing(at: tuple[float, float] = _SITE) -> raw_pb2.UnitOrder:
    """The order a lifted barracks shows while it goes to land at `at`."""
    return raw_pb2.UnitOrder(ability_id=_LAND, target_world_space_pos=common_pb2.Point(x=at[0], y=at[1]))


def _with_reactor(tag: int, reactor: int, *orders: raw_pb2.UnitOrder, progress: float = 1.0) -> list[raw_pb2.Unit]:
    """One of this player's barracks with a reactor `progress` built, carrying out `orders`, and the reactor."""
    barracks = make_unit(tag, UnitTypeId.BARRACKS, at=(12.0, 12.0), orders=orders, add_on_tag=reactor)
    return [barracks, make_unit(reactor, UnitTypeId.REACTOR_BARRACKS, at=(14.5, 11.5), build_progress=progress)]


def _command_center(tag: int, *orders: raw_pb2.UnitOrder) -> raw_pb2.Unit:
    """One of this player's command centers, carrying out `orders`."""
    return make_unit(tag, UnitTypeId.COMMAND_CENTER, at=(40.0, 40.0), orders=orders)


def _flying_command_center(
    tag: int, *orders: raw_pb2.UnitOrder, at: tuple[float, float] = (40.0, 40.0)
) -> raw_pb2.Unit:
    """One of this player's command centers, lifted, carrying out `orders`."""
    return make_unit(tag, UnitTypeId.COMMAND_CENTER_FLYING, at=at, is_flying=True, orders=orders)


def _making(ability: AbilityId, progress: float = 0.5) -> raw_pb2.UnitOrder:
    """The order a structure shows while it makes what `ability` makes."""
    return raw_pb2.UnitOrder(ability_id=ability, progress=progress)


def _sent(request: sc2api_pb2.RequestAction | None) -> list[tuple[int, list[int], tuple[float, float] | None, bool]]:
    """Each unit command of a request that went out: its ability, its units, the point it is aimed at, and whether it
    is queued."""
    sent: list[tuple[int, list[int], tuple[float, float] | None, bool]] = []
    for command in _commands(request):
        point = command.target_world_space_pos if command.HasField("target_world_space_pos") else None
        at = None if point is None else (point.x, point.y)
        sent.append((command.ability_id, list(command.unit_tags), at, command.queue_command))
    return sent


class TestHoldingABuild:
    """A build is charged when ordered, not when the builder arrives (in game), so NachOS sends the worker to the site
    and gives it the build once it is within reach."""

    def test_a_build_out_of_reach_is_held_and_its_worker_sent_to_the_site(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _scv(1))

        build = game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)

        assert _sent(game.flush()) == [(_MOVE, [1], _SITE, False)]
        assert game.book.issued_to(game.own(1)) == (build,)

    def test_a_build_within_reach_goes_out_at_once(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _scv(1, at=(28.0, 30.0)))

        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)

        assert _sent(game.flush()) == [(_BUILD_DEPOT, [1], _SITE, False)]
        assert game.book.issued_to(game.own(1)) == ()

    def test_a_held_build_goes_out_in_place_of_the_move_once_its_worker_is_within_reach(self) -> None:
        game = _holding([ActionResult.SUCCESS], [ActionResult.SUCCESS])
        game.observe(0, _scv(1))
        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)
        game.flush()
        game.observe(16, _scv(1, _moving(_SITE), at=(20.0, 20.0)))

        assert game.flush() is None

        game.observe(32, _scv(1, _moving(_SITE), at=(28.0, 29.0)))

        assert _sent(game.flush()) == [(_BUILD_DEPOT, [1], _SITE, False)]
        assert game.book.issued_to(game.own(1)) == ()

    def test_the_reach_is_the_one_the_book_was_given(self) -> None:
        game = _holding([ActionResult.SUCCESS], build_reach=4.0)
        game.observe(0, _scv(1, at=(26.5, 30.0)))

        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)

        assert _sent(game.flush()) == [(_BUILD_DEPOT, [1], _SITE, False)]

    def test_a_geyser_is_reached_from_its_edge(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        geyser = make_unit(2, UnitTypeId.VESPENE_GEYSER, at=_SITE, alliance=Alliance.NEUTRAL, radius=2.0)
        game.observe(0, _scv(1, at=(25.6, 30.0)), geyser)

        game.book.issue(game.own(1), _BUILD_REFINERY, target=game.tracker.unit_tracker.by_tag(2))

        (command,) = _commands(game.flush())
        assert (command.ability_id, command.target_unit_tag) == (_BUILD_REFINERY, 2)

    def test_a_build_queued_behind_a_move_has_its_move_queued_behind_that_one(self) -> None:
        game = _holding([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _scv(1))

        game.book.issue(game.own(1), _MOVE, target=(50.0, 50.0))
        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE, queued=True)

        assert _sent(game.flush()) == [(_MOVE, [1], (50.0, 50.0), False), (_MOVE, [1], _SITE, True)]

    def test_a_worker_passing_its_site_on_an_earlier_leg_does_not_start_it(self) -> None:
        game = _holding([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _scv(1))
        game.book.issue(game.own(1), _MOVE, target=(50.0, 50.0))
        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE, queued=True)
        game.flush()

        game.observe(16, _scv(1, _moving((50.0, 50.0)), _moving(_SITE), at=(29.0, 29.0)))

        assert game.flush() is None

    def test_a_queued_build_starts_once_its_worker_is_on_its_way_to_the_site(self) -> None:
        game = _holding([ActionResult.SUCCESS, ActionResult.SUCCESS], [ActionResult.SUCCESS])
        game.observe(0, _scv(1))
        game.book.issue(game.own(1), _MOVE, target=(50.0, 50.0))
        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE, queued=True)
        game.flush()
        game.observe(16, _scv(1, _moving((50.0, 50.0)), _moving(_SITE), at=(40.0, 40.0)))
        game.observe(32, _scv(1, _moving(_SITE), at=(29.0, 29.0)))

        assert _sent(game.flush()) == [(_BUILD_DEPOT, [1], _SITE, False)]

    def test_a_free_order_queued_behind_a_held_build_goes_out_right_behind_it(self) -> None:
        game = _holding([ActionResult.SUCCESS], [ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _scv(1))
        build = game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)
        hide = game.book.issue(game.own(1), _MOVE, target=(5.0, 5.0), queued=True)

        assert _sent(game.flush()) == [(_MOVE, [1], _SITE, False)]
        assert game.book.issued_to(game.own(1)) == (build, hide)

        game.observe(16, _scv(1, _moving(_SITE), at=(29.0, 29.0)))

        assert _sent(game.flush()) == [(_BUILD_DEPOT, [1], _SITE, False), (_MOVE, [1], (5.0, 5.0), True)]

    def test_a_build_repeated_every_turn_replaces_the_held_one_and_its_worker_is_not_sent_again(self) -> None:
        """Nothing NachOS holds is redundant; the move to the site is a game order, and the worker is on it."""
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _scv(1))
        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)
        game.flush()
        game.observe(16, _scv(1, _moving(_SITE), at=(20.0, 20.0)))

        again = game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)

        assert game.flush() is None
        assert game.book.issued_to(game.own(1)) == (again,)

    def test_a_build_its_worker_is_already_carrying_out_is_not_sent_again(self) -> None:
        """Sending it again would drop what the worker has queued behind it (in game)."""
        game = _holding()
        game.observe(0, _scv(1, _building(), _moving((5.0, 5.0)), at=(29.0, 29.0)))

        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)

        assert game.flush() is None
        assert game.book.issued_to(game.own(1)) == ()

    def test_a_forced_build_is_sent_though_its_worker_is_carrying_it_out(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _scv(1, _building(), _moving((5.0, 5.0)), at=(29.0, 29.0)))

        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE, force=True)

        assert _sent(game.flush()) == [(_BUILD_DEPOT, [1], _SITE, False)]


class TestHoldingAnAddOnOrAMorph:
    """A lifted barracks given an add-on lands at the point and is charged at once, and a structure making something
    refuses a morph or an add-on (in game), so NachOS holds either until the structure is idle on the ground."""

    def test_a_flying_barracks_given_an_add_on_is_sent_to_land_at_its_point(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _flying_barracks(1))

        add_on = game.book.issue(game.own(1), _BUILD_REACTOR, target=_SITE)

        assert _sent(game.flush()) == [(_LAND, [1], _SITE, False)]
        assert game.book.issued_to(game.own(1)) == (add_on,)

    def test_the_add_on_goes_out_with_no_point_once_the_barracks_has_landed_and_is_idle(self) -> None:
        game = _holding([ActionResult.SUCCESS], [ActionResult.SUCCESS])
        game.observe(0, _flying_barracks(1))
        game.book.issue(game.own(1), _BUILD_REACTOR, target=_SITE)
        game.flush()
        game.observe(16, _flying_barracks(1, _landing(), at=(25.0, 25.0)))

        assert game.flush() is None

        game.observe(32, make_unit(1, UnitTypeId.BARRACKS, at=_SITE))

        assert _sent(game.flush()) == [(_BUILD_REACTOR, [1], None, False)]

    def test_an_add_on_with_no_point_to_a_flying_barracks_is_a_mistake(self) -> None:
        game = _holding()
        game.observe(0, _flying_barracks(1))

        with pytest.raises(TypeError, match="BUILD_REACTOR takes a point for a flying BARRACKS_FLYING"):
            game.book.issue(game.own(1), _BUILD_REACTOR)

    def test_a_morph_waits_until_the_structure_is_idle(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _command_center(1, _making(_TRAIN_SCV)))
        orbital = game.book.issue(game.own(1), _ORBITAL)

        assert game.flush() is None
        assert game.book.issued_to(game.own(1)) == (orbital,)

        game.observe(16, _command_center(1))

        assert _sent(game.flush()) == [(_ORBITAL, [1], None, False)]

    def test_an_unqueued_morph_drops_the_trains_held_and_a_queued_one_goes_behind_them(self) -> None:
        game = _holding()
        game.observe(0, _command_center(1, _making(_TRAIN_SCV)))
        game.book.issue(game.own(1), _TRAIN_SCV)
        game.flush()
        game.observe(16, _command_center(1, _making(_TRAIN_SCV, 0.6)))

        orbital = game.book.issue(game.own(1), _ORBITAL)
        game.flush()

        assert game.book.issued_to(game.own(1)) == (orbital,)

        train = game.book.issue(game.own(1), _TRAIN_SCV)
        queued = game.book.issue(game.own(1), _ORBITAL, queued=True)

        assert game.book.issued_to(game.own(1)) == (orbital, train, queued)

    def test_a_lifted_command_center_given_a_landing_and_an_orbital_lands_first(self) -> None:
        """The game refuses a morph queued behind a landing `NotSupported` as it is given (in game)."""
        game = _holding([ActionResult.SUCCESS], [ActionResult.SUCCESS])
        game.observe(0, _flying_command_center(1))
        game.book.issue(game.own(1), _LAND, target=_SITE)
        orbital = game.book.issue(game.own(1), _ORBITAL, queued=True)

        assert _sent(game.flush()) == [(_LAND, [1], _SITE, False)]
        assert game.book.issued_to(game.own(1)) == (orbital,)

        game.observe(16, _flying_command_center(1, _landing(), at=(35.0, 35.0)))
        assert game.flush() is None
        game.observe(32, make_unit(1, UnitTypeId.COMMAND_CENTER, at=_SITE))

        assert _sent(game.flush()) == [(_ORBITAL, [1], None, True)]

    def test_a_lifted_command_center_holds_an_orbital_until_it_lands(self) -> None:
        game = _holding()
        game.observe(0, _flying_command_center(1))
        orbital = game.book.issue(game.own(1), _ORBITAL)

        assert game.flush() is None
        game.observe(16, _flying_command_center(1))
        game.observe(32, _flying_command_center(1))

        assert game.flush() is None
        assert game.book.issued_to(game.own(1)) == (orbital,)

    def test_the_add_on_order_a_structure_still_shows_fills_every_slot(self) -> None:
        """A barracks stays busy one step longer than its add-on takes (in game)."""
        game = _holding()
        game.observe(0, *_with_reactor(1, 2, _making(_BUILD_REACTOR, 0.0)))

        game.book.issue(game.own(1), _TRAIN_MARINE)

        assert game.flush() is None


class TestHoldingProduction:
    """A train or a research queued behind what a structure is making is paid from the step it is ordered (in game),
    so NachOS gives a structure only what it runs at once: one item, or two with a finished reactor."""

    def test_a_train_to_a_structure_making_one_is_held(self) -> None:
        game = _holding()
        game.observe(0, _barracks(1, _training()))

        train = game.book.issue(game.own(1), _TRAIN_MARINE)

        assert game.flush() is None
        assert game.book.issued_to(game.own(1)) == (train,)

    def test_a_train_to_an_idle_structure_goes_out_at_once(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _barracks(1))

        game.book.issue(game.own(1), _TRAIN_MARINE)

        assert _sent(game.flush()) == [(_TRAIN_MARINE, [1], None, False)]

    def test_a_held_train_goes_out_once_the_structure_has_a_slot_free(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _barracks(1, _training()))
        game.book.issue(game.own(1), _TRAIN_MARINE)
        game.flush()

        game.observe(16, _barracks(1))

        assert _sent(game.flush()) == [(_TRAIN_MARINE, [1], None, False)]

    def test_two_trains_given_in_one_turn_are_two_items(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _barracks(1))

        game.book.issue(game.own(1), _TRAIN_MARINE)
        second = game.book.issue(game.own(1), _TRAIN_MARINE)

        assert _sent(game.flush()) == [(_TRAIN_MARINE, [1], None, False)]
        assert game.book.issued_to(game.own(1)) == (second,)

    def test_a_train_sent_counts_though_the_observation_after_does_not_show_it(self) -> None:
        """On the ladder the observation after an order can still show the structure idle (in game)."""
        game = _holding([ActionResult.SUCCESS], [ActionResult.SUCCESS])
        game.observe(0, _barracks(1))
        game.book.issue(game.own(1), _TRAIN_MARINE)
        game.book.issue(game.own(1), _TRAIN_MARINE)
        game.flush()

        game.observe(16, _barracks(1))
        assert game.flush() is None
        game.observe(32, _barracks(1, _training(0.1)))
        assert game.flush() is None
        game.observe(48, _barracks(1))

        assert _sent(game.flush()) == [(_TRAIN_MARINE, [1], None, False)]

    def test_a_finished_reactor_runs_two_at_once(self) -> None:
        game = _holding([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, *_with_reactor(1, 2))

        for _ in range(3):
            game.book.issue(game.own(1), _TRAIN_MARINE)

        assert len(_sent(game.flush())) == 2
        assert len(game.book.issued_to(game.own(1))) == 1

    def test_an_unfinished_reactor_runs_one(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, *_with_reactor(1, 2, progress=0.5))

        for _ in range(2):
            game.book.issue(game.own(1), _TRAIN_MARINE)

        assert len(_sent(game.flush())) == 1

    def test_a_research_waits_behind_a_research(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.ENGINEERING_BAY, at=(12.0, 12.0), orders=[_making(_WEAPONS_1)]))
        game.book.issue(game.own(1), _WEAPONS_2)

        assert game.flush() is None

        game.observe(16, make_unit(1, UnitTypeId.ENGINEERING_BAY, at=(12.0, 12.0)))

        assert _sent(game.flush()) == [(_WEAPONS_2, [1], None, False)]

    def test_a_train_to_a_lifted_barracks_waits_until_it_lands(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _flying_barracks(1))
        train = game.book.issue(game.own(1), _TRAIN_MARINE)

        assert game.flush() is None
        assert game.book.issued_to(game.own(1)) == (train,)

        game.observe(16, make_unit(1, UnitTypeId.BARRACKS, at=_SITE))

        assert _sent(game.flush()) == [(_TRAIN_MARINE, [1], None, False)]

    def test_a_warp_in_is_not_held_since_a_warp_gate_keeps_no_queue(self) -> None:
        game = _holding([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.WARP_GATE, at=(12.0, 12.0)))

        game.book.issue(game.own(1), _WARP_IN_ADEPT, target=(20.0, 20.0))
        game.book.issue(game.own(1), _WARP_IN_ADEPT, target=(21.0, 20.0))

        assert len(_sent(game.flush())) == 2
        assert game.book.issued_to(game.own(1)) == ()

    def test_what_costs_nothing_is_never_held(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _barracks(1, _training()))
        game.book.issue(game.own(1), _TRAIN_MARINE)

        game.book.issue(game.own(1), _RALLY, target=(20.0, 21.0))

        assert _sent(game.flush()) == [(_RALLY, [1], (20.0, 21.0), False)]


class TestWhatReplacesHeldItems:
    def test_an_unqueued_move_drops_a_held_build(self) -> None:
        game = _holding([ActionResult.SUCCESS], [ActionResult.SUCCESS])
        game.observe(0, _scv(1))
        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)
        game.flush()
        game.observe(16, _scv(1, _moving(_SITE), at=(20.0, 20.0)))

        game.book.issue(game.own(1), _MOVE, target=(5.0, 5.0))

        assert _sent(game.flush()) == [(_MOVE, [1], (5.0, 5.0), False)]
        assert game.book.issued_to(game.own(1)) == ()

    def test_a_move_to_where_the_worker_already_walks_drops_the_build_and_is_not_sent(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _scv(1))
        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)
        game.flush()
        game.observe(16, _scv(1, _moving(_SITE), at=(20.0, 20.0)))

        game.book.issue(game.own(1), _MOVE, target=_SITE)

        assert game.flush() is None
        assert game.book.issued_to(game.own(1)) == ()

    def test_a_queued_move_goes_behind_a_held_build(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _scv(1))
        build = game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)
        game.flush()
        game.observe(16, _scv(1, _moving(_SITE), at=(20.0, 20.0)))

        behind = game.book.issue(game.own(1), _MOVE, target=(5.0, 5.0), queued=True)

        assert game.flush() is None
        assert game.book.issued_to(game.own(1)) == (build, behind)

    def test_withdrawing_a_held_order_lets_the_next_move_up(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _barracks(1, _training()))
        first = game.book.issue(game.own(1), _TRAIN_MARINE)
        game.book.issue(game.own(1), _TRAIN_REAPER)
        game.flush()

        first.withdraw()
        game.observe(16, _barracks(1))

        assert _sent(game.flush()) == [(_TRAIN_REAPER, [1], None, False)]

    def test_withdrawing_a_held_build_leaves_its_worker_walking(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _scv(1))
        build = game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)
        game.flush()
        game.observe(16, _scv(1, _moving(_SITE), at=(29.0, 29.0)))

        build.withdraw()

        assert game.book.issued_to(game.own(1)) == ()
        assert game.flush() is None


class TestWhatEndsAHeldQueue:
    def test_a_refused_release_drops_everything_held_behind_it(self) -> None:
        game = _holding([ActionResult.NOT_ENOUGH_MINERALS])
        game.observe(0, _barracks(1))
        game.book.issue(game.own(1), _TRAIN_MARINE)
        game.book.issue(game.own(1), _TRAIN_MARINE)

        game.flush()

        assert game.refused() == [(1, _TRAIN_MARINE, ActionResult.NOT_ENOUGH_MINERALS)]
        assert game.book.issued_to(game.own(1)) == ()

    def test_a_unit_that_dies_drops_what_is_held_for_it(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _scv(1), _marine(2))
        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)
        game.flush()
        scv = game.own(1)

        game.observe(16, _marine(2), dead=(1,))

        assert game.book.issued_to(scv) == ()

    def test_a_unit_that_changes_hands_drops_what_is_held_for_it(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _scv(1))
        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)
        game.flush()

        game.observe(16, make_unit(1, UnitTypeId.SCV, at=(12.0, 12.0), alliance=Alliance.ENEMY))

        assert game.book._held == {}

    def test_a_worker_idle_out_of_reach_after_its_move_drops_the_build(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _scv(1))
        game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)
        game.flush()
        game.observe(16, _scv(1, _moving(_SITE), at=(20.0, 20.0)))
        game.observe(32, _scv(1, at=(24.0, 24.0)))

        assert game.flush() is None
        assert game.book.issued_to(game.own(1)) == ()

    def test_a_worker_is_not_given_up_on_while_its_move_may_not_show(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _scv(1))
        build = game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)
        game.flush()

        game.observe(16, _scv(1))

        assert game.flush() is None
        assert game.book.issued_to(game.own(1)) == (build,)

    def test_a_barracks_idle_and_still_flying_after_its_landing_drops_its_add_on(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _flying_barracks(1))
        game.book.issue(game.own(1), _BUILD_REACTOR, target=_SITE)
        game.flush()
        game.observe(16, _flying_barracks(1, _landing(), at=(25.0, 25.0)))
        game.observe(32, _flying_barracks(1, at=(28.0, 28.0)))

        assert game.flush() is None
        assert game.book.issued_to(game.own(1)) == ()

    def test_a_worker_the_observation_leaves_out_is_neither_given_its_build_nor_given_up_on(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _scv(1), _marine(2))
        build = game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)
        game.flush()
        scv = game.own(1)

        game.observe(16, _marine(2))
        game.observe(32, _marine(2))

        assert game.flush() is None
        assert game.book.issued_to(scv) == (build,)


class TestReadingHeldOrders:
    def test_what_is_held_comes_ahead_of_the_turn_s_orders_and_pending_is_the_turn_s_alone(self) -> None:
        game = _holding()
        game.observe(0, _barracks(1, _training()))
        held = game.book.issue(game.own(1), _TRAIN_MARINE)
        game.flush()
        game.observe(16, _barracks(1, _training(0.6)))

        new = game.book.issue(game.own(1), _TRAIN_REAPER)

        assert game.book.issued_to(game.own(1)) == (held, new)
        assert game.book.pending == (new,)


class TestPickingOneUnitForACostlyOrder:
    """One command naming several units has one of them carry out a build, a train or a research (in game); NachOS
    picks it, so that what it holds belongs to one unit."""

    def test_a_train_to_several_structures_is_given_to_one(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _barracks(1), _barracks(2))

        order = game.book.issue([game.own(1), game.own(2)], _TRAIN_MARINE)

        assert order.units == (game.own(1),)
        assert _sent(game.flush()) == [(_TRAIN_MARINE, [1], None, False)]

    def test_the_structure_with_a_slot_free_first_is_picked(self) -> None:
        game = _holding()
        game.observe(0, _barracks(1, _training(0.1)), _barracks(2, _training(0.9)))

        order = game.book.issue([game.own(1), game.own(2)], _TRAIN_MARINE)

        assert order.units == (game.own(2),)

    def test_the_turn_s_earlier_orders_count(self) -> None:
        game = _holding([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _barracks(1), _barracks(2))

        first = game.book.issue([game.own(1), game.own(2)], _TRAIN_MARINE)
        second = game.book.issue([game.own(1), game.own(2)], _TRAIN_MARINE)

        assert (first.units, second.units) == ((game.own(1),), (game.own(2),))

    def test_the_nearest_worker_is_picked(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _scv(1, at=(10.0, 30.0)), _scv(2, at=(24.0, 30.0)))

        order = game.book.issue([game.own(1), game.own(2)], _BUILD_DEPOT, target=_SITE)

        assert order.units == (game.own(2),)
        assert _sent(game.flush()) == [(_MOVE, [2], _SITE, False)]

    def test_a_busy_worker_is_picked_only_if_all_are_and_then_goes_on_with_what_it_has(self) -> None:
        game = _holding()
        game.observe(0, _scv(1, _building((26.0, 30.0)), at=(26.0, 30.0)), _scv(2, at=(10.0, 30.0)))

        assert game.book.issue([game.own(1), game.own(2)], _BUILD_DEPOT, target=_SITE).units == (game.own(2),)

        game.book.issue(game.own(2), _BUILD_DEPOT, target=(5.0, 5.0))
        last = game.book.issue([game.own(1), game.own(2)], _BUILD_DEPOT, target=(40.0, 40.0))

        assert last.units == (game.own(1),)
        assert last.queued

    def test_an_order_that_costs_nothing_still_goes_to_every_unit(self) -> None:
        game = _holding([ActionResult.SUCCESS])
        game.observe(0, _scv(1), _scv(2))

        order = game.book.issue([game.own(1), game.own(2)], _MOVE, target=_SITE)

        assert order.units == (game.own(1), game.own(2))


class TestSplittingAQueuedGroupOrder:
    def test_it_goes_now_to_the_units_holding_nothing_and_to_the_rest_behind_what_they_hold(self) -> None:
        game = _holding([ActionResult.SUCCESS], [ActionResult.SUCCESS], [ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _scv(1), _scv(2, at=(12.0, 10.0)))
        build = game.book.issue(game.own(1), _BUILD_DEPOT, target=_SITE)
        game.flush()
        game.observe(16, _scv(1, _moving(_SITE), at=(20.0, 20.0)), _scv(2, at=(12.0, 10.0)))

        rally = game.book.issue([game.own(1), game.own(2)], _MOVE, target=(5.0, 5.0), queued=True)

        assert _sent(game.flush()) == [(_MOVE, [2], (5.0, 5.0), True)]
        assert game.book.issued_to(game.own(1)) == (build, rally)

        game.observe(32, _scv(1, _moving(_SITE), at=(29.0, 29.0)), _scv(2, _moving((5.0, 5.0))))

        assert _sent(game.flush()) == [(_BUILD_DEPOT, [1], _SITE, False), (_MOVE, [1], (5.0, 5.0), True)]


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
        self.moved: Order[str] | None = None
        self.stim: Order[None] | None = None
        self.stimmed_move: Order[None] | None = None
        self.at_a_dead_tag: Order[None] | None = None
        self.killed: OwnUnit[Any] | None = None

    def turn(self, event: TurnEvent) -> None:
        """Give the next order not yet given, one per turn."""
        api = self.api
        marines = api.units.own.of_type(UnitTypeId.GHOST)
        middle = api.map.playable_area.center
        if not marines:
            if event.step < 64:
                api.client.debug([_create(UnitTypeId.GHOST, middle, self.player, quantity=2)])
            return
        if self.moved is None and len(marines) >= 2:
            first, second = marines[0], marines[1]
            self.moved = api.orders.issue(first, _MOVE, target=middle + (6.0, 0.0), data="scouting")
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
        self.loads: list[Order[None]] = []
        self.move: Order[None] | None = None
        self.unload: Order[None] | None = None
        self.unloaded_at: dict[int, Point] = {}
        self.reported: list[tuple[int, list[str]]] = []

    def turn(self, event: TurnEvent) -> None:
        api = self.api
        medivacs = api.units.own.of_type(UnitTypeId.MEDIVAC)
        marines = api.units.own.of_type(UnitTypeId.MARINE)
        middle = api.map.playable_area.center
        if self.unload is not None:
            self.reported.append((event.step, [order.ability.name for medivac in medivacs for order in medivac.orders]))
            return
        if len(medivacs) < 2:
            if event.step < 64:
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
        self.sieges: list[Order[None]] = []
        self.reported: list[tuple[int, UnitTypeId, list[tuple[AbilityId, bool]]]] = []

    def turn(self, event: TurnEvent) -> None:
        api = self.api
        liberators = api.units.own.of_type([UnitTypeId.LIBERATOR, UnitTypeId.LIBERATOR_SIEGED])
        if not liberators:
            if event.step < 64:
                api.client.debug([_create(UnitTypeId.LIBERATOR, api.map.playable_area.center, self.player, quantity=1)])
            return
        liberator = liberators[0]
        orders = [(order.ability, order.target is liberator) for order in liberator.orders]
        self.reported.append((event.step, liberator.type_id, orders))
        if self.zone is None:
            self.zone = liberator.position + (4.0, 0.0)
        if self._siege_due(event.step):
            self.sieges.append(api.orders.issue(liberator, _SIEGE, target=self.zone))

    def _siege_due(self, step: int) -> bool:
        """Whether to order the siege now: at once, then on two turns from 32 steps later, while the liberator still
        shows the first."""
        return not self.sieges or (len(self.sieges) < 3 and step >= self.sieges[0].issued_step + 32)


class _GroupSiegingBot:
    """A bot that sieges a tank and a liberator in one order aimed at the liberator's zone, and once both are sieged,
    unsieges them in one order."""

    def __init__(self, api: Api, player: int) -> None:
        self.api = api
        self.player = player
        self.siege: Order[None] | None = None
        self.unsiege: Order[None] | None = None
        self.sieged: list[UnitTypeId] = []

    def turn(self, event: TurnEvent) -> None:
        api = self.api
        units = api.units.own.of_type(_SIEGE_FORMS)
        middle = api.map.playable_area.center
        if len(units) < 2:
            if event.step < 64:
                api.client.debug(
                    [
                        _create(UnitTypeId.SIEGE_TANK, middle, self.player, quantity=1),
                        _create(UnitTypeId.LIBERATOR, middle + (0.0, 3.0), self.player, quantity=1),
                    ]
                )
            return
        if self.siege is None:
            liberator = next(unit for unit in units if unit.type_id is UnitTypeId.LIBERATOR)
            self.siege = api.orders.issue(units, _SIEGE, target=liberator.position + (4.0, 0.0))
        elif self.unsiege is None and {unit.type_id for unit in units} == _SIEGED:
            self.sieged = sorted(unit.type_id for unit in units)
            self.unsiege = api.orders.issue(units, _UNSIEGE)


class _GroupUnloadingBot:
    """A bot that loads a bunker and a medivac with a marine each, then unloads both in one order."""

    def __init__(self, api: Api, player: int) -> None:
        self.api = api
        self.player = player
        self.loads: list[Order[None]] = []
        self.unload: Order[None] | None = None
        self.marines_left_out = 0

    def turn(self, event: TurnEvent) -> None:
        api = self.api
        transports = api.units.own.of_type([UnitTypeId.BUNKER, UnitTypeId.MEDIVAC])
        marines = api.units.own.of_type(UnitTypeId.MARINE)
        middle = api.map.playable_area.center
        if len(transports) < 2:
            if event.step < 64:
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


class _HeldBuildBot:
    """A bot that, once it has the minerals, gives every SCV one build, and records each turn after: the step, the
    minerals, how far the SCV picked stands from the site, its edge for a geyser, and whether the build is still held.
    It builds a supply depot on open ground toward the middle of the map, or with `refinery` a refinery on the geyser
    nearest its command center."""

    refinery = False

    def __init__(self, api: Api, player: int) -> None:
        self.api = api
        self.player = player
        self.build: Order[None] | None = None
        self.site: Point | Unit[Any] | None = None
        self.turns: list[tuple[int, float, float, bool]] = []

    def turn(self, event: TurnEvent) -> None:
        api = self.api
        if self.build is None or self.site is None:
            ability = _BUILD_REFINERY if self.refinery else _BUILD_DEPOT
            if api.resources.minerals >= api.data.abilities[ability].cost.minerals:
                self.site = _geyser(api) if self.refinery else _depot_site(api)
                self.build = api.orders.issue(api.units.own.of_type(UnitTypeId.SCV), ability, target=self.site)
            return
        (scv,) = self.build.units
        if isinstance(self.site, Unit):
            distance = scv.position.distance_to(self.site.position) - self.site.radius
        else:
            distance = scv.position.distance_to(self.site)
        held = self.build in api.orders.issued_to(scv)
        self.turns.append((event.step, api.resources.minerals, distance, held))


class _HeldRefineryBot(_HeldBuildBot):
    refinery = True


def _command_center_at(api: Api) -> Point:
    """Where this player's command center stands."""
    return api.units.own.of_type(UnitTypeId.COMMAND_CENTER)[0].position


def _depot_site(api: Api) -> Point:
    """A supply depot's site on open ground, 8 to 16 from the command center toward the middle of the map."""
    start, middle = _command_center_at(api), api.map.playable_area.center
    for distance in range(8, 17):
        ahead = start.towards(middle, float(distance))
        site = Point((float(round(ahead.x)), float(round(ahead.y))))
        tiles = [site + (dx, dy) for dx in (-0.5, 0.5) for dy in (-0.5, 0.5)]
        if all(api.map.placement[tile] and api.map.pathing[tile] for tile in tiles):
            return site
    raise AssertionError(f"no open ground for a depot toward the middle from {start}")


def _geyser(api: Api) -> Unit[Any]:
    """The geyser nearest this player's command center."""
    return api.units.neutral.filter(lambda unit: unit.type_data.has_vespene).closest_to(_command_center_at(api))


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
        assert bot.moved.data == "scouting"
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
        print("sieges:", [order.issued_step for order in bot.sieges], "refused or given up:", failures)
        refused = [(failure.step, failure.ability, failure.action_result) for failure in failures]
        assert refused == [(order.issued_step, _SIEGE, ActionResult.NOT_SUPPORTED) for order in bot.sieges[1:]]
        # Each order as its ability and whether it is aimed at the liberator itself.
        (first, *_) = (orders for _, unit_type, orders in bot.reported if unit_type is UnitTypeId.LIBERATOR_SIEGED)
        assert first == [(_SIEGE, True)]

    def test_a_tank_and_a_liberator_siege_and_unsiege_in_one_order_each(self) -> None:
        bot, failures = _play_a_minute(_GroupSiegingBot)

        assert bot.siege is not None and bot.unsiege is not None
        print("refused or given up:", failures)
        assert bot.sieged == sorted(_SIEGED)
        assert not [failure for failure in failures if failure.ability in (_SIEGE, _UNSIEGE)]
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

    @pytest.mark.parametrize("make_bot", [_HeldBuildBot, _HeldRefineryBot], ids=["depot", "refinery"])
    def test_a_held_build_takes_no_minerals_until_its_worker_is_within_reach(
        self, make_bot: type[_HeldBuildBot]
    ) -> None:
        bot, failures = _play_a_minute(make_bot)

        print("the build, turn by turn (step, minerals, distance, held):", bot.turns)
        print("refused or given up:", failures)
        assert bot.build is not None
        assert len(bot.build.units) == 1
        held = [turn for turn in bot.turns if turn[3]]
        after = bot.turns[len(held) : len(held) + 3]
        assert held
        assert all(turn[3] for turn in bot.turns[: len(held)])
        assert all(later[1] >= earlier[1] for earlier, later in itertools.pairwise(held))
        assert held[-1][2] <= 2.5
        assert min(minerals for _, minerals, _, _ in after) <= held[-1][1] - 50
        assert not failures
        built = UnitTypeId.REFINERY if make_bot.refinery else UnitTypeId.SUPPLY_DEPOT
        assert bot.api.units.own.of_type(built)


def _play_a_minute[
    BotT: (_OrderingBot, _UnloadingBot, _SiegingBot, _GroupSiegingBot, _GroupUnloadingBot, _HeldBuildBot)
](
    make_bot: type[BotT],
) -> tuple[BotT, list[ActionFailure]]:
    """Play a minute of a game against a very easy computer with the bot `make_bot` makes, and return the bot and
    what `api.action_failures` listed turn by turn."""
    try:
        game_map = MapFile.find("PylonAIE_v4")
    except MapNotFoundError as missing:
        pytest.skip(str(missing))
    with (
        GameProcess.launch(window=(640, 480)) as process,
        closing(Client(WebSocketTransport.connect(process.url))) as client,
    ):
        client.create_game(game_map.path, [Participant(), Computer(Race.ZERG, Difficulty.VERY_EASY)])
        player = client.join_game(Race.TERRAN)
        api = Api()
        bot = make_bot(api, player)
        failures: list[ActionFailure] = []
        api.events.on(TurnEvent)(lambda _: failures.extend(api.action_failures))
        api.events.on(TurnEvent)(bot.turn)
        api.play(client, steps_per_turn=8, time_limit=60)
    return bot, failures
