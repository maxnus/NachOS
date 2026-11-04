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
from sc2nachos.units import Alliance, OwnUnit, Unit
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
# This player's refineries on the main's first geyser, at (31.5, 27.5), and its second, at (20.5, 17.5).
_REFINERY = 121
_OTHER_REFINERY = 122


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
    `vespene_contents` left and the rest of what `fields` report."""
    dx, dy = _GEYSER_OFFSETS[tag % 10 - 1]
    fields = {"build_progress": 1.0, **fields}
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


class TestWhatIsLeftAlone:
    def test_a_worker_doing_anything_else_until_it_is_idle(self) -> None:
        """Sent to build by the bot after it was handed over, it is sent nothing until it is idle."""
        turns = [
            [*_pylon_main(), _reported_scv((28.0, 30.0), _gather_order())],
            [*_pylon_main(), _reported_scv((28.0, 30.0), _build_order())],
            [*_pylon_main(), _reported_scv((30.0, 33.0))],
        ]
        assert _orders_sent(*turns) == [[], [], [(AbilityId.GATHER, _MINED_FIELD, False)]]

    def test_a_worker_given_an_order_this_turn_or_sent_one_that_may_not_show_yet(self) -> None:
        def order_away(api: Api[SpeedMining]) -> None:
            @api.on(TurnEvent, priority=EventPriority.HIGH, once=True)
            def move_away(event: TurnEvent) -> None:
                worker = _own_unit_of_tag(api, _HANDED_SCV)
                api.orders.issue(worker, AbilityId.MOVE, target=(20.0, 30.0))

        near = [*_pylon_main(), _reported_scv((28.175, 24.5), _gather_order())]
        moved = [(AbilityId.MOVE, Point((20.0, 30.0)), False)]
        assert _orders_sent(near, near, near, started=order_away) == [
            moved,
            [],
            _move_then(AbilityId.GATHER, _GATHER_POINT, _MINED_FIELD),
        ]

    def test_a_burrowed_drone(self) -> None:
        """Handed over idle, it is sent to gather, and burrowed in the next turn, it is sent nothing."""
        drone = make_unit(_HANDED_SCV, UnitTypeId.DRONE, at=(25.0, 30.0), build_progress=1.0)
        burrowed = make_unit(
            _HANDED_SCV, UnitTypeId.DRONE_BURROWED, at=(25.0, 30.0), build_progress=1.0, is_burrowed=True
        )
        assert _orders_sent([*_pylon_main(), drone], [*_pylon_main(), burrowed]) == [
            [(AbilityId.GATHER, _MINED_FIELD, False)],
            [],
        ]

    @pytest.mark.parametrize(
        "collecting",
        [_reported_scv((25.0, 30.0), _gather_order(111)), _scv_carrying_vespene((29.125, 24.5), _return_order())],
        ids=["on its way to the geyser", "on its way back, where a worker returning minerals is given the move"],
    )
    def test_a_worker_collecting_vespene(self, collecting: raw_pb2.Unit) -> None:
        """Sent to collect vespene by the bot after it was handed over."""
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

    def test_a_worker_speed_mining_holds_is_out_of_its_way(self) -> None:
        """It mines `_OTHER_FIELD`, too far from where its trip ends to be given anything."""
        near = _reported_scv((28.175, 24.5), _gather_order())
        held = _reported_scv(_IN_THE_WAY, _gather_order(_OTHER_FIELD), tag=20)
        both_held = [(_HANDED_SCV, _MINED_FIELD), (20, _OTHER_FIELD)]
        assert _orders_sent([*_pylon_main(), near, held], assigned=both_held) == [
            _move_then(AbilityId.GATHER, _GATHER_POINT, _MINED_FIELD)
        ]

    def test_a_worker_speed_mining_holds_to_collect_is_out_of_its_way(self) -> None:
        near = _reported_scv((28.175, 24.5), _gather_order())
        collector = _reported_scv(_IN_THE_WAY, _gather_order(_REFINERY), tag=20)
        assert _orders_sent([*_pylon_main(), _refinery(), near, collector], collected=[(20, _REFINERY)]) == [
            _move_then(AbilityId.GATHER, _GATHER_POINT, _MINED_FIELD)
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

    def test_a_bots_own_attack_is_left_alone(self) -> None:
        """Given after the worker was handed over, which sends a worker on an attack to its field."""
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
    """A worker handed over with `collect` is kept collecting vespene from its gas building, on plain gathers and
    returns."""

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
            _reported_scv((28.5, 26.0), _gather_order(_REFINERY)),
            _scv_carrying_vespene((28.5, 26.0), _return_order()),
            _reported_scv((28.5, 26.0), _return_order()),
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
        collecting = [*_pylon_main(), _refinery(), _reported_scv((28.5, 26.0), _gather_order(_REFINERY))]
        idle_with_vespene = [*_pylon_main(), _refinery(), _scv_carrying_vespene((28.5, 26.0))]
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

    def test_a_collector_the_bot_sends_to_mine_or_build_is_left_alone_until_it_is_idle(self) -> None:
        turns = [
            [*_pylon_main(), _refinery(), _reported_scv((28.5, 26.0), order)]
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
            [*_pylon_main(), _refinery(), _reported_scv((28.5, 26.0), _gather_order(_REFINERY))],
            [*second_turn, _scv_carrying_vespene((28.5, 26.0))],
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
        ("collectors_at_the_nearer", "picked"),
        [(0, _REFINERY), (3, _OTHER_REFINERY)],
        ids=["the nearest", "the other once the nearest is full"],
    )
    def test_collect_with_no_gas_building_picks_the_nearest_with_room(
        self, collectors_at_the_nearer: int, picked: int
    ) -> None:
        """An SCV at (30.0, 26.0), beside `_REFINERY`."""
        others = [(20 + index, _REFINERY) for index in range(collectors_at_the_nearer)]
        scvs = [_reported_scv((30.0, 27.0), _gather_order(_REFINERY), tag=tag) for tag, _ in others]
        picked_gas_buildings: list[int | None] = []

        def hand_over_with_no_gas_building(api: Api[SpeedMining]) -> None:
            gas_building = api.extensions.collect(_own_unit_of_tag(api, _HANDED_SCV))
            picked_gas_buildings.append(None if gas_building is None else gas_building.tag)

        units = [*_pylon_main(), _refinery(), _refinery(_OTHER_REFINERY), _reported_scv((30.0, 26.0)), *scvs]
        _orders_sent(units, assigned=(), collected=others, started=hand_over_with_no_gas_building)
        assert picked_gas_buildings == [picked]

    @pytest.mark.parametrize(
        ("collectors_at_the_nearer_base", "picked"),
        [(0, 222), (6, _REFINERY)],
        ids=["the nearer base", "the other once it is full"],
    )
    def test_collect_with_no_gas_building_picks_the_base_nearest_the_worker_with_room(
        self, collectors_at_the_nearer_base: int, picked: int
    ) -> None:
        """A second base, its townhall at (56.5, 24.5), its refineries tagged 221 and 222; an SCV at (42.0, 28.0),
        nearer it, though nearer `_REFINERY` than either of them."""
        others = [(20 + index, 221 + index // 3) for index in range(collectors_at_the_nearer_base)]
        scvs = [_reported_scv((60.0, 27.0), tag=tag) for tag, _ in others]
        picked_gas_buildings: list[int | None] = []

        def hand_over_with_no_gas_building(api: Api[SpeedMining]) -> None:
            gas_building = api.extensions.collect(_own_unit_of_tag(api, _HANDED_SCV))
            picked_gas_buildings.append(None if gas_building is None else gas_building.tag)

        second_base = [
            *_pylon_main(at=(56.5, 24.5), tags_from=200),
            *(_refinery(tag, at=(56.5, 24.5)) for tag in (221, 222)),
        ]
        units = [*_pylon_main(), _refinery(), *second_base, _reported_scv((42.0, 28.0)), *scvs]
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
