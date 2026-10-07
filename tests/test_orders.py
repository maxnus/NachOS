"""The orders a bot gives: what goes out in a turn's request, and the game's answer to each."""

# Each `type: ignore` below marks a call the type checker must reject, so an unneeded one fails.
# pyright: reportUnnecessaryTypeIgnoreComment=true

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
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.ids.raw import RawAbilityId
from sc2nachos.launch import GameProcess, MapFile, MapNotFoundError
from sc2nachos.match import Computer, Difficulty, Participant, Race
from sc2nachos.orders import Order, OrderBook, OrderState
from sc2nachos.protocol import Client, WebSocketTransport
from sc2nachos.state import ActionResult, UnknownActionResultError
from sc2nachos.units import OwnUnit
from sc2nachos.units._tracking import _Tracker
from support import make_client, make_game_info, make_observation, make_response, make_tables, make_unit

_MOVE = AbilityId.GENERAL_MOVE
# The id a unit reports a move under, which reads as the move.
_MOVE_RUNS_AS = RawAbilityId.Move_Move
_ATTACK = AbilityId.GENERAL_ATTACK
_STIM = AbilityId.GENERAL_STIM
_HOLD_FIRE = AbilityId.GENERAL_HOLD_FIRE_ON
_CREEP_TUMOR = AbilityId.GENERAL_BUILD_CREEP_TUMOR
_TRAIN_MARINE = AbilityId.BARRACKS_TRAIN_MARINE
_TRAIN_REAPER = AbilityId.BARRACKS_TRAIN_REAPER
_RALLY = AbilityId.GENERAL_RALLY_UNITS
_SMART = AbilityId.GENERAL_SMART
_UNLOAD = AbilityId.GENERAL_UNLOAD
_TRAIN_SCV = AbilityId.COMMAND_CENTER_TRAIN_SCV
_UNLOAD_AT = AbilityId.GENERAL_UNLOAD_AT
_SIEGE = AbilityId.GENERAL_SIEGE
_UNSIEGE = AbilityId.GENERAL_UNSIEGE
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
        self.tracker = _Tracker(tables, Enemy())
        self.map = GameMap(make_game_info())
        self.book = OrderBook(tables)

    def observe(self, step: int, *units: raw_pb2.Unit, dead: tuple[int, ...] = ()) -> None:
        """Take in an observation of `units` at `step`, in which the units under the tags `dead` died."""
        observation = make_observation(step, units=units, dead=dead)
        self.tracker.update(observation.observation.raw_data, step)
        changes = self.tracker.last_changes
        self.book._observe(step, [*changes.units_died, *changes.units_found_dead])

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
        assert order.state is OrderState.SENT
        assert order.action_result is ActionResult.SUCCESS

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

        assert repr(one) == f"Order(GENERAL_MOVE, {game.own(1)!r}, pending)"
        assert repr(both) == "Order(GENERAL_MOVE, 2 units, pending)"

    def test_an_ability_aimed_at_what_it_cannot_take_is_a_mistake(self) -> None:
        """The three cases the game was seen to answer `ERROR`, which NachOS now rejects itself."""
        game = _Game()
        game.observe(0, _marine(1))
        marine = game.own(1)

        with pytest.raises(TypeError, match="GENERAL_STIM takes no target"):
            game.book.issue(marine, _STIM, target=(20.0, 21.0))
        with pytest.raises(TypeError, match="GENERAL_MOVE takes a point or a unit"):
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
        first = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        second = game.book.issue(game.own(1), _MOVE, target=(30.0, 31.0))

        (command,) = _commands(game.flush())

        assert command.target_world_space_pos.x == 30.0
        assert first.state is OrderState.OVERRIDDEN
        assert second.state is OrderState.SENT

    def test_an_ability_carried_out_at_once_neither_overrides_nor_is_overridden(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        stim = game.book.issue(game.own(1), _STIM)
        move = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        sent = [command.ability_id for command in _commands(game.flush())]

        assert sent == [_STIM, _MOVE]
        assert (stim.state, move.state) == (OrderState.SENT, OrderState.SENT)

    def test_an_order_keeps_the_units_a_later_order_did_not_take(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2))
        group = game.book.issue([game.own(1), game.own(2)], _MOVE, target=(20.0, 21.0))
        one = game.book.issue(game.own(2), _ATTACK, target=(30.0, 31.0))

        move, attack = _commands(game.flush())

        assert list(move.unit_tags) == [1]
        assert list(attack.unit_tags) == [2]
        assert (group.state, one.state) == (OrderState.SENT, OrderState.SENT)

    def test_an_order_overridden_for_every_unit_is_never_sent(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2))
        group = game.book.issue([game.own(1), game.own(2)], _MOVE, target=(20.0, 21.0))
        game.book.issue([game.own(2), game.own(1)], _ATTACK, target=(30.0, 31.0))

        assert len(_commands(game.flush())) == 1
        assert group.state is OrderState.OVERRIDDEN

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

    def test_it_is_not_sent_and_reads_redundant(self) -> None:
        game = _Game()
        game.observe(0, _marine(1, _moving((20.0, 21.0)), _moving((30.0, 31.0))))

        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0), data="again")

        assert game.flush() is None
        assert order.state is OrderState.REDUNDANT
        assert order.action_result is None
        assert order.data == "again"

    def test_it_counts_as_issued_until_the_turn_is_sent(self) -> None:
        game = _Game()
        game.observe(0, _marine(1, _moving((20.0, 21.0))))

        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        assert order.state is OrderState.PENDING
        assert game.book.issued_to(game.own(1)) == (order,)
        assert game.book.pending == (order,)

    def test_each_turn_s_call_is_an_order_of_its_own(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        first = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()
        game.observe(16, _marine(1, _moving((20.0, 21.0))))

        second = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()

        assert second is not first
        assert (first.state, second.state) == (OrderState.SENT, OrderState.REDUNDANT)

    def test_it_takes_its_unit_from_the_turn_s_earlier_orders(self) -> None:
        game = _Game()
        game.observe(0, _marine(1, _moving((20.0, 21.0))))
        attack = game.book.issue(game.own(1), _ATTACK, target=(30.0, 31.0))
        move = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        assert game.flush() is None
        assert (attack.state, move.state) == (OrderState.OVERRIDDEN, OrderState.REDUNDANT)

    def test_a_later_order_to_its_unit_overrides_it(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0))))
        move = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        attack = game.book.issue(game.own(1), _ATTACK, target=(30.0, 31.0))

        (command,) = _commands(game.flush())

        assert command.ability_id == _ATTACK
        assert (move.state, attack.state) == (OrderState.OVERRIDDEN, OrderState.SENT)

    def test_withdrawing_it_lets_the_turn_s_earlier_order_go_out(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0))))
        attack = game.book.issue(game.own(1), _ATTACK, target=(30.0, 31.0))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0)).withdraw()

        (command,) = _commands(game.flush())

        assert command.ability_id == _ATTACK
        assert attack.state is OrderState.SENT

    def test_only_the_units_already_carrying_it_out_are_left_out(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0))), _marine(2))
        order = game.book.issue([game.own(1), game.own(2)], _MOVE, target=(20.0, 21.0))

        (command,) = _commands(game.flush())

        assert list(command.unit_tags) == [2]
        assert order.state is OrderState.SENT

    def test_a_point_the_game_cut_down_to_its_own_lattice_is_the_same_point(self) -> None:
        game = _Game()
        game.observe(0, _marine(1, _moving((157.123291, 3.0))))

        order = game.book.issue(game.own(1), _MOVE, target=(157.123456, 3.0))

        assert game.flush() is None
        assert order.state is OrderState.REDUNDANT

    def test_a_point_with_a_height_is_the_same_point(self) -> None:
        game = _Game()
        game.observe(0, _marine(1, _moving((20.0, 21.0))))

        order = game.book.issue(game.own(1), _MOVE, target=Point3D((20.0, 21.0, 5.0)))

        assert game.flush() is None
        assert order.state is OrderState.REDUNDANT

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

        move = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        (command,) = _commands(game.flush())
        assert command.ability_id == _MOVE
        assert move.state is OrderState.SENT

    def test_a_repeat_of_the_order_sent_last_turn_is_redundant_though_it_does_not_show_yet(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.book.issue(game.own(1), _ATTACK, target=(30.0, 31.0), queued=True)
        game.flush()
        game.observe(16, _marine(1))

        again = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        assert game.flush() is None
        assert again.state is OrderState.REDUNDANT

    def test_an_order_finished_within_a_turn_is_still_taken_for_what_the_unit_is_doing_on_the_next(self) -> None:
        """In a stepped game the next observation already shows the unit idle, but on the ladder it might not."""
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()
        game.observe(16, _marine(1, at=(20.0, 21.0)))

        again = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        assert game.flush() is None
        assert again.state is OrderState.REDUNDANT

    def test_two_observations_on_the_unit_s_own_orders_decide(self) -> None:
        game = _Game([ActionResult.SUCCESS], [ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()
        game.observe(16, _marine(1, _moving((20.0, 21.0))))
        game.observe(32, _marine(1, at=(20.0, 21.0)))

        again = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        assert len(_commands(game.flush())) == 1
        assert again.state is OrderState.SENT

    def test_an_order_the_game_gave_decides_once_the_last_one_sent_could_show(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2, at=(30.0, 30.0)))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()
        game.observe(16, _marine(1, _moving((20.0, 21.0))), _marine(2, at=(30.0, 30.0)))
        attacking = raw_pb2.UnitOrder(ability_id=_ATTACK, target_unit_tag=2)
        game.observe(32, _marine(1, attacking), _marine(2, at=(30.0, 30.0)))

        order = game.book.issue(game.own(1), _ATTACK, target=game.own(2))

        assert game.flush() is None
        assert order.state is OrderState.REDUNDANT

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

        order = game.book.issue(marine, _MOVE, target=(20.0, 21.0))

        assert len(_commands(game.flush())) == 1
        assert not marine.is_dead
        assert order.state is OrderState.SENT


class TestSieging:
    """`GENERAL_SIEGE` goes out as each type's own siege: a tank's siege mode aimed at nothing, a liberator's defender
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

        order = game.book.issue(game.own(1), _SIEGE, target=(20.0, 21.0))

        (command,) = _commands(game.flush())
        assert command.ability_id == RawAbilityId.Morph_LiberatorAGMode
        assert order.state is OrderState.REFUSED

    def test_a_tank_and_a_liberator_go_out_each_as_its_own_siege(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.SIEGE_TANK), make_unit(2, UnitTypeId.LIBERATOR))

        order = game.book.issue([game.own(1), game.own(2)], _SIEGE, target=(20.0, 21.0))

        tank, liberator = _commands(game.flush())
        assert (tank.ability_id, list(tank.unit_tags)) == (RawAbilityId.SiegeMode_SiegeMode, [1])
        assert not tank.HasField("target_world_space_pos")
        assert (liberator.ability_id, list(liberator.unit_tags)) == (RawAbilityId.Morph_LiberatorAGMode, [2])
        assert (liberator.target_world_space_pos.x, liberator.target_world_space_pos.y) == (20.0, 21.0)
        assert order.state is OrderState.SENT

    def test_a_tank_alone_needs_no_point(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.SIEGE_TANK))

        game.book.issue(game.own(1), _SIEGE)

        (command,) = _commands(game.flush())
        assert command.ability_id == RawAbilityId.SiegeMode_SiegeMode

    def test_a_liberator_needs_a_point(self) -> None:
        game = _Game()
        game.observe(0, make_unit(1, UnitTypeId.SIEGE_TANK), make_unit(2, UnitTypeId.LIBERATOR))

        with pytest.raises(TypeError, match="GENERAL_SIEGE takes a point for LIBERATOR"):
            game.book.issue([game.own(1), game.own(2)], _SIEGE)

    def test_a_type_with_no_siege_is_refused_at_the_call(self) -> None:
        game = _Game()
        game.observe(0, _marine(1))

        with pytest.raises(ValueError, match="GENERAL_SIEGE has nothing to be sent as for MARINE"):
            game.book.issue(game.own(1), _SIEGE)

    def test_a_game_whose_tables_lack_a_siege_refuses_it_at_the_call(self) -> None:
        game = _Game(tables=make_tables(data_pb2.UnitTypeData(unit_id=UnitTypeId.SIEGE_TANK)))
        game.observe(0, make_unit(1, UnitTypeId.SIEGE_TANK))

        with pytest.raises(ValueError, match="GENERAL_SIEGE has nothing in this game's tables to be sent as"):
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
        move = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        hold = game.book.issue([game.own(1), game.own(2)], _HOLD_FIRE)

        sent_move, sent_hold = _commands(game.flush())

        assert hold.order_behavior is OrderBehavior.REPLACES
        assert (move.state, hold.state) == (OrderState.SENT, OrderState.SENT)
        assert list(sent_move.unit_tags) == [1]
        assert list(sent_hold.unit_tags) == [1, 2]

    def test_it_takes_a_unit_it_replaces_the_orders_of_from_the_order_it_was_given_before(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.GHOST), make_unit(2, UnitTypeId.LURKER_BURROWED))
        attack = game.book.issue(game.own(2), _ATTACK, target=(20.0, 21.0))
        game.book.issue([game.own(1), game.own(2)], _HOLD_FIRE)

        (sent,) = _commands(game.flush())

        assert sent.ability_id == _HOLD_FIRE
        assert attack.state is OrderState.OVERRIDDEN

    def test_a_later_order_takes_only_the_units_whose_orders_it_replaces(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.GHOST), make_unit(2, UnitTypeId.LURKER_BURROWED))
        attack = game.book.issue([game.own(1), game.own(2)], _ATTACK, target=(20.0, 21.0))
        game.book.issue([game.own(1), game.own(2)], _HOLD_FIRE)

        sent_attack, sent_hold = _commands(game.flush())

        assert attack.state is OrderState.SENT
        assert list(sent_attack.unit_tags) == [1]
        assert list(sent_hold.unit_tags) == [1, 2]

    def test_a_queens_creep_tumor_replaces_her_orders_though_a_tumors_does_not(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.QUEEN))
        move = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        tumor = game.book.issue(game.own(1), _CREEP_TUMOR, target=(30.0, 31.0))

        (sent,) = _commands(game.flush())

        assert sent.ability_id == _CREEP_TUMOR
        assert tumor.order_behavior is OrderBehavior.REPLACES
        assert move.state is OrderState.OVERRIDDEN


def _medivac(tag: int, *orders: raw_pb2.UnitOrder) -> raw_pb2.Unit:
    """One of this player's medivacs, carrying out `orders`."""
    return make_unit(tag, UnitTypeId.MEDIVAC, at=(14.0, 14.0), orders=orders)


class TestUnloadingWhereTheTransportIs:
    """`GENERAL_UNLOAD` puts everyone down where the transport is: a bunker takes it as it is, and a medivac, which
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
        assert order.state is OrderState.SENT

    def test_a_bunker_and_a_medivac_go_out_each_as_it_takes_it(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.BUNKER), _medivac(2))

        game.book.issue([game.own(1), game.own(2)], _UNLOAD)

        sent = [(c.ability_id, list(c.unit_tags), c.target_unit_tag) for c in _commands(game.flush())]
        assert sent == [(_UNLOAD, [1], 0), (_UNLOAD_AT, [2], 2)]

    def test_a_move_in_the_same_turn_goes_out_too(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _medivac(1))

        move = game.book.issue(game.own(1), _MOVE, target=(30.0, 31.0))
        unload = game.book.issue(game.own(1), _UNLOAD)

        assert [command.ability_id for command in _commands(game.flush())] == [_MOVE, _UNLOAD_AT]
        assert (move.state, unload.state) == (OrderState.SENT, OrderState.SENT)

    def test_a_group_is_sent_one_command_a_transport_and_taken_if_any_takes_it(self) -> None:
        """One command naming several medivacs is answered `SUCCESS` if any of them takes it (in game)."""
        game = _Game([ActionResult.ERROR, ActionResult.SUCCESS, ActionResult.ERROR])
        game.observe(0, _medivac(1), _medivac(2), _medivac(3))

        order = game.book.issue([game.own(1), game.own(2), game.own(3)], _UNLOAD)

        commands = _commands(game.flush())
        assert [(command.ability_id, list(command.unit_tags), command.target_unit_tag) for command in commands] == [
            (_UNLOAD_AT, [1], 1),
            (_UNLOAD_AT, [2], 2),
            (_UNLOAD_AT, [3], 3),
        ]
        assert order.state is OrderState.SENT
        assert order.action_result is ActionResult.SUCCESS

    def test_a_group_every_transport_refuses_is_refused_with_the_first_answer(self) -> None:
        game = _Game([ActionResult.NOT_SUPPORTED, ActionResult.ERROR])
        game.observe(0, _medivac(1), _medivac(2))

        order = game.book.issue([game.own(1), game.own(2)], _UNLOAD)
        game.flush()

        assert order.state is OrderState.REFUSED
        assert order.action_result is ActionResult.NOT_SUPPORTED

    def test_an_unload_at_a_sibling_transport_is_fine_for_a_transport_ordered_alone(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _medivac(1), _medivac(2))

        order = game.book.issue(game.own(1), _UNLOAD_AT, target=game.own(2))

        (command,) = _commands(game.flush())
        assert command.target_unit_tag == 2
        assert order.state is OrderState.SENT

    def test_an_order_after_it_is_answered_by_its_own_result(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS, ActionResult.NOT_SUPPORTED])
        game.observe(0, _medivac(1), _medivac(2), _marine(3))

        game.book.issue([game.own(1), game.own(2)], _UNLOAD)
        stim = game.book.issue(game.own(3), _STIM)

        assert len(_commands(game.flush())) == 3
        assert stim.action_result is ActionResult.NOT_SUPPORTED

    def test_an_unload_at_aimed_at_the_transport_itself_is_refused_at_the_call(self) -> None:
        game = _Game()
        game.observe(0, _medivac(1), _medivac(2))

        with pytest.raises(TypeError, match="GENERAL_UNLOAD_AT aimed at the unit itself is GENERAL_UNLOAD"):
            game.book.issue([game.own(1), game.own(2)], _UNLOAD_AT, target=game.own(2))

    def test_an_unload_at_a_point_still_replaces_a_move(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _medivac(1))

        move = game.book.issue(game.own(1), _MOVE, target=(30.0, 31.0))
        game.book.issue(game.own(1), _UNLOAD_AT, target=(20.0, 21.0))

        (command,) = _commands(game.flush())
        assert command.ability_id == _UNLOAD_AT
        assert move.state is OrderState.OVERRIDDEN


class TestForcingAnOrder:
    """`force` sends an order to a unit already carrying it out, which drops what it has queued (in game)."""

    def test_an_order_the_unit_is_already_carrying_out_is_sent(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0)), _moving((30.0, 31.0))))

        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0), force=True)

        (command,) = _commands(game.flush())
        assert (command.target_world_space_pos.x, command.target_world_space_pos.y) == (20.0, 21.0)
        assert not command.queue_command
        assert order.state is OrderState.SENT

    def test_the_order_sent_last_turn_is_sent_again_though_it_does_not_show_yet(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS], [ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.book.issue(game.own(1), _MOVE, target=(30.0, 31.0), queued=True)
        game.flush()
        game.observe(16, _marine(1))

        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0), force=True)

        assert len(_commands(game.flush())) == 1
        assert order.state is OrderState.SENT

    def test_every_unit_of_a_group_is_sent_it(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0))), _marine(2))

        game.book.issue([game.own(1), game.own(2)], _MOVE, target=(20.0, 21.0), force=True)

        (command,) = _commands(game.flush())
        assert list(command.unit_tags) == [1, 2]

    def test_a_later_order_still_overrides_it(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0)), _moving((30.0, 31.0))))

        forced = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0), force=True)
        game.book.issue(game.own(1), _ATTACK, target=(40.0, 41.0))

        (command,) = _commands(game.flush())
        assert command.ability_id == _ATTACK
        assert forced.state is OrderState.OVERRIDDEN

    def test_it_is_sent_once_for_its_turn_only(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0))))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0), force=True)
        game.flush()
        game.observe(16, _marine(1, _moving((20.0, 21.0))))
        game.observe(32, _marine(1, _moving((20.0, 21.0))))

        again = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        assert game.flush() is None
        assert again.state is OrderState.REDUNDANT


class TestWhatBecameOfAnOrder:
    def test_an_order_the_game_refused_carries_its_verdict(self) -> None:
        game = _Game([ActionResult.NOT_SUPPORTED])
        game.observe(0, _marine(1))
        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        game.flush()

        assert order.state is OrderState.REFUSED
        assert order.action_result is ActionResult.NOT_SUPPORTED

    def test_an_order_withdrawn_before_the_turn_ends_is_never_sent(self) -> None:
        game = _Game()
        game.observe(0, _marine(1))
        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        order.withdraw()

        assert game.flush() is None
        assert order.state is OrderState.WITHDRAWN

    def test_withdrawing_an_order_already_sent_leaves_it_as_it_is(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()

        order.withdraw()

        assert order.state is OrderState.SENT


class TestOrdersThatQueue:
    """A train queues behind what a structure is making, so it is neither a duplicate nor a replacement (in game)."""

    def test_a_train_is_sent_though_the_structure_is_already_making_one(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _barracks(1, _training()))

        order = game.book.issue(game.own(1), _TRAIN_MARINE)

        (command,) = _commands(game.flush())
        assert command.ability_id == _TRAIN_MARINE
        assert order.order_behavior is OrderBehavior.QUEUES
        assert order.state is OrderState.SENT


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
        train = game.book.issue(game.own(1), _TRAIN_MARINE)
        rally = game.book.issue(game.own(1), _RALLY, target=(20.0, 21.0))

        sent = [command.ability_id for command in _commands(game.flush())]

        assert sent == [_TRAIN_MARINE, _RALLY]
        assert (train.state, rally.state) == (OrderState.SENT, OrderState.SENT)

    def test_a_smart_does_not_take_the_turn_from_a_train(self) -> None:
        """A structure goes on training through a smart, which sets its rally (in game)."""
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _barracks(1))
        train = game.book.issue(game.own(1), _TRAIN_MARINE)
        smart = game.book.issue(game.own(1), _SMART, target=(20.0, 21.0))

        sent = [command.ability_id for command in _commands(game.flush())]

        assert sent == [_TRAIN_MARINE, _SMART]
        assert (train.state, smart.state) == (OrderState.SENT, OrderState.SENT)


class TestAQueuedOrder:
    def test_a_queued_order_goes_out_beside_the_unqueued_one_it_follows(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        move = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        behind = game.book.issue(game.own(1), _ATTACK, target=(30.0, 31.0), queued=True)

        first, second = _commands(game.flush())

        assert (first.ability_id, first.queue_command) == (_MOVE, False)
        assert (second.ability_id, second.queue_command) == (_ATTACK, True)
        assert (move.state, behind.state) == (OrderState.SENT, OrderState.SENT)

    def test_an_unqueued_order_after_a_queued_one_takes_the_unit_from_nothing(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        behind = game.book.issue(game.own(1), _ATTACK, target=(30.0, 31.0), queued=True)
        move = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        assert len(_commands(game.flush())) == 2
        assert (behind.state, move.state) == (OrderState.SENT, OrderState.SENT)


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

        order = game.book.issue(game.own(1), _MOVE, target=(crossing, 3.0))

        assert game.flush() is None
        assert order.state is OrderState.REDUNDANT


class TestWhatAnOrderStopsCountingFor:
    def test_a_withdrawn_order_speaks_for_no_unit(self) -> None:
        game = _Game()
        game.observe(0, _marine(1))
        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        assert game.book.issued_to(game.own(1)) == (order,)

        order.withdraw()

        assert game.book.issued_to(game.own(1)) == ()
        assert game.book.pending == ()

    def test_withdrawing_an_order_the_game_is_done_with_keeps_how_it_ended(self) -> None:
        game = _Game([ActionResult.NOT_SUPPORTED])
        game.observe(0, _marine(1))
        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()
        assert order.state is OrderState.REFUSED

        order.withdraw()

        assert order.state is OrderState.REFUSED


class TestTellingAStructureToMakeTwo:
    def test_a_second_train_is_sent_when_the_bot_asks_for_a_place_in_the_queue(self) -> None:
        """This is how a structure with a reactor is told to make two at once (in game)."""
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _barracks(1))
        first = game.book.issue(game.own(1), _TRAIN_MARINE)
        second = game.book.issue(game.own(1), _TRAIN_MARINE, queued=True)

        one, two = _commands(game.flush())

        assert (one.ability_id, one.queue_command) == (_TRAIN_MARINE, False)
        assert (two.ability_id, two.queue_command) == (_TRAIN_MARINE, True)
        assert (first.state, second.state) == (OrderState.SENT, OrderState.SENT)

    def test_a_second_unqueued_train_takes_the_structure_from_the_first(self) -> None:
        """The game would queue it and pay for it from the step it was ordered."""
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _barracks(1))
        first = game.book.issue(game.own(1), _TRAIN_MARINE)
        second = game.book.issue(game.own(1), _TRAIN_REAPER)

        (command,) = _commands(game.flush())

        assert command.ability_id == _TRAIN_REAPER
        assert (first.state, second.state) == (OrderState.OVERRIDDEN, OrderState.SENT)


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
                    api.orders.issue(medivac, AbilityId.GENERAL_LOAD, target=marine)
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
                    api.orders.issue(transport, AbilityId.GENERAL_LOAD, target=marine)
                    for transport, marine in zip(transports, marines, strict=False)
                ]
            return
        if self.unload is None and all(transport.cargo_used > 0 for transport in transports):
            # Units made by debug show up a turn or two later, so there may be more marines than were loaded.
            self.marines_left_out = len(marines)
            self.unload = api.orders.issue(transports, _UNLOAD)


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

    def test_orders_go_out_each_turn_and_carry_the_games_answer(self) -> None:
        bot = _play_a_minute(_OrderingBot)

        assert bot.moved is not None
        assert bot.moved.state is OrderState.SENT
        assert bot.moved.action_result is ActionResult.SUCCESS
        assert bot.moved.data == "scouting"

        # Hold fire and a move to the same ghost both go out; neither overrides the other.
        assert bot.stim is not None and bot.stimmed_move is not None
        assert (bot.stim.state, bot.stimmed_move.state) == (OrderState.SENT, OrderState.SENT)

        # An order to a dead unit's tag is refused (in game).
        assert bot.at_a_dead_tag is not None
        assert bot.at_a_dead_tag.state is OrderState.REFUSED
        assert bot.at_a_dead_tag.action_result is ActionResult.ERROR

    @pytest.mark.parametrize("unload_first", [False, True], ids=["move first", "unload first"])
    def test_transports_unload_where_they_are_and_move_on_in_one_turn(self, unload_first: bool) -> None:
        bot = _play_a_minute(_UnloadingBotUnloadingFirst if unload_first else _UnloadingBot)

        print("loads:", [(order.state.name, order.action_result) for order in bot.loads])
        print("orders the medivacs showed after:", bot.reported[:6])
        assert bot.move is not None and bot.unload is not None
        assert (bot.move.state, bot.unload.state) == (OrderState.SENT, OrderState.SENT)
        api = bot.api
        marines = api.units.own.of_type(UnitTypeId.MARINE)
        assert len(marines) == 2
        for marine in marines:
            assert min(marine.position.distance_to(at) for at in bot.unloaded_at.values()) < 3.0
        for medivac in api.units.own.of_type(UnitTypeId.MEDIVAC):
            assert medivac.cargo_used == 0
            assert medivac.position.distance_to(bot.unloaded_at[medivac.tag]) > 6.0

    def test_a_liberator_sieged_at_once_shows_the_siege_aimed_at_itself_and_refuses_it_again(self) -> None:
        bot = _play_a_minute(_SiegingBot)

        print("the liberator, turn by turn:", bot.reported[:12])
        print("sieges:", [(order.issued_step, order.state.name, order.action_result) for order in bot.sieges])
        assert [order.state for order in bot.sieges] == [OrderState.SENT, OrderState.REFUSED, OrderState.REFUSED]
        assert [order.action_result for order in bot.sieges[1:]] == [ActionResult.NOT_SUPPORTED] * 2
        # Each order as its ability and whether it is aimed at the liberator itself.
        (first, *_) = (orders for _, unit_type, orders in bot.reported if unit_type is UnitTypeId.LIBERATOR_SIEGED)
        assert first == [(_SIEGE, True)]

    def test_a_tank_and_a_liberator_siege_and_unsiege_in_one_order_each(self) -> None:
        bot = _play_a_minute(_GroupSiegingBot)

        assert bot.siege is not None and bot.unsiege is not None
        print("siege:", bot.siege.state.name, bot.siege.action_result, "unsiege:", bot.unsiege.state.name)
        assert bot.sieged == sorted(_SIEGED)
        assert (bot.siege.state, bot.unsiege.state) == (OrderState.SENT, OrderState.SENT)
        units = bot.api.units.own.of_type(_SIEGE_FORMS)
        assert {unit.type_id for unit in units} == {UnitTypeId.SIEGE_TANK, UnitTypeId.LIBERATOR}

    def test_a_bunker_and_a_medivac_unload_in_one_order(self) -> None:
        bot = _play_a_minute(_GroupUnloadingBot)

        print("loads:", [(order.state.name, order.action_result) for order in bot.loads])
        assert bot.unload is not None
        assert bot.unload.state is OrderState.SENT
        transports = bot.api.units.own.of_type([UnitTypeId.BUNKER, UnitTypeId.MEDIVAC])
        assert [transport.cargo_used for transport in transports] == [0, 0]
        assert len(bot.api.units.own.of_type(UnitTypeId.MARINE)) == bot.marines_left_out + 2


def _play_a_minute[BotT: (_OrderingBot, _UnloadingBot, _SiegingBot, _GroupSiegingBot, _GroupUnloadingBot)](
    make_bot: type[BotT],
) -> BotT:
    """Play a minute of a game against a very easy computer with the bot `make_bot` makes, and return the bot."""
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
        api.events.on(TurnEvent)(bot.turn)
        api.play(client, steps_per_turn=8, time_limit=60)
    return bot
