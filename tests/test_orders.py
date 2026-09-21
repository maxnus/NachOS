"""The orders a bot gives: what goes out in a turn's request, and what the next observation makes of each."""

# Each `type: ignore` below marks a call a type checker must refuse, so one that is not needed fails.
# pyright: reportUnnecessaryTypeIgnoreComment=true

from contextlib import closing
from typing import Any

import pytest
from s2clientprotocol import common_pb2, data_pb2, debug_pb2, error_pb2, raw_pb2, sc2api_pb2

from sc2nachos import Api
from sc2nachos.enemy import Enemy
from sc2nachos.events import TurnEvent
from sc2nachos.gamedata import OrderBehavior
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point, Point3D
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.launch import GameProcess, MapFile, MapNotFoundError
from sc2nachos.match import Computer, Difficulty, Participant, Race
from sc2nachos.orders import Order, OrderBook, OrderState
from sc2nachos.protocol import Client, WebSocketTransport
from sc2nachos.state import ActionResult, UnknownActionResultError
from sc2nachos.state._state import _State
from sc2nachos.units import OwnUnit
from sc2nachos.units._tracking import _Tracker
from support import make_client, make_game_info, make_observation, make_response, make_tables, make_unit

_MOVE = AbilityId.GENERAL_MOVE
_MOVE_EXACT = AbilityId.GENERAL_MOVE_EXACT
_ATTACK = AbilityId.GENERAL_ATTACK
_STIM = AbilityId.MARINE_STIM
_HOLD_FIRE = AbilityId.GHOST_HOLD_FIRE_ON
_TRAIN_MARINE = AbilityId.BARRACKS_TRAIN_MARINE
_TRAIN_REAPER = AbilityId.BARRACKS_TRAIN_REAPER
_RALLY = AbilityId.GENERAL_RALLY
_MORPH_DRONE = AbilityId.LARVA_MORPH_DRONE
# An unset `target` reads as the first value the enum declares, which is the one for an ability aimed at nothing.
_AT_A_POINT_OR_UNIT = data_pb2.AbilityData.Target.PointOrUnit

_TABLES = make_tables(
    data_pb2.UnitTypeData(unit_id=UnitTypeId.BARRACKS, attributes=[data_pb2.Attribute.Structure]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.MARINE, attributes=[data_pb2.Attribute.Biological]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.LARVA, attributes=[data_pb2.Attribute.Biological]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.EGG, attributes=[data_pb2.Attribute.Biological]),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.DRONE, attributes=[data_pb2.Attribute.Biological]),
    abilities=[
        data_pb2.AbilityData(ability_id=_MOVE, target=_AT_A_POINT_OR_UNIT),
        data_pb2.AbilityData(ability_id=_MOVE_EXACT, target=_AT_A_POINT_OR_UNIT, remaps_to_ability_id=_MOVE),
        data_pb2.AbilityData(ability_id=_ATTACK, target=_AT_A_POINT_OR_UNIT),
        data_pb2.AbilityData(ability_id=_STIM),
        data_pb2.AbilityData(ability_id=_HOLD_FIRE),
        data_pb2.AbilityData(ability_id=_TRAIN_MARINE),
        data_pb2.AbilityData(ability_id=_TRAIN_REAPER),
        data_pb2.AbilityData(ability_id=_RALLY, target=_AT_A_POINT_OR_UNIT),
        data_pb2.AbilityData(ability_id=_MORPH_DRONE),
    ],
)


def _verdict(result: ActionResult) -> error_pb2.ActionResult.ValueType:
    """A verdict as the protocol spells it."""
    return error_pb2.ActionResult.ValueType(result)


class _Game:
    """An order book over a tracker fed one observation after another, as `Api.play` feeds it."""

    def __init__(self, *verdicts: list[ActionResult]) -> None:
        """A game whose every flush is answered with the next of `verdicts`, one for each action sent."""
        responses = [
            make_response(action=sc2api_pb2.ResponseAction(result=[_verdict(result) for result in verdict]))
            for verdict in verdicts
        ]
        self.client, self.transport = make_client(*responses)
        self.tracker = _Tracker(_TABLES, Enemy())
        self.map = GameMap(make_game_info())
        self.book = OrderBook(_TABLES)

    def observe(
        self,
        step: int,
        *units: raw_pb2.Unit,
        actions: tuple[sc2api_pb2.Action, ...] = (),
        errors: tuple[sc2api_pb2.ActionError, ...] = (),
        dead: tuple[int, ...] = (),
    ) -> None:
        """Take in an observation of `units` at `step`, and settle the orders it answers for."""
        observation = make_observation(step, units=units, actions=actions, action_errors=errors, dead=dead)
        self.tracker.update(observation.observation.raw_data, step)
        self.book._take_in(_State(observation, self.tracker, self.map), step)

    def flush(self) -> sc2api_pb2.RequestAction | None:
        """Send the turn's orders, and answer the request that went out, or `None` where none did."""
        before = len(self.transport.requests)
        self.book._send(self.client)
        sent = self.transport.requests[before:]
        return sent[0].action if sent else None

    def own(self, tag: int) -> OwnUnit[Any]:
        """The unit the game reported under `tag`, which is this player's."""
        unit = self.tracker.unit_tracker.by_tag(tag)
        assert isinstance(unit, OwnUnit)
        return unit


def _marine(tag: int, *orders: raw_pb2.UnitOrder, at: tuple[float, float] = (10.0, 10.0)) -> raw_pb2.Unit:
    """One of this player's marines, carrying out `orders`."""
    return make_unit(tag, UnitTypeId.MARINE, at=at, orders=orders)


def _barracks(tag: int, *orders: raw_pb2.UnitOrder) -> raw_pb2.Unit:
    """One of this player's barracks, making what `orders` say."""
    return make_unit(tag, UnitTypeId.BARRACKS, at=(12.0, 12.0), orders=orders)


def _training(progress: float = 0.5) -> raw_pb2.UnitOrder:
    """The order a barracks shows while it makes a marine."""
    return raw_pb2.UnitOrder(ability_id=_TRAIN_MARINE, progress=progress)


def _moving(to: tuple[float, float] = (20.0, 20.0)) -> raw_pb2.UnitOrder:
    """The order a marine shows while it moves to `to`, under the exact id a move runs."""
    return raw_pb2.UnitOrder(ability_id=_MOVE_EXACT, target_world_space_pos=common_pb2.Point(x=to[0], y=to[1]))


def _reported(
    ability: AbilityId, *tags: int, step: int = 0, target: tuple[float, float] | None = None
) -> sc2api_pb2.Action:
    """The action an observation reports for an order the game carried out."""
    position = None if target is None else common_pb2.Point2D(x=target[0], y=target[1])
    command = raw_pb2.ActionRawUnitCommand(ability_id=ability, unit_tags=tags, target_world_space_pos=position)
    return sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(unit_command=command), game_loop=step)


def _failed(
    ability: AbilityId, tag: int, result: ActionResult = ActionResult.NOT_ENOUGH_FOOD
) -> sc2api_pb2.ActionError:
    """An action error naming the order the game gave up on."""
    return sc2api_pb2.ActionError(unit_tag=tag, ability_id=ability, result=_verdict(result))


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

        assert repr(one) == f"Order(GENERAL_MOVE, {game.own(1)!r}, given)"
        assert repr(both) == "Order(GENERAL_MOVE, 2 units, given)"

    def test_an_ability_aimed_at_what_it_cannot_take_is_a_mistake(self) -> None:
        """The three the game was seen to answer `ERROR`, which NachOS now answers for itself."""
        game = _Game()
        game.observe(0, _marine(1))
        marine = game.own(1)

        with pytest.raises(TypeError, match="MARINE_STIM takes no target"):
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

    def test_a_later_turns_order_supersedes_one_the_unit_is_still_carrying_out(self) -> None:
        game = _Game([ActionResult.SUCCESS], [ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        first = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()
        game.observe(16, _marine(1, _moving()), actions=(_reported(_MOVE_EXACT, 1, step=16),))
        assert first.state is OrderState.RUNNING

        game.book.issue(game.own(1), _ATTACK, target=(30.0, 31.0))
        game.flush()

        assert first.state is OrderState.OVERRIDDEN

    def test_given_says_what_the_turn_has_already_ordered_a_unit_and_is_empty_the_next(self) -> None:
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


class TestAnOrderAUnitAlreadyHas:
    def test_an_unqueued_order_a_unit_is_already_carrying_out_is_not_sent_again(self) -> None:
        game = _Game()
        game.observe(0, _marine(1, _moving((20.0, 21.0))))
        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        assert game.flush() is None
        assert order.state is OrderState.RUNNING
        assert order.taken_by == (game.own(1),)

    def test_a_point_the_game_cut_down_to_its_own_lattice_is_the_same_point(self) -> None:
        game = _Game()
        game.observe(0, _marine(1, _moving((157.123291, 3.0))))

        order = game.book.issue(game.own(1), _MOVE, target=(157.123456, 3.0))

        assert game.flush() is None
        assert order.state is OrderState.RUNNING

    def test_an_order_aimed_elsewhere_is_sent(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0))))
        game.book.issue(game.own(1), _MOVE, target=(30.0, 31.0))

        assert len(_commands(game.flush())) == 1

    def test_a_queued_order_is_sent_though_the_unit_is_already_doing_it(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0))))
        game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0), queued=True)

        assert len(_commands(game.flush())) == 1

    def test_only_the_units_already_carrying_it_out_are_left_out(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0))), _marine(2))
        game.book.issue([game.own(1), game.own(2)], _MOVE, target=(20.0, 21.0))

        (command,) = _commands(game.flush())

        assert list(command.unit_tags) == [2]


class TestClearingAQueue:
    def test_the_order_a_unit_is_at_is_sent_back_unqueued(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0)), _moving((30.0, 31.0))))

        order = game.book.clear_queue(game.own(1))

        (command,) = _commands(game.flush())
        assert command.ability_id == _MOVE
        assert (command.target_world_space_pos.x, command.target_world_space_pos.y) == (20.0, 21.0)
        assert not command.queue_command
        assert order is not None
        assert order.state is OrderState.SENT

    def test_a_unit_with_nothing_queued_has_nothing_to_clear(self) -> None:
        game = _Game()
        game.observe(0, _marine(1, _moving((20.0, 21.0))), _marine(2))

        assert game.book.clear_queue(game.own(1)) is None
        assert game.book.clear_queue(game.own(2)) is None
        assert game.flush() is None

    def test_an_order_given_to_the_unit_after_it_overrides_it(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1, _moving((20.0, 21.0)), _moving((30.0, 31.0))))

        cleared = game.book.clear_queue(game.own(1))
        game.book.issue(game.own(1), _ATTACK, target=(40.0, 41.0))

        (command,) = _commands(game.flush())
        assert command.ability_id == _ATTACK
        assert cleared is not None
        assert cleared.state is OrderState.OVERRIDDEN


class TestWhatBecameOfAnOrder:
    def test_an_order_the_game_reports_is_running_until_the_unit_stops(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()
        assert order.state is OrderState.SENT

        game.observe(16, _marine(1, _moving()), actions=(_reported(_MOVE_EXACT, 1, step=16),))
        assert order.state is OrderState.RUNNING
        assert order.taken_by == (game.own(1),)
        assert game.book.running == (order,)

        game.observe(32, _marine(1))
        assert order.state is OrderState.DONE
        assert game.book.running == ()

    def test_an_ability_carried_out_at_once_is_done_as_soon_as_it_is_reported(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        order = game.book.issue(game.own(1), _STIM)
        game.flush()

        game.observe(16, _marine(1, _moving()), actions=(_reported(_STIM, 1, step=16),))

        assert order.state is OrderState.DONE

    def test_an_order_the_game_refused_carries_its_verdict(self) -> None:
        game = _Game([ActionResult.NOT_SUPPORTED])
        game.observe(0, _marine(1))
        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        game.flush()

        assert order.state is OrderState.REFUSED
        assert order.action_result is ActionResult.NOT_SUPPORTED
        assert game.book.running == ()

    def test_an_order_taken_and_never_carried_out_was_dropped(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()

        game.observe(16, _marine(1))

        assert order.state is OrderState.DROPPED
        assert order.action_result is ActionResult.SUCCESS

    def test_an_action_error_fails_the_order_it_names(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()

        game.observe(16, _marine(1), errors=(_failed(_MOVE_EXACT, 1),))

        assert order.state is OrderState.FAILED
        assert order.failure is not None
        assert order.failure.action_result is ActionResult.NOT_ENOUGH_FOOD
        assert order.failure.unit is game.own(1)

    def test_an_order_is_lost_once_the_unit_it_was_given_to_is_dead(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()
        game.observe(16, _marine(1, _moving()), actions=(_reported(_MOVE_EXACT, 1, step=16),))
        assert order.state is OrderState.RUNNING

        game.observe(32, dead=(1,))

        assert order.state is OrderState.LOST

    def test_what_a_structure_killed_half_way_was_making_is_lost(self) -> None:
        """The game reports a producer dying and nothing more (in game)."""
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _barracks(1))
        order = game.book.issue(game.own(1), _TRAIN_MARINE)
        game.flush()
        game.observe(16, _barracks(1, _training()), actions=(_reported(_TRAIN_MARINE, 1, step=16),))
        assert order.state is OrderState.RUNNING

        game.observe(32, dead=(1,))

        assert order.state is OrderState.LOST
        assert order.failure is None

    def test_one_train_to_three_barracks_is_lost_with_the_one_that_took_it(self) -> None:
        """One command naming several structures is carried out by one of them (in game), so the two left idle
        say nothing about what became of it."""
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _barracks(1), _barracks(2), _barracks(3))
        order = game.book.issue([game.own(1), game.own(2), game.own(3)], _TRAIN_MARINE)
        game.flush()
        game.observe(16, _barracks(1), _barracks(2, _training()), _barracks(3))
        assert order.state is OrderState.RUNNING

        game.observe(32, _barracks(1), _barracks(3), dead=(2,))

        assert order.state is OrderState.LOST

    def test_a_group_move_one_of_which_arrived_is_done_though_the_rest_died(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2))
        order = game.book.issue([game.own(1), game.own(2)], _MOVE, target=(20.0, 21.0))
        game.flush()
        game.observe(16, _marine(1, _moving()), _marine(2, _moving()))
        game.observe(32, _marine(1), _marine(2, _moving()))
        assert order.state is OrderState.RUNNING

        game.observe(48, _marine(1), dead=(2,))

        assert order.state is OrderState.DONE

    def test_an_order_the_game_gave_up_on_fails_though_its_unit_then_died(self) -> None:
        """With several steps a turn, an error and the unit's death can arrive in one observation, and the error
        came first."""
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()

        game.observe(16, errors=(_failed(_MOVE_EXACT, 1),), dead=(1,))

        assert order.state is OrderState.FAILED
        assert order.failure is not None

    def test_a_group_order_fails_where_one_was_given_up_on_and_the_rest_died(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2))
        order = game.book.issue([game.own(1), game.own(2)], _MOVE, target=(20.0, 21.0))
        game.flush()
        game.observe(16, _marine(1, _moving()), _marine(2, _moving()))

        game.observe(32, _marine(1), errors=(_failed(_MOVE_EXACT, 1),), dead=(2,))

        assert order.state is OrderState.FAILED

    def test_one_train_to_three_barracks_is_lost_with_the_one_reported_taking_it_before_it_was_seen_at_it(
        self,
    ) -> None:
        """With several steps a turn, the barracks that took it can die before any observation shows it training;
        the report still names it."""
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _barracks(1), _barracks(2), _barracks(3))
        order = game.book.issue([game.own(1), game.own(2), game.own(3)], _TRAIN_MARINE)
        game.flush()

        game.observe(16, _barracks(1), _barracks(3), actions=(_reported(_TRAIN_MARINE, 2, step=16),), dead=(2,))

        assert order.state is OrderState.LOST

    def test_who_took_an_order_is_kept_though_it_died_in_the_same_turn(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        order = game.book.issue(game.own(1), _STIM)
        game.flush()

        game.observe(16, actions=(_reported(_STIM, 1, step=16),), dead=(1,))

        assert order.taken_by == (game.own(1),)
        assert order.state is OrderState.LOST

    def test_a_larvas_order_is_done_once_its_egg_is_reported_dead(self) -> None:
        """An egg is reported dead as what it makes hatches (in game)."""
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, make_unit(1, UnitTypeId.LARVA))
        order = game.book.issue(game.own(1), _MORPH_DRONE)
        game.flush()
        egg = make_unit(1, UnitTypeId.EGG, orders=[raw_pb2.UnitOrder(ability_id=_MORPH_DRONE, progress=0.5)])
        game.observe(16, egg, actions=(_reported(_MORPH_DRONE, 1, step=16),))
        assert order.state is OrderState.RUNNING

        game.observe(32, make_unit(2, UnitTypeId.DRONE), dead=(1,))

        assert order.state is OrderState.DONE

    def test_an_order_withdrawn_before_the_turn_ends_is_never_sent(self) -> None:
        game = _Game()
        game.observe(0, _marine(1))
        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))

        order.withdraw()

        assert game.flush() is None
        assert order.state is OrderState.WITHDRAWN

    def test_an_order_withdrawn_after_it_was_sent_is_only_forgotten(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()

        order.withdraw()
        game.observe(16, _marine(1, _moving()), actions=(_reported(_MOVE_EXACT, 1, step=16),))

        assert order.state is OrderState.WITHDRAWN
        assert game.book.running == ()

    def test_every_state_but_the_ones_still_going_is_final(self) -> None:
        going = {OrderState.GIVEN, OrderState.SENT, OrderState.RUNNING}
        assert {state for state in OrderState if not state.is_final} == going


class TestOrdersThatQueue:
    """A train goes behind what a structure is making, so it is neither a duplicate nor a replacement (in game)."""

    def test_a_train_is_sent_though_the_structure_is_already_making_one(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _barracks(1, _training()))

        order = game.book.issue(game.own(1), _TRAIN_MARINE)

        (command,) = _commands(game.flush())
        assert command.ability_id == _TRAIN_MARINE
        assert order.order_behavior is OrderBehavior.QUEUES
        assert order.state is OrderState.SENT

    def test_a_train_does_not_end_the_order_the_structure_is_already_running(self) -> None:
        game = _Game([ActionResult.SUCCESS], [ActionResult.SUCCESS])
        game.observe(0, _barracks(1))
        marine = game.book.issue(game.own(1), _TRAIN_MARINE)
        game.flush()
        game.observe(16, _barracks(1, _training()), actions=(_reported(_TRAIN_MARINE, 1, step=16),))
        assert marine.state is OrderState.RUNNING

        game.book.issue(game.own(1), _TRAIN_REAPER)
        game.flush()

        assert marine.state is OrderState.RUNNING


class TestWhatAnOrderSupersedes:
    def test_a_refused_order_ends_nothing(self) -> None:
        game = _Game([ActionResult.SUCCESS], [ActionResult.NOT_SUPPORTED])
        game.observe(0, _marine(1))
        first = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()
        game.observe(16, _marine(1, _moving()), actions=(_reported(_MOVE_EXACT, 1, step=16),))
        assert first.state is OrderState.RUNNING

        refused = game.book.issue(game.own(1), _ATTACK, target=(30.0, 31.0))
        game.flush()

        assert refused.state is OrderState.REFUSED
        assert first.state is OrderState.RUNNING

    def test_clearing_a_queue_keeps_the_order_it_leaves_the_unit_at(self) -> None:
        game = _Game([ActionResult.SUCCESS], [ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        moving = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()
        game.observe(
            16,
            _marine(1, _moving((20.0, 21.0)), _moving((30.0, 31.0))),
            actions=(_reported(_MOVE_EXACT, 1, step=16),),
        )
        assert moving.state is OrderState.RUNNING

        game.book.clear_queue(game.own(1))
        game.flush()

        assert moving.state is OrderState.RUNNING


class TestAnOrderTheGameDropped:
    def test_an_order_is_dropped_though_another_unit_ran_the_same_ability(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2))
        reported = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        dropped = game.book.issue(game.own(2), _MOVE, target=(30.0, 31.0))
        game.flush()

        # The game carried out the first and dropped the second, which it does without a word (in game).
        game.observe(16, _marine(1, _moving()), _marine(2), actions=(_reported(_MOVE_EXACT, 1, step=16),))

        assert reported.state is OrderState.RUNNING
        assert dropped.state is OrderState.DROPPED
        assert dropped.taken_by == ()


class TestATargetOffTheGround:
    def test_a_height_is_left_behind(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1))

        order = game.book.issue(game.own(1), _MOVE, target=Point3D((20.0, 21.0, 5.0)))

        assert order.target == Point((20.0, 21.0))
        (command,) = _commands(game.flush())
        assert (command.target_world_space_pos.x, command.target_world_space_pos.y) == (20.0, 21.0)

    def test_a_unit_at_a_point_with_a_height_is_still_carrying_out_the_order(self) -> None:
        game = _Game()
        game.observe(0, _marine(1, _moving((20.0, 21.0))))

        order = game.book.issue(game.own(1), _MOVE, target=Point3D((20.0, 21.0, 5.0)))

        assert game.flush() is None
        assert order.state is OrderState.RUNNING


class TestWhatAStructureDoesBesidesMaking:
    """A structure goes on making what it is making when given a rally or a cancel (in game)."""

    def test_a_rally_does_not_take_the_turn_from_a_train(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _barracks(1))
        train = game.book.issue(game.own(1), _TRAIN_MARINE)
        rally = game.book.issue(game.own(1), _RALLY, target=(20.0, 21.0))

        sent = [command.ability_id for command in _commands(game.flush())]

        assert sent == [_TRAIN_MARINE, _RALLY]
        assert (train.state, rally.state) == (OrderState.SENT, OrderState.SENT)

    def test_a_rally_does_not_end_the_order_the_structure_is_running(self) -> None:
        game = _Game([ActionResult.SUCCESS], [ActionResult.SUCCESS])
        game.observe(0, _barracks(1))
        train = game.book.issue(game.own(1), _TRAIN_MARINE)
        game.flush()
        game.observe(16, _barracks(1, _training()), actions=(_reported(_TRAIN_MARINE, 1, step=16),))
        assert train.state is OrderState.RUNNING

        game.book.issue(game.own(1), _RALLY, target=(20.0, 21.0))
        game.flush()

        assert train.state is OrderState.RUNNING
        assert train in game.book.running


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


class TestWhatCannotBeCleared:
    def test_a_structure_making_something_is_not_cleared(self) -> None:
        game = _Game()
        game.observe(0, _barracks(1, _training(), _training(0.0)))

        assert game.book.clear_queue(game.own(1)) is None
        assert game.flush() is None

    def test_an_ability_nachos_cannot_name_is_not_cleared(self) -> None:
        game = _Game()
        uncurated = raw_pb2.UnitOrder(ability_id=99991)
        game.observe(0, _marine(1, uncurated, _moving()))

        assert game.book.clear_queue(game.own(1)) is None
        assert game.flush() is None

    def test_the_order_it_sends_keeps_the_unit_it_was_aimed_at(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        attacking = raw_pb2.UnitOrder(ability_id=_ATTACK, target_unit_tag=2)
        game.observe(0, _marine(1, attacking, _moving()), _marine(2, at=(30.0, 30.0)))

        order = game.book.clear_queue(game.own(1))

        (command,) = _commands(game.flush())
        assert command.target_unit_tag == 2
        assert order is not None
        assert order.target is game.own(2)


class TestAPointAsTheGameReadsIt:
    def test_a_target_is_kept_as_the_protocol_carries_it(self) -> None:
        """A coordinate goes out as a 32-bit float, so the point an order holds is the one the game reports."""
        game = _Game()
        # Cut down to 1/4096 this reads a step lower than the 32-bit float the game is actually given.
        crossing = 157.123288015625
        game.observe(0, _marine(1, _moving((crossing, 3.0))))

        order = game.book.issue(game.own(1), _MOVE, target=(crossing, 3.0))

        assert order.target == Point((157.123291015625, 3.0))
        assert game.flush() is None
        assert order.state is OrderState.RUNNING


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
        """Which is how a structure with a reactor is told to make two at once (in game)."""
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS])
        game.observe(0, _barracks(1))
        first = game.book.issue(game.own(1), _TRAIN_MARINE)
        second = game.book.issue(game.own(1), _TRAIN_MARINE, queued=True)

        one, two = _commands(game.flush())

        assert (one.ability_id, one.queue_command) == (_TRAIN_MARINE, False)
        assert (two.ability_id, two.queue_command) == (_TRAIN_MARINE, True)
        assert (first.state, second.state) == (OrderState.SENT, OrderState.SENT)

    def test_a_second_unqueued_train_takes_the_structure_from_the_first(self) -> None:
        """Deliberate: the game would queue it and pay for it from the step it was ordered."""
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _barracks(1))
        first = game.book.issue(game.own(1), _TRAIN_MARINE)
        second = game.book.issue(game.own(1), _TRAIN_REAPER)

        (command,) = _commands(game.flush())

        assert command.ability_id == _TRAIN_REAPER
        assert (first.state, second.state) == (OrderState.OVERRIDDEN, OrderState.SENT)


class TestAnErrorAboutOneOfAGroup:
    def test_one_unit_failing_leaves_the_order_to_the_rest(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2))
        order = game.book.issue([game.own(1), game.own(2)], _MOVE, target=(20.0, 21.0))
        game.flush()

        game.observe(
            16,
            _marine(1, _moving()),
            _marine(2),
            actions=(_reported(_MOVE_EXACT, 1, step=16),),
            errors=(_failed(_MOVE_EXACT, 2),),
        )

        assert order.state is OrderState.RUNNING
        assert order.failure is not None
        assert order.failure.unit is game.own(2)

    def test_an_order_fails_once_every_unit_it_went_out_for_failed(self) -> None:
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2))
        order = game.book.issue([game.own(1), game.own(2)], _MOVE, target=(20.0, 21.0))
        game.flush()

        game.observe(16, _marine(1), _marine(2), errors=(_failed(_MOVE_EXACT, 1), _failed(_MOVE_EXACT, 2)))

        assert order.state is OrderState.FAILED


class TestAnOrderTakenBack:
    def test_the_turns_orders_do_not_undo_a_withdrawal(self) -> None:
        game = _Game([ActionResult.SUCCESS], [ActionResult.SUCCESS])
        game.observe(0, _marine(1))
        order = game.book.issue(game.own(1), _MOVE, target=(20.0, 21.0))
        game.flush()
        game.observe(16, _marine(1, _moving()), actions=(_reported(_MOVE_EXACT, 1, step=16),))

        order.withdraw()
        assert game.book.running == ()

        game.book.issue(game.own(1), _ATTACK, target=(30.0, 31.0))
        game.flush()

        assert order.state is OrderState.WITHDRAWN


class TestAnOrderSentToSomeOfItsUnits:
    def test_it_is_superseded_by_what_took_the_units_it_kept(self) -> None:
        game = _Game([ActionResult.SUCCESS, ActionResult.SUCCESS], [ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2))
        group = game.book.issue([game.own(1), game.own(2)], _MOVE, target=(20.0, 21.0))
        game.book.issue(game.own(2), _ATTACK, target=(30.0, 31.0))
        game.flush()
        game.observe(16, _marine(1, _moving()), _marine(2), actions=(_reported(_MOVE_EXACT, 1, step=16),))
        assert group.state is OrderState.RUNNING

        # The group order went out for marine 1 alone, so marine 1 is all it takes to supersede it.
        game.book.issue(game.own(1), _MOVE, target=(40.0, 41.0))
        game.flush()

        assert group.state is OrderState.OVERRIDDEN


class TestWhoTookAnOrder:
    def test_a_report_that_comes_after_the_unit_is_seen_doing_it_still_names_who_took_it(self) -> None:
        """An order's effect can show up an observation before its report does (in game)."""
        game = _Game([ActionResult.SUCCESS])
        game.observe(0, _marine(1), _marine(2))
        order = game.book.issue([game.own(1), game.own(2)], _MOVE, target=(20.0, 21.0))
        game.flush()

        game.observe(16, _marine(1, _moving()), _marine(2, _moving()))
        assert order.state is OrderState.RUNNING
        assert order.taken_by == ()

        game.observe(32, _marine(1, _moving()), _marine(2, _moving()), actions=(_reported(_MOVE_EXACT, 1, 2, step=32),))

        assert order.state is OrderState.RUNNING
        assert order.taken_by == (game.own(1), game.own(2))


class TestAVerdictNachosCannotName:
    def test_it_says_which_result_the_game_answered_with(self) -> None:
        """The enum is generated from the protocol package, so a game newer than it could answer with a result it
        leaves out. The protocol itself refuses to carry one, so the reading is probed on its own."""
        assert ActionResult.read(int(ActionResult.QUEUE_IS_FULL)) is ActionResult.QUEUE_IS_FULL

        with pytest.raises(UnknownActionResultError, match="no member for the result 250"):
            ActionResult.read(250)


class _OrderingBot:
    """A bot that gives a few orders and holds on to each, to see what the real game makes of them."""

    def __init__(self, api: Api, player: int) -> None:
        self.api = api
        self.player = player
        self.moved: Order[str] | None = None
        self.stim: Order[None] | None = None
        self.stimmed_move: Order[None] | None = None
        self.at_a_dead_tag: Order[None] | None = None
        self.killed: OwnUnit[Any] | None = None

    def turn(self, event: TurnEvent) -> None:
        """Order what the game has not been asked yet, one thing at a time."""
        api = self.api
        marines = api.units.own.of_type(UnitTypeId.GHOST)
        middle = api.map.playable_area.center
        if not marines:
            if event.step < 64:
                api.client.debug([_create(UnitTypeId.GHOST, middle, self.player, quantity=2)])
            return
        if self.moved is None and len(marines) >= 2:
            first, second = marines[0], marines[1]
            self.moved = api.order.issue(first, _MOVE, target=middle + (6.0, 0.0), data="scouting")
            self.stim = api.order.issue(second, _HOLD_FIRE)
            self.stimmed_move = api.order.issue(second, _MOVE, target=middle + (0.0, 6.0))
            return
        if self.moved is None or self.stim is None:
            return
        if self.killed is None and self.moved.state is OrderState.DONE:
            self.killed = marines[-1]
            api.client.debug([debug_pb2.DebugCommand(kill_unit=debug_pb2.DebugKillUnit(tag=[self.killed.tag]))])
            return
        if self.killed is not None and self.at_a_dead_tag is None and self.killed.is_dead:
            self.at_a_dead_tag = api.order.issue(self.killed, _MOVE, target=middle)


class _LosingBot:
    """A bot that has a structure killed while it researches, and a larva hatch a drone, to see which of the two
    orders the real game leaves lost and which done."""

    def __init__(self, api: Api, player: int) -> None:
        self.api = api
        self.player = player
        self.research: Order[None] | None = None
        self.drone: Order[None] | None = None
        self.drone_ran = False
        self.killed = False

    def turn(self, event: TurnEvent) -> None:
        """Put an evolution chamber up, start a research and a drone, then kill the chamber."""
        api = self.api
        if self.drone is not None and self.drone.state is OrderState.RUNNING:
            self.drone_ran = True
        if self.drone is None:
            larvae = api.units.own.of_type(UnitTypeId.LARVA)
            if len(larvae) > 1:
                # One larva left, so the drone cannot be handed to another (in game). A kill lands as the game steps,
                # so the drone waits for a turn that sees the rest gone.
                kill = debug_pb2.DebugKillUnit(tag=[larva.tag for larva in larvae[1:]])
                api.client.debug([debug_pb2.DebugCommand(kill_unit=kill)])
            elif larvae:
                self.drone = api.order.issue(larvae[0], AbilityId.LARVA_MORPH_DRONE)
        chambers = api.units.own.of_type(UnitTypeId.EVOLUTION_CHAMBER).complete
        if not chambers:
            home = api.units.own.of_type(UnitTypeId.HATCHERY)
            if event.step < 64 and home:
                api.client.debug(
                    [
                        _create(UnitTypeId.EVOLUTION_CHAMBER, home[0].position + (0.0, 6.0), self.player, quantity=1),
                        debug_pb2.DebugCommand(game_state=debug_pb2.DebugGameState.all_resources),
                    ]
                )
            return
        if self.research is None:
            self.research = api.order.issue(chambers[0], AbilityId.EVOLUTION_CHAMBER_RESEARCH_MELEE_WEAPONS)
        elif not self.killed and self.research.state is OrderState.RUNNING:
            self.killed = True
            api.client.debug([debug_pb2.DebugCommand(kill_unit=debug_pb2.DebugKillUnit(tag=[chambers[0].tag]))])


def _create(unit_type: UnitTypeId, at: Point, owner: int, *, quantity: int) -> debug_pb2.DebugCommand:
    """The command creating units of `unit_type` at `at`."""
    position = common_pb2.Point2D(x=at.x, y=at.y)
    unit = debug_pb2.DebugCreateUnit(unit_type=unit_type, owner=owner, pos=position, quantity=quantity)
    return debug_pb2.DebugCommand(create_unit=unit)


@pytest.mark.integration
class TestAgainstTheRealGame:
    """Run with `pytest -m integration`. Plays a minute of one game, ordering as a bot would."""

    def test_orders_go_out_each_turn_and_settle_from_what_the_game_reports(self) -> None:
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
            bot = _OrderingBot(api, player)
            api.event.on(TurnEvent)(bot.turn)
            api.play(client, steps_per_turn=8, time_limit=60)

        assert bot.moved is not None
        assert bot.moved.action_result is ActionResult.SUCCESS
        assert bot.moved.state is OrderState.DONE
        assert bot.moved.data == "scouting"
        assert bot.moved.taken_by == bot.moved.units

        # Holding fire and a move to one ghost are both carried out, and neither overrides the other.
        assert bot.stim is not None and bot.stimmed_move is not None
        assert bot.stim.action_result is ActionResult.SUCCESS
        assert bot.stimmed_move.action_result is ActionResult.SUCCESS
        assert bot.stim.state is OrderState.DONE

        # An order to a dead unit's tag is refused (in game).
        assert bot.at_a_dead_tag is not None
        assert bot.at_a_dead_tag.state is OrderState.REFUSED
        assert bot.at_a_dead_tag.action_result is ActionResult.ERROR

    def test_what_a_structure_killed_was_making_is_lost_and_a_hatched_drone_done(self) -> None:
        try:
            game_map = MapFile.find("PylonAIE_v4")
        except MapNotFoundError as missing:
            pytest.skip(str(missing))
        with (
            GameProcess.launch(window=(640, 480)) as process,
            closing(Client(WebSocketTransport.connect(process.url))) as client,
        ):
            client.create_game(game_map.path, [Participant(), Computer(Race.TERRAN, Difficulty.VERY_EASY)])
            player = client.join_game(Race.ZERG)
            api = Api()
            bot = _LosingBot(api, player)
            api.event.on(TurnEvent)(bot.turn)
            api.play(client, steps_per_turn=8, time_limit=60)

        # The game reports the chamber dying and nothing more, so the research reads lost.
        assert bot.killed
        assert bot.research is not None
        assert bot.research.state is OrderState.LOST
        assert bot.research.failure is None

        # The egg the larva became is reported dead as the drone hatches, which is the order done.
        assert bot.drone is not None
        assert bot.drone_ran, f"the drone's order read {bot.drone.state.name}, never running"
        assert bot.drone.state is OrderState.DONE
