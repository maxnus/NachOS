"""Speed mining, NachOS's first extension: what it orders the workers handed over, in a game drawn on Pylon's main."""

import math
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import pytest
from s2clientprotocol import raw_pb2

from sc2nachos import Api, GameTime, NotPlayingError, run_local
from sc2nachos.events import EventPriority, GameStartEvent, TurnEvent
from sc2nachos.extensions.speed_mining import SpeedMining
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, BuffId, UnitTypeId
from sc2nachos.ids.raw import RawAbilityId, RawBuffId
from sc2nachos.match import Computer, Difficulty, Race
from sc2nachos.run import MapFile, MapNotFoundError
from sc2nachos.units import Alliance, OwnUnit, Unit, Units
from support import ScriptedGame, make_game_info, make_observation, make_unit, shipped_game_data

# Pylon's main base, by each resource's offset from where its townhall stands (corpus), around a townhall at the center
# of a map of open ground.
_TOWNHALL_CENTER = (24.5, 24.5)
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
_MINED_FIELD = 102
_OTHER_FIELD = 101
# Where the moves of a trip to `_MINED_FIELD`, 1.325 straight toward the townhall, and back, 3.125 from the townhall's
# center, end, as the game keeps a point.
_GATHER_POINT = Point((29.675, 24.5)).rounded_down(step=1 / 4096)
_RETURN_POINT = Point((27.625, 24.5))
_HANDED_SCV = 10
# This player's refineries on the main's first geyser, at (31.5, 27.5), 7.62 from the townhall's center, and its
# second, at (20.5, 17.5), 8.06 from it, with a refinery's radius (in game).
_REFINERY = 121
_OTHER_REFINERY = 122
_REFINERY_AT = Point((31.5, 27.5))
_REFINERY_RADIUS = 1.6875
# Where the moves of a trip to `_REFINERY` and back end: where a worker's circle touches the refinery's, toward the
# townhall's center, and 3.125 from the townhall's center, as the game keeps a point.
_UNKEPT_GAS_GATHER_POINT = _REFINERY_AT.towards(_TOWNHALL_CENTER, _REFINERY_RADIUS + 0.375)
_GAS_GATHER_POINT = _UNKEPT_GAS_GATHER_POINT.rounded_down(step=1 / 4096)
_GAS_RETURN_POINT = Point(_TOWNHALL_CENTER).towards(_UNKEPT_GAS_GATHER_POINT, 3.125).rounded_down(step=1 / 4096)
# Where a collector stands far from where either of a trip's moves to or from `_REFINERY` ends.
_FAR_FROM_THE_GAS_TRIP_ENDS = (26.0, 30.0)


def _between_the_refinery_and_the_townhall(distance_from_the_refinery: float) -> tuple[float, float]:
    """The point `distance_from_the_refinery` from `_REFINERY`'s center toward the townhall's: its trip's move in ends
    2.0625 from it, and its trip's move back 4.49."""
    point = _REFINERY_AT.towards(_TOWNHALL_CENTER, distance_from_the_refinery)
    return (point.x, point.y)


def _pylon_main(
    *, command_center_progress: float = 1.0, at: tuple[float, float] = _TOWNHALL_CENTER, tags_from: int = 100
) -> list[raw_pb2.Unit]:
    """The base laid out as Pylon's main around a townhall at `at`: its fields, tags `tags_from` + 1 to 8, its geysers,
    + 11 and 12, and this player's command center, `tags_from` // 100, built as far as `command_center_progress`
    says."""
    fields = [
        make_unit(
            tags_from + 1 + index, UnitTypeId.MINERAL_FIELD, at=(at[0] + dx, at[1] + dy), alliance=Alliance.NEUTRAL
        )
        for index, (dx, dy) in enumerate(_FIELD_OFFSETS)
    ]
    geysers = [
        make_unit(
            tags_from + 11 + index, UnitTypeId.VESPENE_GEYSER, at=(at[0] + dx, at[1] + dy), alliance=Alliance.NEUTRAL
        )
        for index, (dx, dy) in enumerate(_GEYSER_OFFSETS)
    ]
    command_center = make_unit(
        tags_from // 100, UnitTypeId.COMMAND_CENTER, at=at, build_progress=command_center_progress
    )
    return [*fields, *geysers, command_center]


def _reported_scv(
    at: tuple[float, float],
    *orders: raw_pb2.UnitOrder,
    carrying: bool = False,
    tag: int = _HANDED_SCV,
    **fields: Any,
) -> raw_pb2.Unit:
    """This player's SCV at `at`, carrying out `orders`, carrying minerals if `carrying`, with a worker's radius and the
    rest of what `fields` report."""
    buffs = [BuffId.CARRYING_MINERALS] if carrying else []
    return make_unit(
        tag, UnitTypeId.SCV, at=at, build_progress=1.0, orders=orders, buff_ids=buffs, radius=0.375, **fields
    )


def _refinery(
    tag: int = _REFINERY,
    *,
    vespene_contents: int = 2250,
    at: tuple[float, float] = _TOWNHALL_CENTER,
    **fields: Any,
) -> raw_pb2.Unit:
    """This player's finished refinery on the geyser of the base around a townhall at `at` tagged `tag` - 10, as
    `_pylon_main` tags them: its first geyser for a tag ending in 1, its second for one ending in 2. It has
    `vespene_contents` left, a refinery's radius, and the rest of what `fields` report."""
    dx, dy = _GEYSER_OFFSETS[tag % 10 - 1]
    fields = {"build_progress": 1.0, "radius": _REFINERY_RADIUS, **fields}
    return make_unit(tag, UnitTypeId.REFINERY, at=(at[0] + dx, at[1] + dy), vespene_contents=vespene_contents, **fields)


def _scv_carrying_vespene(at: tuple[float, float], *orders: raw_pb2.UnitOrder) -> raw_pb2.Unit:
    """This player's SCV at `at`, carrying out `orders`, carrying vespene, with a worker's radius."""
    vespene = [RawBuffId.CarryHarvestableVespeneGeyserGas]
    return make_unit(
        _HANDED_SCV, UnitTypeId.SCV, at=at, build_progress=1.0, orders=orders, buff_ids=vespene, radius=0.375
    )


def _build_order() -> raw_pb2.UnitOrder:
    """A supply depot's build, at (30.0, 34.0)."""
    order = raw_pb2.UnitOrder(ability_id=RawAbilityId.TerranBuild_SupplyDepot)
    order.target_world_space_pos.x, order.target_world_space_pos.y = 30.0, 34.0
    return order


def _gather_order(field: int = _MINED_FIELD) -> raw_pb2.UnitOrder:
    return raw_pb2.UnitOrder(ability_id=RawAbilityId.Harvest_Gather_SCV, target_unit_tag=field)


def _return_order() -> raw_pb2.UnitOrder:
    return raw_pb2.UnitOrder(ability_id=RawAbilityId.Harvest_Return_SCV, target_unit_tag=_COMMAND_CENTER)


def _move_order(to: Point) -> raw_pb2.UnitOrder:
    order = raw_pb2.UnitOrder(ability_id=RawAbilityId.Move)
    order.target_world_space_pos.x, order.target_world_space_pos.y = to
    return order


def _unit_of_tag(api: Api[object], tag: int) -> Unit[Any]:
    return next(unit for unit in api.units if unit.tag == tag)


def _own_unit_of_tag(api: Api[object], tag: int) -> OwnUnit[Any]:
    return next(unit for unit in api.units.own if unit.tag == tag)


def _orders_sent(
    *turns: Sequence[raw_pb2.Unit],
    assigned: Iterable[tuple[int, int]] = ((_HANDED_SCV, _MINED_FIELD),),
    collected: Iterable[tuple[int, int]] = (),
    started: Callable[[Api[SpeedMining]], object] = lambda api: None,
    dead: Callable[[int], Sequence[int]] = lambda turn: (),
    attack_enemies: bool = True,
    map_width: int = 48,
) -> list[list[tuple[int, Point | int | None, bool]]]:
    """Play a turn on each of `turns`, the units each observation lists, speed mining each worker of `assigned` at its
    field and each of `collected` at its gas building, its workers attacking enemies if `attack_enemies`, and return
    what each turn ordered: each command's ability, its target, a point or a tag, and whether it was queued. `started`
    runs as the game starts, after the workers are handed over, and `dead` gives the tags each observation reports
    dead. The map is `map_width` wide and 48 high, of open ground."""
    observations = [make_observation(step, units=units, dead=dead(step)) for step, units in enumerate([*turns, ()])]
    game = ScriptedGame(observations, game_info=make_game_info(*["#" * map_width] * 48), data=shipped_game_data())
    api = Api(extensions=lambda api: SpeedMining(api, attack_enemies=attack_enemies))

    @api.on(GameStartEvent)
    def hand_over(event: GameStartEvent) -> None:
        for worker, field in assigned:
            api.extensions.mine(_own_unit_of_tag(api, worker), _unit_of_tag(api, field))
        for worker, gas_building in collected:
            api.extensions.collect(_own_unit_of_tag(api, worker), _unit_of_tag(api, gas_building))
        started(api)

    api.play(game.joined())
    return [[_read_command(command) for command in commands] for commands in game.commands_by_turn()[: len(turns)]]


def _read_command(command: raw_pb2.ActionRawUnitCommand) -> tuple[int, Point | int | None, bool]:
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
        assert _orders_sent([*_pylon_main(), _reported_scv((25.0, 30.0))]) == [
            [(AbilityId.GATHER, _MINED_FIELD, False)]
        ]

    def test_a_worker_near_its_field_is_given_the_move_with_the_gather_behind_once(self) -> None:
        """Its orders may not show the move in the next observation; nothing goes out again for it."""
        near = [*_pylon_main(), _reported_scv((28.175, 24.5), _gather_order())]
        assert _orders_sent(near, near) == [_move_then(AbilityId.GATHER, _GATHER_POINT, _MINED_FIELD), []]

    def test_a_worker_near_the_townhall_with_cargo_is_given_the_move_with_the_return_behind(self) -> None:
        near = [*_pylon_main(), _reported_scv((29.125, 24.5), _return_order(), carrying=True)]
        assert _orders_sent(near) == [_move_then(AbilityId.SMART, _RETURN_POINT, _COMMAND_CENTER)]

    @pytest.mark.parametrize("distance", [0.5, 2.5])
    def test_a_worker_too_near_or_too_far_from_where_its_trip_ends_is_left_on_its_gather(self, distance: float) -> None:
        assert _orders_sent([*_pylon_main(), _reported_scv((_GATHER_POINT.x - distance, 24.5), _gather_order())]) == [
            []
        ]

    def test_a_worker_on_its_move_with_the_gather_behind_is_left_alone(self) -> None:
        on_its_way = _reported_scv((28.175, 24.5), _move_order(_GATHER_POINT), _gather_order())
        assert _orders_sent([*_pylon_main(), on_its_way]) == [[]]

    def test_a_worker_with_cargo_but_no_return_returns_it_with_the_gather_queued_behind(self) -> None:
        assert _orders_sent([*_pylon_main(), _reported_scv((25.0, 30.0), _gather_order(), carrying=True)]) == [
            [(AbilityId.RETURN, None, False), (AbilityId.GATHER, _MINED_FIELD, True)]
        ]

    def test_a_worker_idle_with_vespene_hands_it_in_and_comes_to_its_field(self) -> None:
        """Left to its return alone, it would go back to its geyser."""
        mining = [*_pylon_main(), _reported_scv((25.0, 30.0), _gather_order())]
        idle_with_vespene = [*_pylon_main(), _scv_carrying_vespene((25.0, 30.0))]
        assert _orders_sent(mining, idle_with_vespene) == [
            [],
            [(AbilityId.RETURN, None, False), (AbilityId.GATHER, _MINED_FIELD, True)],
        ]

    def test_a_worker_the_game_sent_to_another_field_is_sent_back(self) -> None:
        bounced = [*_pylon_main(), _reported_scv((25.0, 30.0), _gather_order(_OTHER_FIELD))]
        assert _orders_sent(bounced) == [[(AbilityId.GATHER, _MINED_FIELD, False)]]

    def test_a_worker_on_its_way_to_a_field_it_was_handed_before_is_sent_to_its_own(self) -> None:
        """Speed mining's move toward `_OTHER_FIELD`, at (24.0, 18.5), ends 1.325 straight toward the townhall."""
        other_gather_point = Point((24.0, 18.5)).towards(_TOWNHALL_CENTER, 1.325).rounded_down(step=1 / 4096)
        on_its_way = _reported_scv((24.5, 22.0), _move_order(other_gather_point), _gather_order(_OTHER_FIELD))
        assert _orders_sent([*_pylon_main(), on_its_way]) == [[(AbilityId.GATHER, _MINED_FIELD, False)]]


class TestSendingAtOnce:
    """`mine` sends a worker to its field in the turn it is handed over, unless it mines minerals already."""

    @pytest.mark.parametrize(
        "order", [_move_order(Point((10.0, 40.0))), _gather_order(111)], ids=["a move", "collecting vespene"]
    )
    def test_a_worker_on_another_order_is_sent_to_gather_once(self, order: raw_pb2.UnitOrder) -> None:
        """The next turn's orders may not show the gather yet; nothing goes out again."""
        elsewhere = [*_pylon_main(), _reported_scv((20.0, 30.0), order)]
        assert _orders_sent(elsewhere, elsewhere) == [[(AbilityId.GATHER, _MINED_FIELD, False)], []]

    def test_a_worker_handed_over_again_before_its_orders_show_is_sent_nothing_again(self) -> None:
        def hand_over_again_next_turn(api: Api[SpeedMining]) -> None:
            @api.on(TurnEvent, priority=EventPriority.HIGH)
            def hand_over_again(event: TurnEvent) -> None:
                if event.time.steps == 1:
                    api.extensions.mine(_own_unit_of_tag(api, _HANDED_SCV), _unit_of_tag(api, _MINED_FIELD))

        carrying = [*_pylon_main(), _reported_scv((20.0, 30.0), _move_order(Point((10.0, 40.0))), carrying=True)]
        assert _orders_sent(carrying, carrying, started=hand_over_again_next_turn) == [
            [(AbilityId.RETURN, None, False), (AbilityId.GATHER, _MINED_FIELD, True)],
            [],
        ]

    @pytest.mark.parametrize(
        ("first_turn", "second_turn"),
        [
            ([_reported_scv((28.0, 30.0))], [_reported_scv((28.0, 30.0), _build_order())]),
            (
                [make_unit(_HANDED_SCV, UnitTypeId.DRONE, at=(25.0, 30.0), build_progress=1.0)],
                [
                    make_unit(
                        _HANDED_SCV, UnitTypeId.DRONE_BURROWED, at=(25.0, 30.0), build_progress=1.0, is_burrowed=True
                    )
                ],
            ),
            ([_reported_scv((25.0, 30.0))], []),
        ],
        ids=["building", "burrowed", "out of reach of orders, as inside a refinery"],
    )
    def test_mine_takes_no_worker_it_cannot_send(
        self, first_turn: list[raw_pb2.Unit], second_turn: list[raw_pb2.Unit]
    ) -> None:
        """The worker, kept from the first turn, is handed over in the second: `mine` returns `None`, holds nothing and
        sends nothing."""
        handed: list[tuple[bool, bool]] = []

        def hand_over_in_the_second_turn(api: Api[SpeedMining]) -> None:
            worker = _own_unit_of_tag(api, _HANDED_SCV)

            @api.on(TurnEvent, priority=EventPriority.HIGH)
            def hand_over(event: TurnEvent) -> None:
                if event.time.steps == 1:
                    refused = api.extensions.mine(worker, _unit_of_tag(api, _MINED_FIELD)) is None
                    handed.append((refused, worker in api.extensions.miners))

        turns = [[*_pylon_main(), *first_turn], [*_pylon_main(), *second_turn]]
        assert _orders_sent(*turns, assigned=(), started=hand_over_in_the_second_turn) == [[], []]
        assert handed == [(True, False)]

    @pytest.mark.parametrize(
        "carrying",
        [
            _reported_scv((20.0, 30.0), _move_order(Point((10.0, 40.0))), carrying=True),
            _scv_carrying_vespene((20.0, 30.0), raw_pb2.UnitOrder(ability_id=RawAbilityId.Harvest_Return_SCV)),
            _reported_scv((20.0, 30.0), raw_pb2.UnitOrder(ability_id=RawAbilityId.Harvest_Return_SCV)),
        ],
        ids=["minerals, on a move", "vespene, on its way back", "vespene from a rich geyser, which shows no cargo"],
    )
    def test_a_worker_with_cargo_returns_it_with_the_gather_queued_behind(self, carrying: raw_pb2.Unit) -> None:
        """The return goes out to a worker returning vespene too, which shows a return already, aimed at nothing as the
        game may report it."""
        assert _orders_sent([*_pylon_main(), carrying]) == [
            [(AbilityId.RETURN, None, False), (AbilityId.GATHER, _MINED_FIELD, True)]
        ]

    def test_nothing_is_sent_to_a_worker_mine_finds_no_room_for(self) -> None:
        def hand_over_at_a_base_being_built(api: Api[SpeedMining]) -> None:
            api.extensions.mine(_own_unit_of_tag(api, _HANDED_SCV), _unit_of_tag(api, _MINED_FIELD))

        units = [*_pylon_main(command_center_progress=0.5), _reported_scv((25.0, 30.0))]
        assert _orders_sent(units, assigned=(), started=hand_over_at_a_base_being_built) == [[]]


def _workers_held_each_turn(held: list[list[int]]) -> Callable[[Api[SpeedMining]], object]:
    """What `_orders_sent` runs as the game starts to append to `held` the tags of the workers speed mining holds,
    miners and collectors, at the end of each turn."""

    def read_the_workers_held(api: Api[SpeedMining]) -> None:
        api.on(TurnEvent, priority=EventPriority.LOWEST)(
            lambda event: held.append(sorted(worker.tag for worker in api.extensions.workers))
        )

    return read_the_workers_held


def _ordered_by_the_bot(
    give: Callable[[Api[SpeedMining], OwnUnit[Any]], object],
    held: list[list[int]],
    *,
    priority: EventPriority = EventPriority.HIGH,
) -> Callable[[Api[SpeedMining]], object]:
    """What `_orders_sent` runs as the game starts to have the bot `give` `_HANDED_SCV` its orders in the first turn, in
    a handler of `priority`, and append to `held` the tags of the workers speed mining holds at the end of each turn."""

    def order_in_the_first_turn(api: Api[SpeedMining]) -> None:
        @api.on(TurnEvent, priority=priority, once=True)
        def order(event: TurnEvent) -> None:
            give(api, _own_unit_of_tag(api, _HANDED_SCV))

        _workers_held_each_turn(held)(api)

    return order_in_the_first_turn


def _build_a_supply_depot(api: Api[object], worker: OwnUnit[Any], *, queued: bool = False) -> None:
    """Order `worker` to build a supply depot at (30.0, 34.0), as `_build_order` reports it."""
    api.orders.issue(worker, AbilityId.SCV_BUILD_SUPPLY_DEPOT, target=(30.0, 34.0), queued=queued)


class TestWhatIsLeftAlone:
    def test_a_worker_on_an_order_no_order_lately_explains_until_it_is_idle(self) -> None:
        """Repairing its command center by autocast, an order the bot did not give, it is sent nothing, and kept,
        until it is idle."""
        repair = raw_pb2.UnitOrder(ability_id=RawAbilityId.Effect_Repair_SCV, target_unit_tag=_COMMAND_CENTER)
        held: list[list[int]] = []
        turns = [
            [*_pylon_main(), _reported_scv((28.0, 30.0), _gather_order())],
            [*_pylon_main(), _reported_scv((28.0, 30.0), repair)],
            [*_pylon_main(), _reported_scv((30.0, 33.0))],
        ]
        assert _orders_sent(*turns, started=_workers_held_each_turn(held)) == [
            [],
            [],
            [(AbilityId.GATHER, _MINED_FIELD, False)],
        ]
        assert held == [[_HANDED_SCV]] * 3

    @pytest.mark.parametrize(
        ("scv", "given"),
        [
            (
                _reported_scv((28.175, 24.5), _gather_order()),
                _move_then(AbilityId.GATHER, _GATHER_POINT, _MINED_FIELD),
            ),
            (
                _reported_scv((29.125, 24.5), _return_order(), carrying=True),
                _move_then(AbilityId.SMART, _RETURN_POINT, _COMMAND_CENTER),
            ),
            (
                _reported_scv((25.0, 30.0), _gather_order(), carrying=True),
                [(AbilityId.RETURN, None, False), (AbilityId.GATHER, _MINED_FIELD, True)],
            ),
        ],
        ids=[
            "the move to its field, the gather behind",
            "the move back, the right-click on the townhall behind",
            "a return, the gather behind",
        ],
    )
    def test_a_worker_sent_one_of_speed_minings_orders_lately_for_the_turn(
        self, scv: raw_pb2.Unit, given: list[tuple[int, Point | int | None, bool]]
    ) -> None:
        """It is sent nothing in the next turn, whose orders may not show the order yet, and is kept."""
        held: list[list[int]] = []
        units = [*_pylon_main(), scv]
        assert _orders_sent(units, units, started=_workers_held_each_turn(held)) == [given, []]
        assert held == [[_HANDED_SCV]] * 2

    @pytest.mark.parametrize(
        "collecting",
        [_reported_scv((25.0, 30.0), _gather_order(111)), _scv_carrying_vespene((29.125, 24.5), _return_order())],
        ids=["on its way to the geyser", "on its way back, where a worker returning minerals is given the move"],
    )
    def test_a_worker_collecting_vespene_by_no_order_lately(self, collecting: raw_pb2.Unit) -> None:
        mining = [*_pylon_main(), _reported_scv((25.0, 30.0), _gather_order())]
        assert _orders_sent(mining, [*_pylon_main(), collecting]) == [[], []]

    def test_a_released_worker(self) -> None:
        """It gathers at another field, where speed mining would send it back to its own."""

        def release(api: Api[SpeedMining]) -> None:
            worker = _own_unit_of_tag(api, _HANDED_SCV)
            api.extensions.release(worker)

        elsewhere = [*_pylon_main(), _reported_scv((25.0, 30.0), _gather_order(_OTHER_FIELD))]
        assert _orders_sent(elsewhere, started=release) == [[]]

    def test_several_released_at_once(self) -> None:
        """A miner and a collector, released as `Units`, each gathering where speed mining would send it back to its
        own."""

        def release_both(api: Api[SpeedMining]) -> None:
            api.extensions.release(api.units.own.workers)

        miner = _reported_scv((25.0, 30.0), _gather_order(_OTHER_FIELD))
        collector = _reported_scv((25.0, 20.0), _gather_order(_OTHER_REFINERY), tag=11)
        units = [*_pylon_main(), _refinery(), _refinery(_OTHER_REFINERY), miner, collector]
        assert _orders_sent(units, collected=[(11, _REFINERY)], started=release_both) == [[]]

    def test_a_worker_whose_field_is_mined_out_is_let_go(self) -> None:
        held: list[int] = []

        def count_the_workers_held(api: Api[SpeedMining]) -> None:
            api.on(TurnEvent, priority=EventPriority.LOWEST)(
                lambda event: held.append(len(api.extensions._field_by_worker))
            )

        idle = _reported_scv((25.0, 30.0))
        mined_out = [unit for unit in _pylon_main() if unit.tag != _MINED_FIELD]
        turns = [[*_pylon_main(), idle], [*mined_out, idle], [*mined_out, idle]]
        sent = _orders_sent(*turns, started=count_the_workers_held, dead=lambda step: [_MINED_FIELD] * (step == 1))
        assert sent == [[(AbilityId.GATHER, _MINED_FIELD, False)], [], []]
        assert held == [1, 0, 0]


_BUILD_A_SUPPLY_DEPOT = (AbilityId.SCV_BUILD_SUPPLY_DEPOT, Point((30.0, 34.0)), False)


class TestLettingGoOfAWorkerTheBotOrders:
    """A worker the bot orders is let go, its order standing: in that turn for an order given before speed mining's
    turn, and in the next for one given after."""

    @pytest.mark.parametrize(
        "priority", [EventPriority.HIGH, EventPriority.LOW], ids=["before speed mining's turn", "after it"]
    )
    @pytest.mark.parametrize(
        ("collecting", "give", "given"),
        [
            (False, _build_a_supply_depot, _BUILD_A_SUPPLY_DEPOT),
            (
                False,
                lambda api, worker: _build_a_supply_depot(api, worker, queued=True),
                (AbilityId.SCV_BUILD_SUPPLY_DEPOT, Point((30.0, 34.0)), True),
            ),
            (
                False,
                lambda api, worker: api.orders.issue(worker, AbilityId.GATHER, target=_unit_of_tag(api, _REFINERY)),
                (AbilityId.GATHER, _REFINERY, False),
            ),
            (
                False,
                lambda api, worker: api.orders.issue(worker, AbilityId.SMART, target=(30.0, 34.0)),
                (AbilityId.SMART, Point((30.0, 34.0)), False),
            ),
            (True, _build_a_supply_depot, _BUILD_A_SUPPLY_DEPOT),
            (
                True,
                lambda api, worker: api.orders.issue(worker, AbilityId.GATHER, target=_unit_of_tag(api, _MINED_FIELD)),
                (AbilityId.GATHER, _MINED_FIELD, False),
            ),
        ],
        ids=[
            "a miner sent to build",
            "a miner given a build queued behind its gather",
            "a miner sent to collect vespene",
            "a miner right-clicked on a point",
            "a collector sent to build",
            "a collector sent to mine",
        ],
    )
    def test_a_worker_the_bot_orders_is_let_go_and_sent_nothing(
        self,
        collecting: bool,
        give: Callable[[Api[SpeedMining], OwnUnit[Any]], object],
        given: tuple[int, Point | int | None, bool],
        priority: EventPriority,
    ) -> None:
        """Ordered in the first turn, far from where its trips end, it is sent nothing in the next, and nothing idle in
        the third."""
        held: list[list[int]] = []
        work = _gather_order(_REFINERY) if collecting else _gather_order()
        at_work = [*_pylon_main(), _refinery(), _reported_scv(_FAR_FROM_THE_GAS_TRIP_ENDS, work)]
        idle = [*_pylon_main(), _refinery(), _reported_scv(_FAR_FROM_THE_GAS_TRIP_ENDS)]
        sent = _orders_sent(
            at_work,
            at_work,
            idle,
            assigned=() if collecting else [(_HANDED_SCV, _MINED_FIELD)],
            collected=[(_HANDED_SCV, _REFINERY)] if collecting else (),
            started=_ordered_by_the_bot(give, held, priority=priority),
        )
        assert sent == [[given], [], []]
        assert held == ([[_HANDED_SCV], [], []] if priority is EventPriority.LOW else [[], [], []])

    def test_a_worker_the_bot_hands_over_again_after_ordering_it_is_kept(self) -> None:
        """The bot moves it away and then has it mine its field again, in one turn: the gather goes out in place of the
        move, and to a worker gathering there already, nothing does, and it is given its move near the field in the
        next turn."""

        def move_away_then_mine(api: Api[SpeedMining], worker: OwnUnit[Any]) -> None:
            api.orders.issue(worker, AbilityId.MOVE, target=(20.0, 30.0))
            api.extensions.mine(worker, _unit_of_tag(api, _MINED_FIELD))

        held: list[list[int]] = []
        far = [*_pylon_main(), _reported_scv((25.0, 30.0), _gather_order())]
        near = [*_pylon_main(), _reported_scv((28.175, 24.5), _gather_order())]
        sent = _orders_sent(far, near, started=_ordered_by_the_bot(move_away_then_mine, held))
        assert sent == [[], _move_then(AbilityId.GATHER, _GATHER_POINT, _MINED_FIELD)]
        assert held == [[_HANDED_SCV]] * 2

    def test_a_worker_the_bot_sends_to_another_field_is_kept_and_sent_back(self) -> None:
        """As one the game sends there is, once its orders show it there."""
        held: list[list[int]] = []
        elsewhere = [*_pylon_main(), _reported_scv((25.0, 30.0), _gather_order(_OTHER_FIELD))]

        def gather_at_the_other_field(api: Api[SpeedMining], worker: OwnUnit[Any]) -> None:
            api.orders.issue(worker, AbilityId.GATHER, target=_unit_of_tag(api, _OTHER_FIELD))

        sent = _orders_sent(
            [*_pylon_main(), _reported_scv((25.0, 30.0), _gather_order())],
            elsewhere,
            elsewhere,
            started=_ordered_by_the_bot(gather_at_the_other_field, held),
        )
        assert sent == [[(AbilityId.GATHER, _OTHER_FIELD, False)], [], [(AbilityId.GATHER, _MINED_FIELD, False)]]
        assert held == [[_HANDED_SCV]] * 3

    def test_a_worker_the_bot_has_attack_is_let_go(self) -> None:
        """Its weapon cooling down, speed mining would not have it attack."""
        held: list[list[int]] = []
        mining = _reported_scv((26.0, 30.0), _gather_order(), facing=0.0, weapon_cooldown=20.0)
        zergling = _zergling_beside((26.0, 30.0), degrees_off_its_facing=0.0)

        def attack_the_zergling(api: Api[SpeedMining], worker: OwnUnit[Any]) -> None:
            api.orders.issue(worker, AbilityId.ATTACK, target=_unit_of_tag(api, 30))

        units = [*_pylon_main(), mining, zergling]
        sent = _orders_sent(units, units, started=_ordered_by_the_bot(attack_the_zergling, held))
        assert sent == [[(AbilityId.ATTACK, 30, False)], []]
        assert held == [[], []]

    def test_a_worker_the_bot_has_attack_what_speed_mining_had_it_attack_before_it_was_let_go_is_let_go(self) -> None:
        """Speed mining has it attack the zergling in the first turn; the bot stops it in the second, and in the third
        hands it over again and then has it attack the zergling."""
        held: list[list[int]] = []
        zergling = _zergling_beside((26.0, 30.0), degrees_off_its_facing=0.0)
        attack = raw_pb2.UnitOrder(ability_id=RawAbilityId.attack_Attack, target_unit_tag=30)
        turns = [
            [*_pylon_main(), _reported_scv((26.0, 30.0), _gather_order(), facing=0.0), zergling],
            [*_pylon_main(), _reported_scv((26.0, 30.0), attack, facing=0.0, weapon_cooldown=20.0), zergling],
            [*_pylon_main(), _reported_scv((26.0, 30.0), facing=0.0, weapon_cooldown=20.0), zergling],
        ]

        def stop_then_hand_over_and_attack(api: Api[SpeedMining]) -> None:
            @api.on(TurnEvent, priority=EventPriority.HIGH)
            def order(event: TurnEvent) -> None:
                worker = _own_unit_of_tag(api, _HANDED_SCV)
                if event.time.steps == 1:
                    api.orders.issue(worker, AbilityId.STOP)
                elif event.time.steps == 2:
                    api.extensions.mine(worker, _unit_of_tag(api, _MINED_FIELD))
                    api.orders.issue(worker, AbilityId.ATTACK, target=_unit_of_tag(api, 30))

            _workers_held_each_turn(held)(api)

        sent = _orders_sent(*turns, started=stop_then_hand_over_and_attack)
        assert sent == [
            [(AbilityId.ATTACK, 30, False)],
            [(AbilityId.STOP, None, False)],
            [(AbilityId.ATTACK, 30, False)],
        ]
        assert held == [[_HANDED_SCV], [], []]

    def test_a_drone_the_bot_burrows_is_let_go(self) -> None:
        """Handed over idle, it is sent to gather, and burrowed in the next turn, it is let go and sent nothing."""
        held: list[list[int]] = []
        drone = make_unit(_HANDED_SCV, UnitTypeId.DRONE, at=(25.0, 30.0), build_progress=1.0)
        burrowed = make_unit(
            _HANDED_SCV, UnitTypeId.DRONE_BURROWED, at=(25.0, 30.0), build_progress=1.0, is_burrowed=True
        )
        sent = _orders_sent([*_pylon_main(), drone], [*_pylon_main(), burrowed], started=_workers_held_each_turn(held))
        assert sent == [[(AbilityId.GATHER, _MINED_FIELD, False)], []]
        assert held == [[_HANDED_SCV], []]


class TestLettingGoOfABase:
    @pytest.mark.parametrize(
        "townhall_gone",
        [
            [],
            [make_unit(_COMMAND_CENTER, UnitTypeId.COMMAND_CENTER_FLYING, at=_TOWNHALL_CENTER, build_progress=1.0)],
        ],
        ids=["it dies", "it lifts off"],
    )
    def test_the_workers_of_a_base_whose_townhall_is_gone_are_let_go_with_their_orders_as_they_are(
        self, townhall_gone: list[raw_pb2.Unit]
    ) -> None:
        """The second turn's worker, idle with cargo, would be sent to return it if speed mining held it."""
        held: list[int] = []

        def count_the_workers_held(api: Api[SpeedMining]) -> None:
            api.on(TurnEvent, priority=EventPriority.LOWEST)(lambda event: held.append(len(api.extensions.miners)))

        without_the_townhall = [unit for unit in _pylon_main() if unit.tag != _COMMAND_CENTER] + townhall_gone
        turns = [
            [*_pylon_main(), _reported_scv((25.0, 30.0), _gather_order())],
            [*without_the_townhall, _reported_scv((25.0, 30.0), carrying=True)],
        ]
        dead = (lambda step: [_COMMAND_CENTER] * (step == 1)) if not townhall_gone else (lambda step: ())
        assert _orders_sent(*turns, started=count_the_workers_held, dead=dead) == [[], []]
        assert held == [1, 0]

    @pytest.mark.parametrize(
        "morphed", [UnitTypeId.ORBITAL_COMMAND, UnitTypeId.PLANETARY_FORTRESS, UnitTypeId.LAIR, UnitTypeId.HIVE]
    )
    def test_the_workers_of_a_townhall_finishing_its_morph_are_kept(self, morphed: UnitTypeId) -> None:
        """The game reports an orbital at build progress 0.998 the step before its morph finishes (in game), and only a
        morph makes any of these; `mine` picks a field there too, and SCV 11, gathering there already, is sent
        nothing."""
        held: list[int] = []
        picked: list[int | None] = []

        def hand_over_and_count(api: Api[SpeedMining]) -> None:
            def count_and_hand_over(event: TurnEvent) -> None:
                held.append(len(api.extensions.miners))
                if event.time.steps == 1:
                    field = api.extensions.mine(_own_unit_of_tag(api, 11))
                    picked.append(None if field is None else field.tag)

            api.on(TurnEvent, priority=EventPriority.LOWEST)(count_and_hand_over)

        finishing = [unit for unit in _pylon_main() if unit.tag != _COMMAND_CENTER] + [
            make_unit(_COMMAND_CENTER, morphed, at=_TOWNHALL_CENTER, build_progress=0.998)
        ]
        scvs = [_reported_scv((25.0, 30.0), _gather_order()), _reported_scv((26.0, 30.0), _gather_order(), tag=11)]
        sent = _orders_sent([*_pylon_main(), *scvs], [*finishing, *scvs], started=hand_over_and_count)
        assert (sent, held, picked) == ([[], []], [1, 1], [_MINED_FIELD])

    def test_the_workers_of_another_base_are_kept(self) -> None:
        """A second base, its townhall at (56.5, 24.5), tag 2, and its fields tagged from 201."""
        kept: list[list[int]] = []

        def read_the_workers_held(api: Api[SpeedMining]) -> None:
            api.on(TurnEvent, priority=EventPriority.LOWEST)(
                lambda event: kept.append(sorted(worker.tag for worker in api.extensions.miners))
            )

        second_base = _pylon_main(at=(56.5, 24.5), tags_from=200)
        main_without_its_townhall = [unit for unit in _pylon_main() if unit.tag != _COMMAND_CENTER]
        scvs = [_reported_scv((25.0, 30.0), _gather_order()), _reported_scv((57.0, 30.0), _gather_order(202), tag=11)]
        turns = [[*_pylon_main(), *second_base, *scvs], [*main_without_its_townhall, *second_base, *scvs]]
        _orders_sent(
            *turns,
            assigned=[(_HANDED_SCV, _MINED_FIELD), (11, 202)],
            started=read_the_workers_held,
            dead=lambda step: [_COMMAND_CENTER] * (step == 1),
            map_width=80,
        )
        assert kept == [[_HANDED_SCV, 11], [11]]


# Where a unit stands in the way of the move of `_HANDED_SCV` at (28.175, 24.5) to `_GATHER_POINT`, 0.4 off its line
# and out of the SCV's reach.
_IN_THE_WAY = (29.5, 24.9)


def _unit_at(at: tuple[float, float], unit_type: UnitTypeId, **fields: Any) -> raw_pb2.Unit:
    """A unit of `unit_type` at `at`, tag 20, this player's unless `fields` say otherwise, with a marine's radius."""
    return make_unit(20, unit_type, at=at, build_progress=1.0, radius=0.375, **fields)


class TestUnitsInTheWay:
    """A worker on a move, unlike one on a gather or a return, can be stopped by other units."""

    @pytest.mark.parametrize(
        "unit",
        [
            _unit_at(_IN_THE_WAY, UnitTypeId.MARINE),
            _unit_at(_IN_THE_WAY, UnitTypeId.ZERGLING, alliance=Alliance.ENEMY),
            _unit_at(_IN_THE_WAY, UnitTypeId.PROBE, alliance=Alliance.ENEMY),
            _unit_at(_IN_THE_WAY, UnitTypeId.SCV),
            _unit_at(_IN_THE_WAY, UnitTypeId.EGG),
        ],
        ids=["this player's marine", "an enemy's zergling", "an enemy's worker", "a worker not held", "an egg"],
    )
    def test_no_move_is_given_while_a_unit_stands_in_its_way(self, unit: raw_pb2.Unit) -> None:
        near = _reported_scv((28.175, 24.5), _gather_order())
        assert _orders_sent([*_pylon_main(), near, unit]) == [[]]

    def test_a_worker_on_the_move_with_a_unit_come_in_its_way_is_given_its_gather_back(self) -> None:
        on_its_way = _reported_scv((28.175, 24.5), _move_order(_GATHER_POINT), _gather_order())
        marine = _unit_at(_IN_THE_WAY, UnitTypeId.MARINE)
        assert _orders_sent([*_pylon_main(), on_its_way, marine]) == [[(AbilityId.GATHER, _MINED_FIELD, False)]]

    @pytest.mark.parametrize(
        "unit",
        [
            _unit_at((27.0, 21.0), UnitTypeId.MARINE),
            _unit_at(_IN_THE_WAY, UnitTypeId.LARVA),
            _unit_at(_IN_THE_WAY, UnitTypeId.LARVA, alliance=Alliance.ENEMY),
            _unit_at(_IN_THE_WAY, UnitTypeId.ROACH_BURROWED, is_burrowed=True),
            _unit_at(_IN_THE_WAY, UnitTypeId.MEDIVAC, is_flying=True),
        ],
        ids=["elsewhere in the mineral line", "a larva", "an enemy's larva", "burrowed", "flying"],
    )
    def test_a_unit_out_of_its_way_or_never_in_it_leaves_the_move_given(self, unit: raw_pb2.Unit) -> None:
        near = _reported_scv((28.175, 24.5), _gather_order())
        assert _orders_sent([*_pylon_main(), near, unit]) == [_move_then(AbilityId.GATHER, _GATHER_POINT, _MINED_FIELD)]

    def test_a_worker_let_go_in_the_turn_is_in_its_way(self) -> None:
        """SCV 20, handed over after `_HANDED_SCV`, mines `_OTHER_FIELD`, mined out in the second turn."""
        far = _reported_scv((25.0, 30.0), _gather_order())
        near = _reported_scv((28.175, 24.5), _gather_order())
        let_go = _reported_scv(_IN_THE_WAY, _gather_order(_OTHER_FIELD), tag=20)
        mined_out = [unit for unit in _pylon_main() if unit.tag != _OTHER_FIELD]
        both_held = [(_HANDED_SCV, _MINED_FIELD), (20, _OTHER_FIELD)]
        sent = _orders_sent(
            [*_pylon_main(), far, let_go],
            [*mined_out, near, let_go],
            assigned=both_held,
            dead=lambda step: [_OTHER_FIELD] * (step == 1),
        )
        assert sent == [[], []]

    def test_a_worker_the_bot_orders_in_the_turn_is_in_its_way(self) -> None:
        """SCV 20, handed over after `_HANDED_SCV`, mines `_OTHER_FIELD` and is held in the way of `_HANDED_SCV`'s move
        until the bot orders it to stop, before speed mining's turn: only the stop goes out."""
        far = _reported_scv((25.0, 30.0), _gather_order())
        near = _reported_scv((28.175, 24.5), _gather_order())
        in_the_way = _reported_scv(_IN_THE_WAY, _gather_order(_OTHER_FIELD), tag=20)

        def stop_it_in_the_second_turn(api: Api[SpeedMining]) -> None:
            @api.on(TurnEvent, priority=EventPriority.HIGH)
            def stop(event: TurnEvent) -> None:
                if event.time.steps == 1:
                    api.orders.issue(_own_unit_of_tag(api, 20), AbilityId.STOP)

        sent = _orders_sent(
            [*_pylon_main(), far, in_the_way],
            [*_pylon_main(), near, in_the_way],
            assigned=[(_HANDED_SCV, _MINED_FIELD), (20, _OTHER_FIELD)],
            started=stop_it_in_the_second_turn,
        )
        assert sent == [[], [(AbilityId.STOP, None, False)]]

    def test_a_worker_speed_mining_holds_is_out_of_its_way(self) -> None:
        """It mines `_OTHER_FIELD`, too far from where its trip ends to be given anything."""
        near = _reported_scv((28.175, 24.5), _gather_order())
        held = _reported_scv(_IN_THE_WAY, _gather_order(_OTHER_FIELD), tag=20)
        both_held = [(_HANDED_SCV, _MINED_FIELD), (20, _OTHER_FIELD)]
        assert _orders_sent([*_pylon_main(), near, held], assigned=both_held) == [
            _move_then(AbilityId.GATHER, _GATHER_POINT, _MINED_FIELD)
        ]

    def test_a_worker_speed_mining_holds_to_collect_is_out_of_its_way(self) -> None:
        """The collector, near enough `_REFINERY`, is given its own move, the miner out of its way too."""
        near = _reported_scv((28.175, 24.5), _gather_order())
        collector = _reported_scv(_IN_THE_WAY, _gather_order(_REFINERY), tag=20)
        assert _orders_sent([*_pylon_main(), _refinery(), near, collector], collected=[(20, _REFINERY)]) == [
            [
                *_move_then(AbilityId.GATHER, _GATHER_POINT, _MINED_FIELD),
                *_move_then(AbilityId.GATHER, _GAS_GATHER_POINT, _REFINERY),
            ]
        ]


def _zergling_beside(scv_at: tuple[float, float], *, degrees_off_its_facing: float) -> raw_pb2.Unit:
    """An enemy's zergling, tag 30, 0.8 from an SCV at `scv_at` facing east, within its reach of 0.95, its weapon's
    0.2 and both radii; at `degrees_off_its_facing` round from east."""
    angle = math.radians(degrees_off_its_facing)
    at = (scv_at[0] + 0.8 * math.cos(angle), scv_at[1] + 0.8 * math.sin(angle))
    return make_unit(30, UnitTypeId.ZERGLING, at=at, alliance=Alliance.ENEMY, build_progress=1.0, radius=0.375)


class TestAttacking:
    """A worker attacks an enemy it can hit as it stands: its cooldown at most a step, and a step's turn away at most, a
    worker's 62.5 degrees."""

    @pytest.mark.parametrize("degrees_off_its_facing", [0.0, 60.0])
    def test_a_worker_attacks_an_enemy_in_range_ahead_with_its_weapon_ready(
        self, degrees_off_its_facing: float
    ) -> None:
        scv = _reported_scv((26.0, 30.0), _gather_order(), facing=0.0)
        zergling = _zergling_beside((26.0, 30.0), degrees_off_its_facing=degrees_off_its_facing)
        assert _orders_sent([*_pylon_main(), scv, zergling]) == [[(AbilityId.ATTACK, 30, False)]]

    @pytest.mark.parametrize(
        ("degrees_off_its_facing", "weapon_cooldown"), [(180.0, 0.0), (0.0, 2.0)], ids=["behind it", "cooling down"]
    )
    def test_not_one_behind_it_or_while_its_weapon_cools_down(
        self, degrees_off_its_facing: float, weapon_cooldown: float
    ) -> None:
        scv = _reported_scv((26.0, 30.0), _gather_order(), facing=0.0, weapon_cooldown=weapon_cooldown)
        zergling = _zergling_beside((26.0, 30.0), degrees_off_its_facing=degrees_off_its_facing)
        assert _orders_sent([*_pylon_main(), scv, zergling]) == [[]]

    def test_not_when_speed_mining_was_built_not_to(self) -> None:
        scv = _reported_scv((26.0, 30.0), _gather_order(), facing=0.0)
        zergling = _zergling_beside((26.0, 30.0), degrees_off_its_facing=0.0)
        assert _orders_sent([*_pylon_main(), scv, zergling], attack_enemies=False) == [[]]

    def test_of_several_it_attacks_the_one_with_least_life(self) -> None:
        scv = _reported_scv((26.0, 30.0), _gather_order(), facing=0.0)
        healthy = _zergling_beside((26.0, 30.0), degrees_off_its_facing=-30.0)
        healthy.health, healthy.health_max = 35.0, 35.0
        wounded = _zergling_beside((26.0, 30.0), degrees_off_its_facing=30.0)
        wounded.tag, wounded.health, wounded.health_max = 31, 10.0, 35.0
        assert _orders_sent([*_pylon_main(), scv, healthy, wounded]) == [[(AbilityId.ATTACK, 31, False)]]

    def test_once_it_has_fired_it_goes_back_to_mining(self) -> None:
        """The turn after the attack is sent, its orders may not show it, so nothing goes out."""
        zergling = _zergling_beside((26.0, 30.0), degrees_off_its_facing=0.0)
        ready = _reported_scv((26.0, 30.0), _gather_order(), facing=0.0)
        attack = raw_pb2.UnitOrder(ability_id=RawAbilityId.attack_Attack, target_unit_tag=30)
        winding_up = _reported_scv((26.0, 30.0), attack, facing=0.0)
        fired = _reported_scv((26.0, 30.0), attack, facing=0.0, weapon_cooldown=20.0)
        turns = [[*_pylon_main(), scv, zergling] for scv in (ready, winding_up, winding_up, fired)]
        assert _orders_sent(*turns) == [
            [(AbilityId.ATTACK, 30, False)],
            [],
            [],
            [(AbilityId.GATHER, _MINED_FIELD, False)],
        ]

    def test_an_attack_no_order_lately_explains_is_left_alone(self) -> None:
        """Its orders show it attacking with no order issued or sent, speed mining's or the bot's: handing a worker over
        would send one on an attack to its field."""
        attack = raw_pb2.UnitOrder(ability_id=RawAbilityId.attack_Attack, target_unit_tag=30)
        mining = _reported_scv((26.0, 30.0), _gather_order(), facing=0.0, weapon_cooldown=20.0)
        attacking = _reported_scv((26.0, 30.0), attack, facing=0.0, weapon_cooldown=20.0)
        zergling = _zergling_beside((26.0, 30.0), degrees_off_its_facing=0.0)
        assert _orders_sent([*_pylon_main(), mining, zergling], [*_pylon_main(), attacking, zergling]) == [[], []]


class TestHandingOver:
    def test_mine_refuses_a_unit_that_is_not_a_worker_or_a_field_that_is_not_minerals(self) -> None:
        """Geyser 111 is not minerals, alone or among fields."""
        refused: list[str] = []

        def try_others(api: Api[SpeedMining]) -> None:
            marine = _own_unit_of_tag(api, 20)
            scv = _own_unit_of_tag(api, _HANDED_SCV)
            field = _unit_of_tag(api, _MINED_FIELD)
            geyser = _unit_of_tag(api, 111)
            for worker, fields in [(marine, field), (scv, geyser), (scv, [field, geyser])]:
                with pytest.raises(ValueError) as raised:
                    api.extensions.mine(worker, fields)
                refused.append(str(raised.value).split(",")[0])

        marine = make_unit(20, UnitTypeId.MARINE, at=(10.0, 10.0), build_progress=1.0)
        _orders_sent([*_pylon_main(), _reported_scv((25.0, 30.0)), marine], assigned=(), started=try_others)
        assert refused == [
            "speed mining orders workers",
            "speed mining mines mineral fields",
            "speed mining mines mineral fields",
        ]

    def test_mine_hands_no_third_worker_to_a_field_and_leaves_it_at_its_own(self) -> None:
        """SCV 12 mines `_OTHER_FIELD`; SCV 11, one of the two at `_MINED_FIELD`, can be handed it again."""
        handed: list[tuple[int | None, int | None, int | None]] = []

        def hand_over_a_third(api: Api[SpeedMining]) -> None:
            third = _own_unit_of_tag(api, 12)
            refused = api.extensions.mine(third, _unit_of_tag(api, _MINED_FIELD))
            handed_again = api.extensions.mine(_own_unit_of_tag(api, 11), _unit_of_tag(api, _MINED_FIELD))
            still_mined = api.extensions.miners.get(third)
            handed.append(
                (
                    None if refused is None else refused.tag,
                    None if handed_again is None else handed_again.tag,
                    None if still_mined is None else still_mined.tag,
                )
            )

        scvs = [_reported_scv((25.0, 30.0), _gather_order(), tag=tag) for tag in (_HANDED_SCV, 11, 12)]
        two_at_the_field_and_one_elsewhere = [(_HANDED_SCV, _MINED_FIELD), (11, _MINED_FIELD), (12, _OTHER_FIELD)]
        _orders_sent([*_pylon_main(), *scvs], assigned=two_at_the_field_and_one_elsewhere, started=hand_over_a_third)
        assert handed == [(None, _MINED_FIELD, _OTHER_FIELD)]

    def test_miners_reads_each_worker_held_with_its_field_and_no_dead_one(self) -> None:
        read: list[tuple[bool, int | None, int, list[int]]] = []

        def read_the_miners(api: Api[SpeedMining]) -> None:
            def read_them(event: TurnEvent) -> None:
                miners = api.extensions.miners
                scv = _own_unit_of_tag(api, 11) if event.time.steps == 0 else None
                field = None if scv is None else miners.get(scv)
                at_the_other_field = api.extensions.workers_at(_unit_of_tag(api, _OTHER_FIELD))
                tags = [worker.tag for worker in at_the_other_field]
                read.append((scv in miners, None if field is None else field.tag, len(miners), tags))

            api.on(TurnEvent, priority=EventPriority.LOWEST)(read_them)

        scvs = [_reported_scv((25.0, 30.0), _gather_order(), tag=tag) for tag in (_HANDED_SCV, 11)]
        both = [(_HANDED_SCV, _MINED_FIELD), (11, _OTHER_FIELD)]
        _orders_sent(
            [*_pylon_main(), *scvs],
            [*_pylon_main(), scvs[0]],
            assigned=both,
            started=read_the_miners,
            dead=lambda step: [11] * (step == 1),
        )
        assert read == [(True, _OTHER_FIELD, 2, [11]), (False, None, 1, [])]

    @pytest.mark.parametrize(
        ("held_at_its_nearest_fields", "picked"),
        [((), 102), ((102, 102), 104), ((102, 102, 104, 104, 101, 101, 103, 103), 107)],
        ids=["the nearest near field", "the next near field", "the nearest far field once the near ones are full"],
    )
    def test_mine_with_no_field_picks_one_at_the_nearest_base_near_ones_first(
        self, held_at_its_nearest_fields: tuple[int, ...], picked: int
    ) -> None:
        """An SCV at (31.0, 28.0), above `_MINED_FIELD`: far field 107, at (32.0, 23.5), is nearer than near field 104,
        at (31.0, 22.5), but comes after it."""
        others = [(20 + index, field) for index, field in enumerate(held_at_its_nearest_fields)]
        scvs = [_reported_scv((25.0, 30.0), tag=tag) for tag, _ in others]
        picked_fields: list[int | None] = []

        def hand_over_with_no_field(api: Api[SpeedMining]) -> None:
            field = api.extensions.mine(_own_unit_of_tag(api, _HANDED_SCV))
            picked_fields.append(None if field is None else field.tag)

        newcomer = _reported_scv((31.0, 28.0))
        _orders_sent([*_pylon_main(), newcomer, *scvs], assigned=others, started=hand_over_with_no_field)
        assert picked_fields == [picked]

    @pytest.mark.parametrize(
        ("held_at_the_nearer_base", "picked"),
        [((), 201), ((202, 202, 201, 201, 203, 203, 204, 204, 205, 205, 206, 206, 207, 207, 208, 208), 102)],
        ids=["the nearer base", "the other once it is full"],
    )
    def test_mine_with_no_field_picks_the_base_nearest_the_worker_with_room(
        self, held_at_the_nearer_base: tuple[int, ...], picked: int
    ) -> None:
        """A second base, its townhall at (56.5, 24.5), its fields tagged from 201; an SCV at (45.0, 28.0), nearer
        it."""
        others = [(20 + index, field) for index, field in enumerate(held_at_the_nearer_base)]
        scvs = [_reported_scv((50.0, 30.0), tag=tag) for tag, _ in others]
        picked_fields: list[int | None] = []

        def hand_over_with_no_field(api: Api[SpeedMining]) -> None:
            field = api.extensions.mine(_own_unit_of_tag(api, _HANDED_SCV))
            picked_fields.append(None if field is None else field.tag)

        bases = [*_pylon_main(), *_pylon_main(at=(56.5, 24.5), tags_from=200)]
        newcomer = _reported_scv((45.0, 28.0))
        _orders_sent([*bases, newcomer, *scvs], assigned=others, started=hand_over_with_no_field, map_width=80)
        assert picked_fields == [picked]

    @pytest.mark.parametrize(
        ("command_center_progress", "held_at_each_field"),
        [(0.5, 0), (1.0, 2)],
        ids=["no finished townhall of this player's", "two workers at each field"],
    )
    def test_mine_with_no_field_returns_none_where_no_base_has_room(
        self, command_center_progress: float, held_at_each_field: int
    ) -> None:
        others = [(20 + index, 101 + index // 2) for index in range(8 * held_at_each_field)]
        scvs = [_reported_scv((25.0, 30.0), tag=tag) for tag, _ in others]
        handed: list[tuple[bool, bool]] = []

        def hand_over_with_no_field(api: Api[SpeedMining]) -> None:
            newcomer = _own_unit_of_tag(api, _HANDED_SCV)
            handed.append((api.extensions.mine(newcomer) is None, newcomer in api.extensions.miners))

        units = [*_pylon_main(command_center_progress=command_center_progress), _reported_scv((31.0, 28.0)), *scvs]
        _orders_sent(units, assigned=others, started=hand_over_with_no_field)
        assert handed == [(True, False)]

    @pytest.mark.parametrize(
        ("given", "picked"),
        [((107, 104, 108), 104), ((107, 108), 107), (108, 108)],
        ids=["a near field before a nearer far one", "the nearest of the far ones", "a single field"],
    )
    def test_mine_given_fields_picks_among_them_as_with_none(self, given: int | tuple[int, ...], picked: int) -> None:
        """An SCV at (31.0, 28.0), above `_MINED_FIELD`, 102, which `mine` picks given no field: far field 107, at
        (32.0, 23.5), is nearer than near field 104, at (31.0, 22.5), and far field 108, at (31.0, 20.5)."""
        picked_fields: list[int | None] = []

        def hand_over_given_fields(api: Api[SpeedMining]) -> None:
            fields = _unit_of_tag(api, given) if isinstance(given, int) else [_unit_of_tag(api, tag) for tag in given]
            field = api.extensions.mine(_own_unit_of_tag(api, _HANDED_SCV), fields)
            picked_fields.append(None if field is None else field.tag)

        newcomer = _reported_scv((31.0, 28.0))
        _orders_sent([*_pylon_main(), newcomer], assigned=(), started=hand_over_given_fields)
        assert picked_fields == [picked]

    @pytest.mark.parametrize(
        "base",
        [
            _pylon_main(command_center_progress=0.5),
            [unit for unit in _pylon_main() if unit.tag != _COMMAND_CENTER],
            [unit for unit in _pylon_main() if unit.tag != _COMMAND_CENTER]
            + [make_unit(_COMMAND_CENTER, UnitTypeId.COMMAND_CENTER, at=(25.5, 24.5), build_progress=1.0)],
        ],
        ids=["an unfinished townhall", "no townhall", "a townhall off its center"],
    )
    def test_mine_given_a_field_at_a_base_without_a_finished_townhall_at_its_center_returns_none(
        self, base: list[raw_pb2.Unit]
    ) -> None:
        handed: list[tuple[bool, bool]] = []

        def hand_over_at_the_field(api: Api[SpeedMining]) -> None:
            worker = _own_unit_of_tag(api, _HANDED_SCV)
            refused = api.extensions.mine(worker, _unit_of_tag(api, _MINED_FIELD)) is None
            handed.append((refused, worker in api.extensions.miners))

        _orders_sent([*base, _reported_scv((25.0, 30.0))], assigned=(), started=hand_over_at_the_field)
        assert handed == [(True, False)]

    def test_built_before_the_game_starts_it_raises(self) -> None:
        with pytest.raises(NotPlayingError):
            SpeedMining(Api())


def _collectors_held_each_turn(held: list[list[int]]) -> Callable[[Api[SpeedMining]], object]:
    """What `_orders_sent` runs as the game starts to append to `held` the tags of the collectors speed mining holds at
    the end of each turn."""

    def read_the_collectors_held(api: Api[SpeedMining]) -> None:
        api.on(TurnEvent, priority=EventPriority.LOWEST)(
            lambda event: held.append(sorted(worker.tag for worker in api.extensions.collectors))
        )

    return read_the_collectors_held


class TestCollecting:
    """A worker handed over with `collect` is kept collecting vespene from its gas building, far from where its trips
    end on plain gathers and returns."""

    def test_an_idle_worker_is_sent_to_gather_at_its_gas_building_once(self) -> None:
        """The next turn's orders may not show the gather yet; nothing goes out again."""
        idle = [*_pylon_main(), _refinery(), _reported_scv((28.0, 27.0))]
        assert _orders_sent(idle, idle, assigned=(), collected=[(_HANDED_SCV, _REFINERY)]) == [
            [(AbilityId.GATHER, _REFINERY, False)],
            [],
        ]

    @pytest.mark.parametrize(
        "collecting",
        [
            _reported_scv(_FAR_FROM_THE_GAS_TRIP_ENDS, _gather_order(_REFINERY)),
            _scv_carrying_vespene(_FAR_FROM_THE_GAS_TRIP_ENDS, _return_order()),
            _reported_scv(_FAR_FROM_THE_GAS_TRIP_ENDS, _return_order()),
        ],
        ids=["gathering", "returning vespene", "returning gas from a rich geyser, which shows no cargo"],
    )
    def test_a_collector_gathering_or_returning_is_left_to_it(self, collecting: raw_pb2.Unit) -> None:
        collector = [*_pylon_main(), _refinery(), collecting]
        assert _orders_sent(collector, collector, assigned=(), collected=[(_HANDED_SCV, _REFINERY)]) == [[], []]

    def test_a_collector_gathering_at_another_gas_building_is_sent_to_its_own(self) -> None:
        elsewhere = _reported_scv((25.0, 20.0), _gather_order(_OTHER_REFINERY))
        units = [*_pylon_main(), _refinery(), _refinery(_OTHER_REFINERY), elsewhere]
        assert _orders_sent(units, assigned=(), collected=[(_HANDED_SCV, _REFINERY)]) == [
            [(AbilityId.GATHER, _REFINERY, False)]
        ]

    def test_a_collector_idle_with_cargo_returns_it_with_the_gather_queued_behind(self) -> None:
        collecting = [*_pylon_main(), _refinery(), _reported_scv(_FAR_FROM_THE_GAS_TRIP_ENDS, _gather_order(_REFINERY))]
        idle_with_vespene = [*_pylon_main(), _refinery(), _scv_carrying_vespene(_FAR_FROM_THE_GAS_TRIP_ENDS)]
        assert _orders_sent(collecting, idle_with_vespene, assigned=(), collected=[(_HANDED_SCV, _REFINERY)]) == [
            [],
            [(AbilityId.RETURN, None, False), (AbilityId.GATHER, _REFINERY, True)],
        ]

    def test_a_collector_inside_its_gas_building_is_left_alone_and_still_held(self) -> None:
        """Inside, it is out of the observation."""
        held: list[list[int]] = []
        gathering = [*_pylon_main(), _refinery(), _reported_scv((30.0, 26.5), _gather_order(_REFINERY))]
        inside = [*_pylon_main(), _refinery()]
        out_with_vespene = [*_pylon_main(), _refinery(), _scv_carrying_vespene((30.0, 26.5), _return_order())]
        sent = _orders_sent(
            gathering,
            inside,
            out_with_vespene,
            assigned=(),
            collected=[(_HANDED_SCV, _REFINERY)],
            started=_collectors_held_each_turn(held),
        )
        assert sent == [[], [], []]
        assert held == [[_HANDED_SCV]] * 3

    def test_a_collector_on_an_order_no_order_lately_explains_is_left_alone_until_it_is_idle(self) -> None:
        """Its orders show it mining, and then building, with no order issued or sent."""
        turns = [
            [*_pylon_main(), _refinery(), _reported_scv(_FAR_FROM_THE_GAS_TRIP_ENDS, order)]
            for order in (_gather_order(_REFINERY), _gather_order(_MINED_FIELD), _build_order())
        ]
        idle = [*_pylon_main(), _refinery(), _reported_scv((30.0, 33.0))]
        assert _orders_sent(*turns, idle, assigned=(), collected=[(_HANDED_SCV, _REFINERY)]) == [
            [],
            [],
            [],
            [(AbilityId.GATHER, _REFINERY, False)],
        ]

    def test_a_collector_attacks_an_enemy_it_can_hit(self) -> None:
        scv = _reported_scv((26.0, 30.0), _gather_order(_REFINERY), facing=0.0)
        zergling = _zergling_beside((26.0, 30.0), degrees_off_its_facing=0.0)
        units = [*_pylon_main(), _refinery(), scv, zergling]
        assert _orders_sent(units, assigned=(), collected=[(_HANDED_SCV, _REFINERY)]) == [
            [(AbilityId.ATTACK, 30, False)]
        ]

    def test_a_released_collector(self) -> None:
        """It gathers at another gas building, where speed mining would send it back to its own."""

        def release(api: Api[SpeedMining]) -> None:
            api.extensions.release(_own_unit_of_tag(api, _HANDED_SCV))

        elsewhere = _reported_scv((25.0, 20.0), _gather_order(_OTHER_REFINERY))
        units = [*_pylon_main(), _refinery(), _refinery(_OTHER_REFINERY), elsewhere]
        assert _orders_sent(units, assigned=(), collected=[(_HANDED_SCV, _REFINERY)], started=release) == [[]]


class TestSpeedCollecting:
    """Each trip of a collector ends in a move, as a miner's does, the trip read off its gather or return, and one
    coming out of its gas building yields to one going in on its move."""

    def test_a_collector_near_its_gas_building_is_given_the_move_with_the_gather_behind_once(self) -> None:
        """Its orders may not show the move in the next observation; nothing goes out again for it."""
        near = [
            *_pylon_main(),
            _refinery(),
            _reported_scv(_between_the_refinery_and_the_townhall(3.4), _gather_order(_REFINERY)),
        ]
        assert _orders_sent(near, near, assigned=(), collected=[(_HANDED_SCV, _REFINERY)]) == [
            _move_then(AbilityId.GATHER, _GAS_GATHER_POINT, _REFINERY),
            [],
        ]

    @pytest.mark.parametrize(
        "returning",
        [
            _scv_carrying_vespene(_between_the_refinery_and_the_townhall(3.3), _return_order()),
            _reported_scv(_between_the_refinery_and_the_townhall(3.3), _return_order()),
        ],
        ids=["vespene", "gas from a rich geyser, which shows no cargo"],
    )
    def test_a_collector_near_the_townhall_returning_is_given_the_move_with_the_return_behind(
        self, returning: raw_pb2.Unit
    ) -> None:
        units = [*_pylon_main(), _refinery(), returning]
        assert _orders_sent(units, assigned=(), collected=[(_HANDED_SCV, _REFINERY)]) == [
            _move_then(AbilityId.SMART, _GAS_RETURN_POINT, _COMMAND_CENTER)
        ]

    @pytest.mark.parametrize("distance_from_the_refinery", [2.5, 4.5])
    def test_a_collector_too_near_or_too_far_from_where_its_trip_ends_is_left_on_its_gather(
        self, distance_from_the_refinery: float
    ) -> None:
        """0.44 and 2.44 from where the move of its trip ends."""
        at = _between_the_refinery_and_the_townhall(distance_from_the_refinery)
        units = [*_pylon_main(), _refinery(), _reported_scv(at, _gather_order(_REFINERY))]
        assert _orders_sent(units, assigned=(), collected=[(_HANDED_SCV, _REFINERY)]) == [[]]

    def test_a_collector_on_its_way_to_a_field_it_was_handed_before_is_sent_to_its_own_gas_building(self) -> None:
        on_its_way = _reported_scv((28.175, 24.5), _move_order(_GATHER_POINT), _gather_order())
        units = [*_pylon_main(), _refinery(), on_its_way]
        assert _orders_sent(units, assigned=(), collected=[(_HANDED_SCV, _REFINERY)]) == [
            [(AbilityId.GATHER, _REFINERY, False)]
        ]

    def test_no_move_is_given_to_a_collector_while_a_unit_stands_in_its_way(self) -> None:
        near = _reported_scv(_between_the_refinery_and_the_townhall(3.4), _gather_order(_REFINERY))
        marine = _unit_at(_between_the_refinery_and_the_townhall(2.7), UnitTypeId.MARINE)
        units = [*_pylon_main(), _refinery(), near, marine]
        assert _orders_sent(units, assigned=(), collected=[(_HANDED_SCV, _REFINERY)]) == [[]]

    def test_one_coming_out_is_given_no_move_until_one_going_in_on_its_move_has_passed(self) -> None:
        """SCV 11 goes in on its move, from 3.6 from the refinery's center to 2.5, while `_HANDED_SCV` comes out on
        its return, from 3.0 to 3.5, both turns near enough the townhall for its move."""
        going_in = [
            _reported_scv(
                _between_the_refinery_and_the_townhall(distance),
                _move_order(_GAS_GATHER_POINT),
                _gather_order(_REFINERY),
                tag=11,
            )
            for distance in (3.6, 2.5)
        ]
        coming_out = [
            _scv_carrying_vespene(_between_the_refinery_and_the_townhall(distance), _return_order())
            for distance in (3.0, 3.5)
        ]
        sent = _orders_sent(
            *([*_pylon_main(), _refinery(), out, into] for out, into in zip(coming_out, going_in, strict=True)),
            assigned=(),
            collected=[(_HANDED_SCV, _REFINERY), (11, _REFINERY)],
        )
        assert sent == [[], _move_then(AbilityId.SMART, _GAS_RETURN_POINT, _COMMAND_CENTER)]

    @pytest.mark.parametrize(
        "carrying_vespene", [True, False], ids=["vespene", "gas from a rich geyser, which shows no cargo"]
    )
    def test_one_coming_out_on_its_move_is_given_its_return_back_when_one_going_in_is_given_its_move(
        self, carrying_vespene: bool
    ) -> None:
        """SCV 11, going in, 3.4 from the refinery's center, has yet to pass `_HANDED_SCV`, 3.0 from it. A gather
        alone would send the one coming out back with its cargo."""
        going_in = _reported_scv(_between_the_refinery_and_the_townhall(3.4), _gather_order(_REFINERY), tag=11)
        at = _between_the_refinery_and_the_townhall(3.0)
        orders = (_move_order(_GAS_RETURN_POINT), _return_order())
        coming_out = _scv_carrying_vespene(at, *orders) if carrying_vespene else _reported_scv(at, *orders)
        sent = _orders_sent(
            [*_pylon_main(), _refinery(), coming_out, going_in],
            assigned=(),
            collected=[(_HANDED_SCV, _REFINERY), (11, _REFINERY)],
        )
        assert sent == [
            [
                *_move_then(AbilityId.GATHER, _GAS_GATHER_POINT, _REFINERY),
                (AbilityId.RETURN, None, False),
                (AbilityId.GATHER, _REFINERY, True),
            ]
        ]

    def test_one_going_in_sent_its_move_lately_keeps_one_coming_out_off_its_move(self) -> None:
        """SCV 11, going in, is given its move in the first turn, which its orders do not show yet in the second,
        where it has yet to pass `_HANDED_SCV`, near enough the townhall for its move by then."""
        going_in = [
            _reported_scv(_between_the_refinery_and_the_townhall(distance), _gather_order(_REFINERY), tag=11)
            for distance in (3.4, 3.0)
        ]
        coming_out = [
            _scv_carrying_vespene(_between_the_refinery_and_the_townhall(distance), _return_order())
            for distance in (2.3, 2.7)
        ]
        sent = _orders_sent(
            *([*_pylon_main(), _refinery(), out, into] for out, into in zip(coming_out, going_in, strict=True)),
            assigned=(),
            collected=[(_HANDED_SCV, _REFINERY), (11, _REFINERY)],
        )
        assert sent == [_move_then(AbilityId.GATHER, _GAS_GATHER_POINT, _REFINERY), []]

    def test_one_coming_out_sent_its_move_lately_is_given_its_return_back_when_one_going_in_is_given_its_move(
        self,
    ) -> None:
        """`_HANDED_SCV`, coming out, is given its move in the first turn, which its orders do not show yet in the
        second, where SCV 11, going in, is given its own and has yet to pass it."""
        coming_out = [
            _scv_carrying_vespene(_between_the_refinery_and_the_townhall(distance), _return_order())
            for distance in (3.0, 3.2)
        ]
        going_in = [
            _reported_scv(_FAR_FROM_THE_GAS_TRIP_ENDS, _gather_order(_REFINERY), tag=11),
            _reported_scv(_between_the_refinery_and_the_townhall(3.6), _gather_order(_REFINERY), tag=11),
        ]
        sent = _orders_sent(
            *([*_pylon_main(), _refinery(), out, into] for out, into in zip(coming_out, going_in, strict=True)),
            assigned=(),
            collected=[(_HANDED_SCV, _REFINERY), (11, _REFINERY)],
        )
        assert sent == [
            _move_then(AbilityId.SMART, _GAS_RETURN_POINT, _COMMAND_CENTER),
            [
                *_move_then(AbilityId.GATHER, _GAS_GATHER_POINT, _REFINERY),
                (AbilityId.RETURN, None, False),
                (AbilityId.GATHER, _REFINERY, True),
            ],
        ]


class TestLettingGoOfACollector:
    @pytest.mark.parametrize(
        ("second_turn", "dead"),
        [
            (_pylon_main(), _REFINERY),
            ([*_pylon_main(), _refinery(vespene_contents=0)], None),
            ([unit for unit in _pylon_main() if unit.tag != _COMMAND_CENTER] + [_refinery()], _COMMAND_CENTER),
        ],
        ids=["its gas building dies", "its gas building runs dry", "its base's townhall dies"],
    )
    def test_a_collector_is_let_go_with_its_orders_as_they_are(
        self, second_turn: list[raw_pb2.Unit], dead: int | None
    ) -> None:
        """The second turn's worker, idle with cargo, would be sent to return it if speed mining held it."""
        held: list[list[int]] = []
        turns = [
            [*_pylon_main(), _refinery(), _reported_scv(_FAR_FROM_THE_GAS_TRIP_ENDS, _gather_order(_REFINERY))],
            [*second_turn, _scv_carrying_vespene(_FAR_FROM_THE_GAS_TRIP_ENDS)],
        ]
        sent = _orders_sent(
            *turns,
            assigned=(),
            collected=[(_HANDED_SCV, _REFINERY)],
            started=_collectors_held_each_turn(held),
            dead=lambda step: [dead] if dead is not None and step == 1 else [],
        )
        assert sent == [[], []]
        assert held == [[_HANDED_SCV], []]


class TestHandingOverToCollect:
    def test_collect_refuses_a_unit_that_is_not_a_worker_or_a_gas_building_that_is_not_one(self) -> None:
        """`_MINED_FIELD` is not a gas building, alone or among them, and neither is geyser 111 under the refinery."""
        refused: list[str] = []

        def try_others(api: Api[SpeedMining]) -> None:
            marine = _own_unit_of_tag(api, 20)
            scv = _own_unit_of_tag(api, _HANDED_SCV)
            refinery = _unit_of_tag(api, _REFINERY)
            field = _unit_of_tag(api, _MINED_FIELD)
            geyser = _unit_of_tag(api, 111)
            for worker, gas_buildings in [(marine, refinery), (scv, field), (scv, [refinery, field]), (scv, geyser)]:
                with pytest.raises(ValueError) as raised:
                    api.extensions.collect(worker, gas_buildings)
                refused.append(str(raised.value).split(",")[0])

        marine = make_unit(20, UnitTypeId.MARINE, at=(10.0, 10.0), build_progress=1.0)
        units = [*_pylon_main(), _refinery(), _reported_scv((25.0, 30.0)), marine]
        _orders_sent(units, assigned=(), started=try_others)
        assert refused == ["speed mining orders workers"] + ["speed mining collects from gas buildings"] * 3

    @pytest.mark.parametrize(
        "units",
        [
            [*_pylon_main(), _refinery(build_progress=0.5)],
            [*_pylon_main(), _refinery(vespene_contents=0)],
            [*_pylon_main(), _refinery(alliance=Alliance.ENEMY)],
            [*_pylon_main(command_center_progress=0.5), _refinery()],
            [*_pylon_main(), _refinery(), *(_reported_scv((28.0, 27.0), tag=tag) for tag in (20, 21, 22))],
        ],
        ids=[
            "an unfinished gas building",
            "one with no vespene left",
            "the enemy's",
            "one at a base without a finished townhall",
            "one three others collect from",
        ],
    )
    def test_collect_returns_none_where_there_is_no_room_and_leaves_the_worker_as_it_was(
        self, units: list[raw_pb2.Unit]
    ) -> None:
        """The worker, gathering at `_MINED_FIELD`, is handed over to mine it first, which `mine` refuses at a base
        without a finished townhall: whether speed mining holds it there or not, `collect` leaves that as it was."""
        handed: list[tuple[bool, bool, bool]] = []

        def hand_over_to_collect(api: Api[SpeedMining]) -> None:
            for tag in (20, 21, 22):
                if any(unit.tag == tag for unit in api.units):
                    api.extensions.collect(_own_unit_of_tag(api, tag), _unit_of_tag(api, _REFINERY))
            worker = _own_unit_of_tag(api, _HANDED_SCV)
            mined = worker in api.extensions.miners
            refused = api.extensions.collect(worker, _unit_of_tag(api, _REFINERY)) is None
            handed.append((refused, worker in api.extensions.collectors, (worker in api.extensions.miners) == mined))

        _orders_sent([*units, _reported_scv((25.0, 30.0), _gather_order())], started=hand_over_to_collect)
        assert handed == [(True, False, True)]

    def test_collect_takes_no_worker_it_cannot_send(self) -> None:
        """The worker, kept from the first turn, is out of reach of orders in the second, as inside a gas building:
        `collect` returns `None`, holds nothing and sends nothing."""
        handed: list[tuple[bool, bool]] = []

        def hand_over_in_the_second_turn(api: Api[SpeedMining]) -> None:
            worker = _own_unit_of_tag(api, _HANDED_SCV)

            @api.on(TurnEvent, priority=EventPriority.HIGH)
            def hand_over(event: TurnEvent) -> None:
                if event.time.steps == 1:
                    refused = api.extensions.collect(worker, _unit_of_tag(api, _REFINERY)) is None
                    handed.append((refused, worker in api.extensions.collectors))

        turns = [[*_pylon_main(), _refinery(), _reported_scv((30.0, 26.5))], [*_pylon_main(), _refinery()]]
        assert _orders_sent(*turns, assigned=(), started=hand_over_in_the_second_turn) == [[], []]
        assert handed == [(True, False)]

    @pytest.mark.parametrize(
        ("collectors_at_the_nearer", "collectors_at_the_farther", "picked"),
        [
            (0, 0, _REFINERY),
            (1, 0, _REFINERY),
            (2, 0, _OTHER_REFINERY),
            (2, 1, _OTHER_REFINERY),
            (2, 2, _OTHER_REFINERY),
            (3, 2, _OTHER_REFINERY),
            (2, 3, _REFINERY),
        ],
        ids=[
            "the nearer to the townhall",
            "the nearer to two",
            "the farther once the nearer has two",
            "the farther to two",
            "a third at the farther",
            "a third at the farther once the nearer is full",
            "a third at the nearer once the farther is full",
        ],
    )
    def test_collect_with_no_gas_building_fills_each_to_two_nearest_the_townhall_first_and_then_to_three_farthest_first(
        self, collectors_at_the_nearer: int, collectors_at_the_farther: int, picked: int
    ) -> None:
        """An SCV at (22.0, 19.0), beside `_OTHER_REFINERY`, the farther from the townhall's center."""
        others = [
            *((20 + index, _REFINERY) for index in range(collectors_at_the_nearer)),
            *((30 + index, _OTHER_REFINERY) for index in range(collectors_at_the_farther)),
        ]
        scvs = [_reported_scv(_FAR_FROM_THE_GAS_TRIP_ENDS, tag=tag) for tag, _ in others]
        picked_gas_buildings: list[int | None] = []

        def hand_over_with_no_gas_building(api: Api[SpeedMining]) -> None:
            gas_building = api.extensions.collect(_own_unit_of_tag(api, _HANDED_SCV))
            picked_gas_buildings.append(None if gas_building is None else gas_building.tag)

        units = [*_pylon_main(), _refinery(), _refinery(_OTHER_REFINERY), _reported_scv((22.0, 19.0)), *scvs]
        _orders_sent(units, assigned=(), collected=others, started=hand_over_with_no_gas_building)
        assert picked_gas_buildings == [picked]

    @pytest.mark.parametrize(
        ("collectors_by_refinery", "picked"),
        [
            ({_REFINERY: 0, 221: 0, 222: 0}, 221),
            ({_REFINERY: 0, 221: 3, 222: 3}, _REFINERY),
            ({_REFINERY: 0, 221: 2, 222: 2}, _REFINERY),
            ({_OTHER_REFINERY: 2, 221: 2}, 221),
        ],
        ids=[
            "the nearer base",
            "the other once it is full",
            "one under two at the other before a third",
            "a third base by base",
        ],
    )
    def test_collect_with_no_gas_building_fills_each_base_to_two_the_one_nearest_the_worker_first_before_a_third(
        self, collectors_by_refinery: dict[int, int], picked: int
    ) -> None:
        """A second base, its townhall at (56.5, 24.5), its refineries tagged 221, the nearer its townhall, and 222; an
        SCV at (42.0, 28.0), nearer it, though nearer `_REFINERY` than either of them. A third goes to the nearer
        base's 221 though `_OTHER_REFINERY` stands farther from its townhall, 8.06 against 7.62."""
        held_at = [refinery for refinery, count in collectors_by_refinery.items() for _ in range(count)]
        others = [(20 + index, refinery) for index, refinery in enumerate(held_at)]
        scvs = [_reported_scv((60.0, 27.0), tag=tag) for tag, _ in others]
        picked_gas_buildings: list[int | None] = []

        def hand_over_with_no_gas_building(api: Api[SpeedMining]) -> None:
            gas_building = api.extensions.collect(_own_unit_of_tag(api, _HANDED_SCV))
            picked_gas_buildings.append(None if gas_building is None else gas_building.tag)

        refineries = [
            _refinery(tag, at=(56.5, 24.5) if tag > 200 else _TOWNHALL_CENTER) for tag in collectors_by_refinery
        ]
        bases = [*_pylon_main(), *_pylon_main(at=(56.5, 24.5), tags_from=200), *refineries]
        units = [*bases, _reported_scv((42.0, 28.0)), *scvs]
        _orders_sent(units, assigned=(), collected=others, started=hand_over_with_no_gas_building, map_width=80)
        assert picked_gas_buildings == [picked]

    def test_collect_given_gas_buildings_picks_among_them(self) -> None:
        """An SCV beside `_REFINERY`, which `collect` picks given none."""
        picked_gas_buildings: list[int | None] = []

        def hand_over_given_gas_buildings(api: Api[SpeedMining]) -> None:
            gas_building = api.extensions.collect(
                _own_unit_of_tag(api, _HANDED_SCV), [_unit_of_tag(api, _OTHER_REFINERY)]
            )
            picked_gas_buildings.append(None if gas_building is None else gas_building.tag)

        units = [*_pylon_main(), _refinery(), _refinery(_OTHER_REFINERY), _reported_scv((30.0, 26.0))]
        _orders_sent(units, assigned=(), started=hand_over_given_gas_buildings)
        assert picked_gas_buildings == [_OTHER_REFINERY]

    @pytest.mark.parametrize(
        ("handed", "sent"),
        [
            (_reported_scv((28.0, 27.0), _gather_order()), [(AbilityId.GATHER, _REFINERY, False)]),
            (
                _reported_scv((28.0, 27.0), _return_order(), carrying=True),
                [(AbilityId.RETURN, None, False), (AbilityId.GATHER, _REFINERY, True)],
            ),
        ],
        ids=["gathering minerals", "returning minerals"],
    )
    def test_a_miner_handed_over_to_collect_is_sent_at_once_and_moves(
        self, handed: raw_pb2.Unit, sent: list[tuple[int, Point | int | None, bool]]
    ) -> None:
        moved: list[tuple[bool, bool]] = []

        def hand_over_to_collect(api: Api[SpeedMining]) -> None:
            worker = _own_unit_of_tag(api, _HANDED_SCV)
            api.extensions.collect(worker, _unit_of_tag(api, _REFINERY))
            moved.append((worker in api.extensions.miners, worker in api.extensions.collectors))

        assert _orders_sent([*_pylon_main(), _refinery(), handed], started=hand_over_to_collect) == [sent]
        assert moved == [(False, True)]

    def test_a_collector_handed_over_to_mine_is_sent_at_once_and_moves(self) -> None:
        moved: list[tuple[bool, bool]] = []

        def hand_over_to_mine(api: Api[SpeedMining]) -> None:
            worker = _own_unit_of_tag(api, _HANDED_SCV)
            api.extensions.mine(worker, _unit_of_tag(api, _MINED_FIELD))
            moved.append((worker in api.extensions.miners, worker in api.extensions.collectors))

        units = [*_pylon_main(), _refinery(), _reported_scv((28.0, 27.0), _gather_order(_REFINERY))]
        assert _orders_sent(units, assigned=(), collected=[(_HANDED_SCV, _REFINERY)], started=hand_over_to_mine) == [
            [(AbilityId.GATHER, _MINED_FIELD, False)]
        ]
        assert moved == [(True, False)]

    def test_collectors_reads_each_worker_held_with_its_gas_building_and_no_dead_one(self) -> None:
        read: list[tuple[int, list[int]]] = []

        def read_the_collectors(api: Api[SpeedMining]) -> None:
            def read_them(event: TurnEvent) -> None:
                at_the_other = api.extensions.workers_at(_unit_of_tag(api, _OTHER_REFINERY))
                read.append((len(api.extensions.collectors), [worker.tag for worker in at_the_other]))

            api.on(TurnEvent, priority=EventPriority.LOWEST)(read_them)

        refineries = [_refinery(), _refinery(_OTHER_REFINERY)]
        scvs = [
            _reported_scv((28.0, 27.0), _gather_order(_REFINERY)),
            _reported_scv((22.0, 19.0), _gather_order(_OTHER_REFINERY), tag=11),
        ]
        _orders_sent(
            [*_pylon_main(), *refineries, *scvs],
            [*_pylon_main(), *refineries, scvs[0]],
            assigned=(),
            collected=[(_HANDED_SCV, _REFINERY), (11, _OTHER_REFINERY)],
            started=read_the_collectors,
            dead=lambda step: [11] * (step == 1),
        )
        assert read == [(2, [11]), (1, [])]

    def test_workers_reads_the_miners_and_the_collectors_and_no_dead_one(self) -> None:
        read: list[list[tuple[int, int]]] = []

        def read_the_workers(api: Api[SpeedMining]) -> None:
            api.on(TurnEvent, priority=EventPriority.LOWEST)(
                lambda event: read.append(
                    sorted((worker.tag, held_at.tag) for worker, held_at in api.extensions.workers.items())
                )
            )

        scvs = [
            _reported_scv((25.0, 30.0), _gather_order()),
            _reported_scv((28.0, 27.0), _gather_order(_REFINERY), tag=11),
        ]
        _orders_sent(
            [*_pylon_main(), _refinery(), *scvs],
            [*_pylon_main(), _refinery(), scvs[0]],
            collected=[(11, _REFINERY)],
            started=read_the_workers,
            dead=lambda step: [11] * (step == 1),
        )
        assert read == [[(_HANDED_SCV, _MINED_FIELD), (11, _REFINERY)], [(_HANDED_SCV, _MINED_FIELD)]]


def _released_and_kept(
    release: Callable[[Api[SpeedMining]], Units[OwnUnit[Any]]],
    read: list[tuple[list[int], list[int]]],
) -> Callable[[Api[SpeedMining]], object]:
    """What `_orders_sent` runs as the game starts to `release` workers and append to `read` the tags released, in the
    order returned, and those of the workers speed mining still holds."""

    def release_and_read(api: Api[SpeedMining]) -> None:
        released = release(api)
        read.append(([worker.tag for worker in released], sorted(worker.tag for worker in api.extensions.workers)))

    return release_and_read


class TestReleasing:
    @pytest.mark.parametrize(
        ("count", "released"),
        [
            (0, []),
            (1, [22]),
            (2, [22, 32]),
            (3, [22, 32, 31]),
            (4, [22, 32, 31, 30]),
            (6, [22, 32, 31, 30, 21, 20]),
            (7, [22, 32, 31, 30, 21, 20]),
        ],
        ids=[
            "none",
            "the third at the nearer",
            "the third at the farther next",
            "then the farther's others",
            "the farther's last",
            "then the nearer's",
            "all where it holds fewer",
        ],
    )
    def test_release_collectors_releases_thirds_nearest_first_then_the_others_farthest_first(
        self, count: int, released: list[int]
    ) -> None:
        """Three collect from `_REFINERY`, the nearer the townhall, handed over as 20, 21 and 22, and three from
        `_OTHER_REFINERY`, as 30, 31 and 32."""
        others = [
            *((20 + index, _REFINERY) for index in range(3)),
            *((30 + index, _OTHER_REFINERY) for index in range(3)),
        ]
        scvs = [_reported_scv(_FAR_FROM_THE_GAS_TRIP_ENDS, tag=tag) for tag, _ in others]
        read: list[tuple[list[int], list[int]]] = []
        units = [*_pylon_main(), _refinery(), _refinery(_OTHER_REFINERY), *scvs]
        release = _released_and_kept(lambda api: api.extensions.release_collectors(count), read)
        _orders_sent(units, assigned=(), collected=others, started=release)
        assert read == [(released, sorted(tag for tag, _ in others if tag not in released))]

    def test_release_collectors_releases_the_one_handed_over_last_of_those_alike(self) -> None:
        """Two collect from `_REFINERY`, handed over as 20 and 21, and two from the second base's 221, as far from its
        townhall, as 30 and 31: the second base's go first, as handed over later."""
        others = [(20, _REFINERY), (21, _REFINERY), (30, 221), (31, 221)]
        scvs = [_reported_scv(_FAR_FROM_THE_GAS_TRIP_ENDS, tag=tag) for tag, _ in others]
        read: list[tuple[list[int], list[int]]] = []
        second_base = [*_pylon_main(at=(56.5, 24.5), tags_from=200), _refinery(221, at=(56.5, 24.5))]
        units = [*_pylon_main(), _refinery(), *second_base, *scvs]
        release = _released_and_kept(lambda api: api.extensions.release_collectors(3), read)
        _orders_sent(units, assigned=(), collected=others, started=release, map_width=80)
        assert read == [([31, 30, 21], [20])]

    def test_release_collectors_releases_one_speed_mining_would_let_go_first(self) -> None:
        """`_OTHER_REFINERY` has no vespene left in the second turn, when the bot releases a collector before speed
        mining's turn lets its collector go: that one, 30, leaves before the third at `_REFINERY`, 22."""
        others = [*((20 + index, _REFINERY) for index in range(3)), (30, _OTHER_REFINERY)]
        scvs = [_reported_scv(_FAR_FROM_THE_GAS_TRIP_ENDS, tag=tag) for tag, _ in others]
        released: list[list[int]] = []

        def release_in_the_second_turn(api: Api[SpeedMining]) -> None:
            @api.on(TurnEvent, priority=EventPriority.HIGH)
            def release(event: TurnEvent) -> None:
                if event.time.steps == 1:
                    released.append([worker.tag for worker in api.extensions.release_collectors(1)])

        refineries = [_refinery(), _refinery(_OTHER_REFINERY)]
        mined_out = [_refinery(), _refinery(_OTHER_REFINERY, vespene_contents=0)]
        _orders_sent(
            [*_pylon_main(), *refineries, *scvs],
            [*_pylon_main(), *mined_out, *scvs],
            assigned=(),
            collected=others,
            started=release_in_the_second_turn,
        )
        assert released == [[30]]

    def test_release_collectors_releases_one_the_bot_ordered_first_and_counts_thirds_among_those_kept(self) -> None:
        """Three collect from `_REFINERY`, the nearer the townhall, handed over as 20, 21 and 22, and two from
        `_OTHER_REFINERY`, as 30 and 31. The bot sends 20 to build: it leaves first, and 22, no third once 20 is let
        go, leaves after the farther's."""
        others = [(20, _REFINERY), (21, _REFINERY), (22, _REFINERY), (30, _OTHER_REFINERY), (31, _OTHER_REFINERY)]
        scvs = [_reported_scv(_FAR_FROM_THE_GAS_TRIP_ENDS, tag=tag) for tag, _ in others]
        read: list[tuple[list[int], list[int]]] = []

        def build_then_release(api: Api[SpeedMining]) -> Units[OwnUnit[Any]]:
            _build_a_supply_depot(api, _own_unit_of_tag(api, 20))
            return api.extensions.release_collectors(2)

        units = [*_pylon_main(), _refinery(), _refinery(_OTHER_REFINERY), *scvs]
        _orders_sent(units, assigned=(), collected=others, started=_released_and_kept(build_then_release, read))
        assert read == [([20, 31], [21, 22, 30])]

    def test_release_miners_releases_one_the_bot_ordered_first(self) -> None:
        """Miners handed over as 20 at near field 102 and 21 at far field 107: the bot sends 20 to build, and it leaves
        first."""
        assigned = [(20, 102), (21, 107)]
        scvs = [_reported_scv((25.0, 30.0), tag=tag) for tag, _ in assigned]
        read: list[tuple[list[int], list[int]]] = []

        def build_then_release(api: Api[SpeedMining]) -> Units[OwnUnit[Any]]:
            _build_a_supply_depot(api, _own_unit_of_tag(api, 20))
            return api.extensions.release_miners(1)

        _orders_sent([*_pylon_main(), *scvs], assigned=assigned, started=_released_and_kept(build_then_release, read))
        assert read == [([20], [21])]

    @pytest.mark.parametrize(
        ("count", "released"),
        [(1, [23]), (2, [23, 21]), (3, [23, 21, 22]), (5, [23, 21, 22, 20])],
        ids=["the last at a far field", "the other at a far field", "then the last at a near field", "all"],
    )
    def test_release_miners_releases_those_at_the_farther_half_of_the_fields_first(
        self, count: int, released: list[int]
    ) -> None:
        """Miners handed over as 20 at near field 102, 21 at far field 107, 22 at near field 104 and 23 at far field
        108, the main's fields nearest its townhall's center being 101 to 104."""
        assigned = [(20, 102), (21, 107), (22, 104), (23, 108)]
        scvs = [_reported_scv((25.0, 30.0), tag=tag) for tag, _ in assigned]
        read: list[tuple[list[int], list[int]]] = []
        release = _released_and_kept(lambda api: api.extensions.release_miners(count), read)
        _orders_sent([*_pylon_main(), *scvs], assigned=assigned, started=release)
        assert read == [(released, sorted(tag for tag, _ in assigned if tag not in released))]

    def test_a_count_below_zero_raises(self) -> None:
        refused: list[str] = []

        def release_fewer_than_none(api: Api[SpeedMining]) -> None:
            for release in (api.extensions.release_miners, api.extensions.release_collectors):
                with pytest.raises(ValueError) as raised:
                    release(-1)
                refused.append(str(raised.value))

        _orders_sent([*_pylon_main(), _reported_scv((25.0, 30.0))], started=release_fewer_than_none)
        assert refused == ["speed mining releases no fewer than none, not -1"] * 2

    def test_a_miner_handed_over_again_counts_as_handed_over_last(self) -> None:
        """20 and 21 mine `_MINED_FIELD`, handed over in that order; 20, handed over to another field and back, comes
        after 21, and is the first `release_miners` releases."""
        read: list[tuple[list[int], list[int]]] = []

        def hand_over_again(api: Api[SpeedMining]) -> None:
            worker = _own_unit_of_tag(api, 20)
            api.extensions.mine(worker, _unit_of_tag(api, _OTHER_FIELD))
            api.extensions.mine(worker, _unit_of_tag(api, _MINED_FIELD))
            at_the_field = api.extensions.workers_at(_unit_of_tag(api, _MINED_FIELD))
            released = api.extensions.release_miners(1)
            read.append(([worker.tag for worker in at_the_field], [worker.tag for worker in released]))

        scvs = [_reported_scv((25.0, 30.0), tag=tag) for tag in (20, 21)]
        _orders_sent(
            [*_pylon_main(), *scvs], assigned=[(20, _MINED_FIELD), (21, _MINED_FIELD)], started=hand_over_again
        )
        assert read == [([21, 20], [20])]

    def test_a_collector_handed_over_again_counts_as_handed_over_last(self) -> None:
        """20 and 21 collect from `_REFINERY`, handed over in that order; 20, handed over to `_OTHER_REFINERY` and back,
        comes after 21, and is the first `release_collectors` releases."""
        read: list[tuple[list[int], list[int]]] = []

        def hand_over_again(api: Api[SpeedMining]) -> None:
            worker = _own_unit_of_tag(api, 20)
            api.extensions.collect(worker, _unit_of_tag(api, _OTHER_REFINERY))
            api.extensions.collect(worker, _unit_of_tag(api, _REFINERY))
            at_the_refinery = api.extensions.workers_at(_unit_of_tag(api, _REFINERY))
            released = api.extensions.release_collectors(1)
            read.append(([worker.tag for worker in at_the_refinery], [worker.tag for worker in released]))

        scvs = [_reported_scv(_FAR_FROM_THE_GAS_TRIP_ENDS, tag=tag) for tag in (20, 21)]
        units = [*_pylon_main(), _refinery(), _refinery(_OTHER_REFINERY), *scvs]
        collected = [(20, _REFINERY), (21, _REFINERY)]
        _orders_sent(units, assigned=(), collected=collected, started=hand_over_again)
        assert read == [([21, 20], [20])]


def _workers_on_the_main(api: Api[object]) -> list[tuple[OwnUnit[Any], Unit[Any]]]:
    """This player's workers, each with a field of its main to mine: one at each field, nearest first, then a second at
    the nearest, as speed mining's own picks have it."""
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
        for worker in speed_mined.units.own.workers:
            speed_mined.extensions.mine(worker)

    for api in (sent_once, speed_mined):
        opponent = Computer(Race.ZERG, Difficulty.VERY_EASY)
        run_local("PylonAIE_v4", api, opponent, race=Race.TERRAN, time_limit=GameTime(seconds=120), random_seed=1)

    assert speed_mined.score.collected.minerals > 1.05 * sent_once.score.collected.minerals


# How long the in-game test of collecting gives the main's refineries to be built and their builders to come out, from
# the start, then the collectors to settle, and then measures for.
_TIME_TO_BUILD_THE_REFINERIES = GameTime(seconds=90)
_TIME_TO_SETTLE = GameTime(seconds=20)
_TIME_MEASURED = GameTime(seconds=120)


def _vespene_collected_on_pylons_main(*, speed_collected: bool) -> float:
    """Play a game on Pylon, its SCVs speed mining its main until two of them have built a refinery on each of its
    geysers, then two SCVs collecting at each, the builder and the miner nearest, handed to `collect` if
    `speed_collected`, else sent to gather once, and return the vespene collected over the time measured."""
    api = Api(extensions=SpeedMining)
    builder_by_geyser: dict[Point, OwnUnit[Any]] = {}
    handed_over_at: list[GameTime] = []
    vespene_at_the_start: list[float] = []

    @api.on(GameStartEvent)
    def mine(event: GameStartEvent) -> None:
        for worker in api.units.own.workers:
            api.extensions.mine(worker)

    @api.on(TurnEvent)
    def build_then_collect(event: TurnEvent) -> None:
        if handed_over_at:
            if not vespene_at_the_start and api.time >= handed_over_at[0] + _TIME_TO_SETTLE:
                vespene_at_the_start.append(api.score.collected.vespene)
            return
        main = api.expansions[0]
        refinery_by_geyser = {refinery.position: refinery for refinery in api.units.own.gas_buildings}
        finished = [
            refinery_by_geyser[geyser.position]
            for geyser in main.geysers
            if geyser.position in refinery_by_geyser
            and refinery_by_geyser[geyser.position].is_complete
            and builder_by_geyser[geyser.position].is_orderable
            and not builder_by_geyser[geyser.position].is_constructing
        ]
        if len(finished) == len(main.geysers):
            for refinery in finished:
                builder = builder_by_geyser[refinery.position]
                miner = min(
                    (scv for scv in api.extensions.miners if scv.is_orderable),
                    key=lambda scv: scv.distance_to(refinery),
                )
                for scv in (builder, miner):
                    if speed_collected:
                        api.extensions.collect(scv, refinery)
                    else:
                        api.extensions.release(scv)
                        if scv.is_carrying_resource:
                            api.orders.issue(scv, AbilityId.RETURN, force=True)
                            api.orders.issue(scv, AbilityId.GATHER, target=refinery, queued=True)
                        else:
                            api.orders.issue(scv, AbilityId.GATHER, target=refinery)
            handed_over_at.append(api.time)
            return
        minerals = api.resources.minerals
        cost = api.data.units[UnitTypeId.REFINERY].cost.minerals
        for geyser in main.geysers:
            builder = builder_by_geyser.get(geyser.position)
            if (
                geyser.position in refinery_by_geyser
                or (builder is not None and builder.is_alive and builder.orders)
                or minerals < cost
            ):
                continue
            builder = min(
                (scv for scv in api.extensions.miners if scv.is_orderable), key=lambda scv: scv.distance_to(geyser)
            )
            api.extensions.release(builder)
            api.orders.issue(builder, AbilityId.SCV_BUILD_REFINERY, target=geyser)
            builder_by_geyser[geyser.position] = builder
            minerals -= cost

    opponent = Computer(Race.ZERG, Difficulty.VERY_EASY)
    time_limit = _TIME_TO_BUILD_THE_REFINERIES + _TIME_TO_SETTLE + _TIME_MEASURED
    run_local("PylonAIE_v4", api, opponent, race=Race.TERRAN, time_limit=time_limit, random_seed=1)
    assert vespene_at_the_start, "the refineries were not built in time"
    return api.score.collected.vespene - vespene_at_the_start[0]


@pytest.mark.integration
def test_in_a_real_game_two_scvs_a_refinery_speed_collected_collect_more_than_two_sent_to_gather_once() -> None:
    """Run with `pytest -m integration`. Plays two games on Pylon, two SCVs collecting at each refinery of its main for
    two minutes, once they are built: speed collected, they collected 9 to 10% more in
    `tools/sweep_gas.py`'s games."""
    try:
        MapFile.find("PylonAIE_v4")
    except MapNotFoundError as missing:
        pytest.skip(str(missing))
    sent_once = _vespene_collected_on_pylons_main(speed_collected=False)
    speed_collected = _vespene_collected_on_pylons_main(speed_collected=True)
    assert speed_collected > 1.05 * sent_once
