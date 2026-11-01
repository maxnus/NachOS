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
from sc2nachos.ids.raw import RawAbilityId
from sc2nachos.launch import MapFile, MapNotFoundError
from sc2nachos.match import Computer, Difficulty, Race
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
    started: Callable[[Api[SpeedMining]], object] = lambda api: None,
    dead: Callable[[int], Sequence[int]] = lambda turn: (),
    attack_enemies: bool = True,
    map_width: int = 48,
) -> list[list[tuple[int, Point | int | None, bool]]]:
    """Play a turn on each of `turns`, the units each observation lists, speed mining each worker of `assigned` at its
    field, its workers attacking enemies if `attack_enemies`, and return what each turn ordered: each command's
    ability, its target, a point or a tag, and whether it was queued. `started` runs as the game starts, after the
    workers are handed over, and `dead` gives the tags each observation reports dead. The map is `map_width` wide and
    48 high, of open ground."""
    observations = [make_observation(step, units=units, dead=dead(step)) for step, units in enumerate([*turns, ()])]
    game = ScriptedGame(observations, game_info=make_game_info(*["#" * map_width] * 48), data=shipped_game_data())
    api = Api(extensions=lambda api: SpeedMining(api, attack_enemies=attack_enemies))

    @api.on(GameStartEvent)
    def hand_over(event: GameStartEvent) -> None:
        for worker, field in assigned:
            api.extensions.mine(_own_unit_of_tag(api, worker), _unit_of_tag(api, field))
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

    def test_a_worker_with_cargo_but_no_return_returns_it(self) -> None:
        assert _orders_sent([*_pylon_main(), _reported_scv((25.0, 30.0), _gather_order(), carrying=True)]) == [
            [(AbilityId.RETURN, None, False)]
        ]

    def test_a_worker_the_game_sent_to_another_field_is_sent_back(self) -> None:
        bounced = [*_pylon_main(), _reported_scv((25.0, 30.0), _gather_order(_OTHER_FIELD))]
        assert _orders_sent(bounced) == [[(AbilityId.GATHER, _MINED_FIELD, False)]]

    def test_a_worker_on_its_way_to_a_field_it_was_handed_before_is_sent_to_its_own(self) -> None:
        """Speed mining's move toward `_OTHER_FIELD`, at (24.0, 18.5), ends 1.325 straight toward the townhall."""
        other_gather_point = Point((24.0, 18.5)).towards(_TOWNHALL_CENTER, 1.325).rounded_down(step=1 / 4096)
        on_its_way = _reported_scv((24.5, 22.0), _move_order(other_gather_point), _gather_order(_OTHER_FIELD))
        assert _orders_sent([*_pylon_main(), on_its_way]) == [[(AbilityId.GATHER, _MINED_FIELD, False)]]


class TestWhatIsLeftAlone:
    def test_a_worker_doing_anything_else_until_it_is_idle(self) -> None:
        building = raw_pb2.UnitOrder(ability_id=RawAbilityId.TerranBuild_SupplyDepot)
        building.target_world_space_pos.x, building.target_world_space_pos.y = 30.0, 34.0
        turns = [[*_pylon_main(), _reported_scv((28.0, 30.0), building)], [*_pylon_main(), _reported_scv((30.0, 33.0))]]
        assert _orders_sent(*turns) == [[], [(AbilityId.GATHER, _MINED_FIELD, False)]]

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
        drone = make_unit(_HANDED_SCV, UnitTypeId.DRONE_BURROWED, at=(25.0, 30.0), build_progress=1.0, is_burrowed=True)
        assert _orders_sent([*_pylon_main(), drone]) == [[]]

    def test_a_released_worker(self) -> None:
        def release(api: Api[SpeedMining]) -> None:
            worker = _own_unit_of_tag(api, _HANDED_SCV)
            api.extensions.release(worker)

        assert _orders_sent([*_pylon_main(), _reported_scv((25.0, 30.0))], started=release) == [[]]

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


class TestPlainMining:
    def test_without_a_finished_townhall_of_this_players_at_the_center(self) -> None:
        idle_with_cargo = _reported_scv((25.0, 30.0), carrying=True, tag=11)
        units = [
            *_pylon_main(command_center_progress=0.5),
            _reported_scv((28.175, 24.5), _gather_order()),
            idle_with_cargo,
        ]
        assert _orders_sent(units, assigned=[(_HANDED_SCV, _MINED_FIELD), (11, _MINED_FIELD)]) == [
            [(AbilityId.RETURN, None, False)]
        ]


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

    def test_a_worker_speed_mining_holds_is_out_of_its_way(self) -> None:
        """It mines `_OTHER_FIELD`, too far from where its trip ends to be given anything."""
        near = _reported_scv((28.175, 24.5), _gather_order())
        held = _reported_scv(_IN_THE_WAY, _gather_order(_OTHER_FIELD), tag=20)
        both_held = [(_HANDED_SCV, _MINED_FIELD), (20, _OTHER_FIELD)]
        assert _orders_sent([*_pylon_main(), near, held], assigned=both_held) == [
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
        attack = raw_pb2.UnitOrder(ability_id=RawAbilityId.attack_Attack, target_unit_tag=30)
        attacking = _reported_scv((26.0, 30.0), attack, facing=0.0, weapon_cooldown=20.0)
        zergling = _zergling_beside((26.0, 30.0), degrees_off_its_facing=0.0)
        assert _orders_sent([*_pylon_main(), attacking, zergling]) == [[]]


class TestHandingOver:
    def test_mine_refuses_a_unit_that_is_not_a_worker_or_a_field_that_is_not_minerals(self) -> None:
        refused: list[str] = []

        def try_others(api: Api[SpeedMining]) -> None:
            marine = _own_unit_of_tag(api, 20)
            scv = _own_unit_of_tag(api, _HANDED_SCV)
            for worker, field in [(marine, _unit_of_tag(api, _MINED_FIELD)), (scv, _unit_of_tag(api, 111))]:
                with pytest.raises(ValueError) as raised:
                    api.extensions.mine(worker, field)
                refused.append(str(raised.value).split(",")[0])

        marine = make_unit(20, UnitTypeId.MARINE, at=(10.0, 10.0), build_progress=1.0)
        _orders_sent([*_pylon_main(), _reported_scv((25.0, 30.0)), marine], assigned=(), started=try_others)
        assert refused == ["speed mining orders workers", "speed mining mines mineral fields"]

    def test_mine_refuses_a_third_worker_at_a_field(self) -> None:
        refused: list[str] = []

        def hand_over_a_third(api: Api[SpeedMining]) -> None:
            with pytest.raises(ValueError) as raised:
                api.extensions.mine(_own_unit_of_tag(api, 12), _unit_of_tag(api, _MINED_FIELD))
            refused.append(str(raised.value).split(",")[0])
            api.extensions.mine(_own_unit_of_tag(api, 11), _unit_of_tag(api, _MINED_FIELD))

        scvs = [_reported_scv((25.0, 30.0), _gather_order(), tag=tag) for tag in (_HANDED_SCV, 11, 12)]
        two_at_the_field = [(_HANDED_SCV, _MINED_FIELD), (11, _MINED_FIELD)]
        _orders_sent([*_pylon_main(), *scvs], assigned=two_at_the_field, started=hand_over_a_third)
        assert refused == ["speed mining mines a field with 2 workers at most"]

    def test_miners_reads_each_worker_held_with_its_field_and_no_dead_one(self) -> None:
        read: list[tuple[bool, int | None, int, list[int]]] = []

        def read_the_miners(api: Api[SpeedMining]) -> None:
            def read_them(event: TurnEvent) -> None:
                miners = api.extensions.miners
                scv = _own_unit_of_tag(api, 11) if event.time.steps == 0 else None
                field = None if scv is None else miners.get(scv)
                at_the_other_field = api.extensions.miners_at(_unit_of_tag(api, _OTHER_FIELD))
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
        picked_fields: list[int] = []

        def hand_over_with_no_field(api: Api[SpeedMining]) -> None:
            picked_fields.append(api.extensions.mine(_own_unit_of_tag(api, _HANDED_SCV)).tag)

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
        picked_fields: list[int] = []

        def hand_over_with_no_field(api: Api[SpeedMining]) -> None:
            picked_fields.append(api.extensions.mine(_own_unit_of_tag(api, _HANDED_SCV)).tag)

        bases = [*_pylon_main(), *_pylon_main(at=(56.5, 24.5), tags_from=200)]
        newcomer = _reported_scv((45.0, 28.0))
        _orders_sent([*bases, newcomer, *scvs], assigned=others, started=hand_over_with_no_field, map_width=80)
        assert picked_fields == [picked]

    def test_mine_with_no_field_raises_with_no_finished_townhall_of_this_players(self) -> None:
        refused: list[str] = []

        def hand_over_with_no_field(api: Api[SpeedMining]) -> None:
            with pytest.raises(ValueError) as raised:
                api.extensions.mine(_own_unit_of_tag(api, _HANDED_SCV))
            refused.append(str(raised.value).split(":")[0])

        units = [*_pylon_main(command_center_progress=0.5), _reported_scv((31.0, 28.0))]
        _orders_sent(units, assigned=(), started=hand_over_with_no_field)
        assert len(refused) == 1
        assert refused[0].startswith("speed mining has no field for")

    def test_built_before_the_game_starts_it_raises(self) -> None:
        with pytest.raises(NotPlayingError):
            SpeedMining(Api())


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
