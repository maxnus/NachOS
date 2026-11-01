"""Speed mining, NachOS's first extension: what it orders the workers handed over, in a game drawn on Pylon's main."""

from collections.abc import Callable, Iterable, Sequence
from typing import Any

import pytest
from s2clientprotocol import raw_pb2

from sc2nachos import Api, GameTime, NotPlayingError, run_local
from sc2nachos.events import EventPriority, GameStartEvent, TurnEvent
from sc2nachos.extensions.speed_mining import SpeedMining
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, BuffId, UnitTypeId
from sc2nachos.ids.raw import RawAbilityId
from sc2nachos.launch import MapFile, MapNotFoundError
from sc2nachos.match import Computer, Difficulty, Race
from sc2nachos.units import Alliance, OwnUnit, Unit
from support import ScriptedGame, make_game_info, make_observation, make_unit, shipped_game_data

# Pylon's main base, by each resource's offset from where its townhall stands (corpus), around a townhall at the center
# of a map of open ground.
_CENTER = (24.5, 24.5)
_FIELD_OFFSETS = (
    (-0.5, -6.0),
    (6.5, 0.0),
    (2.5, -6.0),
    (6.5, -2.0),
    (0.5, -7.0),
    (5.5, -5.0),
    (7.5, -1.0),
    (6.5, -4.0),
)
_GEYSER_OFFSETS = ((7.0, 3.0), (-4.0, -7.0))
_COMMAND_CENTER = 1
_FIELD = 102
_OTHER_FIELD = 101
# Where the moves of a trip to `_FIELD`, 1.325 straight toward the townhall, and back, 3.125 from the townhall's
# center, end, as the game keeps a point.
_GATHER_POINT = Point((29.675, 24.5)).rounded_down(step=1 / 4096)
_RETURN_POINT = Point((27.625, 24.5))
_SCV = 10


def _base(*, command_center_progress: float = 1.0) -> list[raw_pb2.Unit]:
    """The base's fields, tags 101 to 108, its geysers, and this player's command center, built as far as
    `command_center_progress` says."""
    fields = [
        make_unit(
            101 + index, UnitTypeId.MINERAL_FIELD, at=(_CENTER[0] + dx, _CENTER[1] + dy), alliance=Alliance.NEUTRAL
        )
        for index, (dx, dy) in enumerate(_FIELD_OFFSETS)
    ]
    geysers = [
        make_unit(
            111 + index, UnitTypeId.VESPENE_GEYSER, at=(_CENTER[0] + dx, _CENTER[1] + dy), alliance=Alliance.NEUTRAL
        )
        for index, (dx, dy) in enumerate(_GEYSER_OFFSETS)
    ]
    command_center = make_unit(
        _COMMAND_CENTER, UnitTypeId.COMMAND_CENTER, at=_CENTER, build_progress=command_center_progress
    )
    return [*fields, *geysers, command_center]


def _scv(
    at: tuple[float, float],
    *orders: raw_pb2.UnitOrder,
    carrying: bool = False,
    tag: int = _SCV,
) -> raw_pb2.Unit:
    """This player's SCV at `at`, carrying out `orders`, and carrying minerals if `carrying`."""
    buffs = [BuffId.CARRYING_MINERALS] if carrying else []
    return make_unit(tag, UnitTypeId.SCV, at=at, build_progress=1.0, orders=orders, buff_ids=buffs)


def _gathering(field: int = _FIELD) -> raw_pb2.UnitOrder:
    return raw_pb2.UnitOrder(ability_id=RawAbilityId.Harvest_Gather_SCV, target_unit_tag=field)


def _returning() -> raw_pb2.UnitOrder:
    return raw_pb2.UnitOrder(ability_id=RawAbilityId.Harvest_Return_SCV, target_unit_tag=_COMMAND_CENTER)


def _moving(to: Point) -> raw_pb2.UnitOrder:
    order = raw_pb2.UnitOrder(ability_id=RawAbilityId.Move)
    order.target_world_space_pos.x, order.target_world_space_pos.y = to
    return order


def _unit(api: Api[object], tag: int) -> Unit[Any]:
    return next(unit for unit in api.units if unit.tag == tag)


def _own_unit(api: Api[object], tag: int) -> OwnUnit[Any]:
    return next(unit for unit in api.units.own if unit.tag == tag)


def _mine(
    *turns: Sequence[raw_pb2.Unit],
    assigned: Iterable[tuple[int, int]] = ((_SCV, _FIELD),),
    started: Callable[[Api[SpeedMining]], object] = lambda api: None,
    dead: Callable[[int], Sequence[int]] = lambda turn: (),
) -> list[list[tuple[int, Point | int | None, bool]]]:
    """Play a turn on each of `turns`, the units each observation lists, speed mining each worker of `assigned` at its
    field, and return what each turn ordered: each command's ability, its target, a point or a tag, and whether it was
    queued. `started` runs as the game starts, after the workers are handed over, and `dead` gives the tags each
    observation reports dead."""
    observations = [make_observation(step, units=units, dead=dead(step)) for step, units in enumerate([*turns, ()])]
    game = ScriptedGame(observations, game_info=make_game_info(*["#" * 48] * 48), data=shipped_game_data())
    api = Api(extensions=SpeedMining)

    @api.on(GameStartEvent)
    def hand_over(event: GameStartEvent) -> None:
        for worker, field in assigned:
            api.extensions.mine(_own_unit(api, worker), _unit(api, field))
        started(api)

    api.play(game.joined())
    return [[_read(command) for command in commands] for commands in game.commands_by_turn()[: len(turns)]]


def _read(command: raw_pb2.ActionRawUnitCommand) -> tuple[int, Point | int | None, bool]:
    if command.HasField("target_world_space_pos"):
        target: Point | int | None = Point((command.target_world_space_pos.x, command.target_world_space_pos.y))
    elif command.HasField("target_unit_tag"):
        target = command.target_unit_tag
    else:
        target = None
    return command.ability_id, target, command.queue_command


def _move_then(ability: AbilityId, to: Point, target: int | None) -> list[tuple[int, Point | int | None, bool]]:
    return [(AbilityId.MOVE, to, False), (ability, target, True)]


class TestATrip:
    def test_an_idle_worker_is_sent_to_gather_at_its_field(self) -> None:
        assert _mine([*_base(), _scv((25.0, 30.0))]) == [[(AbilityId.GATHER, _FIELD, False)]]

    def test_a_worker_near_its_field_is_given_the_move_with_the_gather_behind_once(self) -> None:
        """Its orders may not show the move in the next observation; nothing goes out again for it."""
        near = [*_base(), _scv((28.175, 24.5), _gathering())]
        assert _mine(near, near) == [_move_then(AbilityId.GATHER, _GATHER_POINT, _FIELD), []]

    def test_a_worker_near_the_townhall_with_cargo_is_given_the_move_with_the_return_behind(self) -> None:
        near = [*_base(), _scv((29.125, 24.5), _returning(), carrying=True)]
        assert _mine(near) == [_move_then(AbilityId.SMART, _RETURN_POINT, _COMMAND_CENTER)]

    @pytest.mark.parametrize("distance", [0.5, 2.5])
    def test_a_worker_too_near_or_too_far_from_where_its_trip_ends_is_left_on_its_gather(self, distance: float) -> None:
        assert _mine([*_base(), _scv((_GATHER_POINT.x - distance, 24.5), _gathering())]) == [[]]

    def test_a_worker_on_its_move_with_the_gather_behind_is_left_alone(self) -> None:
        on_its_way = _scv((28.175, 24.5), _moving(_GATHER_POINT), _gathering())
        assert _mine([*_base(), on_its_way]) == [[]]

    def test_a_worker_with_cargo_but_no_return_returns_it(self) -> None:
        assert _mine([*_base(), _scv((25.0, 30.0), _gathering(), carrying=True)]) == [[(AbilityId.RETURN, None, False)]]

    def test_a_worker_the_game_sent_to_another_field_is_sent_back(self) -> None:
        bounced = [*_base(), _scv((25.0, 30.0), _gathering(_OTHER_FIELD))]
        assert _mine(bounced) == [[(AbilityId.GATHER, _FIELD, False)]]


class TestWhatIsLeftAlone:
    def test_a_worker_doing_anything_else_until_it_is_idle(self) -> None:
        building = raw_pb2.UnitOrder(ability_id=RawAbilityId.TerranBuild_SupplyDepot)
        building.target_world_space_pos.x, building.target_world_space_pos.y = 30.0, 34.0
        turns = [[*_base(), _scv((28.0, 30.0), building)], [*_base(), _scv((30.0, 33.0))]]
        assert _mine(*turns) == [[], [(AbilityId.GATHER, _FIELD, False)]]

    def test_a_worker_given_an_order_this_turn_or_sent_one_that_may_not_show_yet(self) -> None:
        def order_away(api: Api[SpeedMining]) -> None:
            @api.on(TurnEvent, priority=EventPriority.HIGH, once=True)
            def move_away(event: TurnEvent) -> None:
                worker = _own_unit(api, _SCV)
                api.orders.issue(worker, AbilityId.MOVE, target=(20.0, 30.0))

        near = [*_base(), _scv((28.175, 24.5), _gathering())]
        moved = [(AbilityId.MOVE, Point((20.0, 30.0)), False)]
        assert _mine(near, near, near, started=order_away) == [
            moved,
            [],
            _move_then(AbilityId.GATHER, _GATHER_POINT, _FIELD),
        ]

    def test_a_released_worker(self) -> None:
        def release(api: Api[SpeedMining]) -> None:
            worker = _own_unit(api, _SCV)
            api.extensions.release(worker)

        assert _mine([*_base(), _scv((25.0, 30.0))], started=release) == [[]]

    def test_a_worker_whose_field_is_mined_out_is_let_go(self) -> None:
        idle = _scv((25.0, 30.0))
        mined_out = [unit for unit in _base() if unit.tag != _FIELD]
        assert _mine(
            [*_base(), idle], [*mined_out, idle], [*mined_out, idle], dead=lambda step: [_FIELD] * (step == 1)
        ) == [
            [(AbilityId.GATHER, _FIELD, False)],
            [],
            [],
        ]


class TestPlainMining:
    """Where speed mining mines with plain gathers and returns."""

    def test_without_a_finished_townhall_of_this_players_at_the_center(self) -> None:
        idle_with_cargo = _scv((25.0, 30.0), carrying=True, tag=11)
        units = [*_base(command_center_progress=0.5), _scv((28.175, 24.5), _gathering()), idle_with_cargo]
        assert _mine(units, assigned=[(_SCV, _FIELD), (11, _FIELD)]) == [[(AbilityId.RETURN, None, False)]]

    def test_with_this_players_army_in_the_mineral_line_but_not_its_workers(self) -> None:
        marine = make_unit(20, UnitTypeId.MARINE, at=(27.0, 22.0), build_progress=1.0)
        other_worker = _scv((27.0, 21.0), tag=11)
        near = _scv((28.175, 24.5), _gathering())
        assert _mine([*_base(), near, marine]) == [[]]
        assert _mine([*_base(), near, other_worker]) == [_move_then(AbilityId.GATHER, _GATHER_POINT, _FIELD)]


class TestHandingOver:
    def test_mine_refuses_a_unit_that_is_not_a_worker_or_a_field_that_is_not_minerals(self) -> None:
        refused: list[str] = []

        def try_others(api: Api[SpeedMining]) -> None:
            marine = _own_unit(api, 20)
            scv = _own_unit(api, _SCV)
            for worker, field in [(marine, _unit(api, _FIELD)), (scv, _unit(api, 111))]:
                with pytest.raises(ValueError) as raised:
                    api.extensions.mine(worker, field)
                refused.append(str(raised.value).split(",")[0])

        marine = make_unit(20, UnitTypeId.MARINE, at=(10.0, 10.0), build_progress=1.0)
        _mine([*_base(), _scv((25.0, 30.0)), marine], assigned=(), started=try_others)
        assert refused == ["speed mining orders workers", "speed mining mines mineral fields"]

    def test_built_before_the_game_starts_it_raises(self) -> None:
        with pytest.raises(NotPlayingError):
            SpeedMining(Api())


def _workers_on_the_main(api: Api[object]) -> list[tuple[OwnUnit[Any], Unit[Any]]]:
    """This player's workers, each with a field of its main to mine: one at each field, nearest first, then a second at
    the nearest."""
    fields = api.expansions[0].mineral_fields
    workers = sorted(api.units.own.workers, key=lambda worker: worker.tag)
    return [(worker, fields[index % len(fields)]) for index, worker in enumerate(workers)]


@pytest.mark.integration
def test_in_a_real_game_speed_mined_scvs_mine_more_than_scvs_sent_to_gather_once() -> None:
    """Run with `pytest -m integration`. Plays two minutes of two games on Pylon, twelve SCVs mining its main's eight
    fields. With the return queued aimed at nothing rather than at the command center, speed mining mined less."""
    try:
        MapFile.find("PylonAIE_v4")
    except MapNotFoundError as missing:
        pytest.skip(str(missing))
    sent_once = Api()
    speed_mined = Api(extensions=SpeedMining)

    @sent_once.on(GameStartEvent)
    def send_to_gather(event: GameStartEvent) -> None:
        for worker, field in _workers_on_the_main(sent_once):
            sent_once.orders.issue(worker, AbilityId.GATHER, target=field)

    @speed_mined.on(GameStartEvent)
    def hand_over(event: GameStartEvent) -> None:
        for worker, field in _workers_on_the_main(speed_mined):
            speed_mined.extensions.mine(worker, field)

    for api in (sent_once, speed_mined):
        opponent = Computer(Race.ZERG, Difficulty.VERY_EASY)
        run_local("PylonAIE_v4", api, opponent, race=Race.TERRAN, time_limit=GameTime(seconds=120), random_seed=1)

    assert speed_mined.score.collected.minerals > 1.05 * sent_once.score.collected.minerals
