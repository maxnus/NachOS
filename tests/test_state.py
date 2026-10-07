"""The state beyond the units: the score, the counters, the map's current state, and what happened."""

from contextlib import closing
from itertools import count
from typing import Any

import pytest
from s2clientprotocol import common_pb2, data_pb2, debug_pb2, raw_pb2, sc2api_pb2, score_pb2

from sc2nachos import Api, NotPlayingError
from sc2nachos.enemy import Enemy
from sc2nachos.events import ChatEvent
from sc2nachos.gamedata import GameData, Resources
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, EffectId, UncuratedIdError, UnitTypeId, UpgradeId
from sc2nachos.ids.raw import RawAbilityId, RawEffectId, RawUpgradeId
from sc2nachos.launch import GameProcess, MapFile, MapNotFoundError
from sc2nachos.match import Computer, Difficulty, Participant, Race, Result
from sc2nachos.protocol import Client, ProtocolError, Status, WebSocketTransport
from sc2nachos.state import (
    AutocastToggle,
    CameraMove,
    CategoryScore,
    Effect,
    Supply,
    UiUnitCounts,
    UnitCommand,
    ValueScore,
    VitalScore,
)
from sc2nachos.state._state import _State
from sc2nachos.units import Alliance, Unit
from sc2nachos.units._tracking import _Tracker
from support import (
    FakeTransport,
    RealGame,
    make_bits,
    make_bytes,
    make_game_info,
    make_observation,
    make_response,
    make_tables,
    make_unit,
    record,
)

_TABLES = make_tables(
    data_pb2.UnitTypeData(unit_id=UnitTypeId.ZERGLING, food_required=0.5),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.BANELING, food_required=0.5),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.ROACH, food_required=2),
)
_ENEMY = Alliance.ENEMY


class _Game:
    """A game on an eight by eight map, taking in observations one at a time."""

    def __init__(self, game_info: sc2api_pb2.ResponseGameInfo | None = None, tables: GameData = _TABLES) -> None:
        self.tracker = _Tracker(tables, Enemy())
        self.map = GameMap(game_info or make_game_info(), start_location=Point((0.5, 0.5)))

    def observe(self, step: int = 0, **fields: Any) -> _State:
        observation = make_observation(step, **fields)
        self.tracker.update(observation.observation.raw_data, step)
        return _State(observation, self.tracker, self.map)


def _row(unit_type: UnitTypeId, *, structure: bool = False, minerals: int = 0) -> data_pb2.UnitTypeData:
    """A unit type's row, a structure or not, at its price in minerals."""
    attribute = data_pb2.Attribute.Structure if structure else data_pb2.Attribute.Biological
    return data_pb2.UnitTypeData(unit_id=unit_type, attributes=[attribute], mineral_cost=minerals)


_MAKING_TABLES = make_tables(
    _row(UnitTypeId.MARINE, minerals=50),
    _row(UnitTypeId.BARRACKS, structure=True, minerals=150),
    *(
        _row(reactor, structure=True, minerals=50)
        for reactor in (UnitTypeId.REACTOR_BARRACKS, UnitTypeId.REACTOR_FACTORY, UnitTypeId.REACTOR_STARPORT)
    ),
    _row(UnitTypeId.SCV, minerals=50),
    _row(UnitTypeId.SUPPLY_DEPOT, structure=True, minerals=100),
    _row(UnitTypeId.COMMAND_CENTER, structure=True, minerals=400),
    _row(UnitTypeId.ORBITAL_COMMAND, structure=True, minerals=550),
    _row(UnitTypeId.LARVA),
    _row(UnitTypeId.EGG),
    _row(UnitTypeId.DRONE, minerals=50),
    _row(UnitTypeId.ZERGLING, minerals=25),
    _row(UnitTypeId.BANELING_COCOON),
    _row(UnitTypeId.BANELING, minerals=50),
    _row(UnitTypeId.ZEALOT, minerals=100),
    _row(UnitTypeId.SIEGE_TANK, minerals=150),
    _row(UnitTypeId.SIEGE_TANK_SIEGED, minerals=150),
    _row(UnitTypeId.ENGINEERING_BAY, structure=True, minerals=125),
    abilities=[
        data_pb2.AbilityData(ability_id=AbilityId.BARRACKS_TRAIN_MARINE),
        data_pb2.AbilityData(ability_id=AbilityId.BUILD_REACTOR, is_building=True),
        data_pb2.AbilityData(
            ability_id=AbilityId.SCV_BUILD_SUPPLY_DEPOT, target=data_pb2.AbilityData.Target.Point, is_building=True
        ),
        data_pb2.AbilityData(ability_id=AbilityId.COMMAND_CENTER_MORPH_ORBITAL_COMMAND),
        data_pb2.AbilityData(ability_id=AbilityId.LARVA_MORPH_DRONE),
        data_pb2.AbilityData(ability_id=AbilityId.ZERGLING_MORPH_BANELING),
        data_pb2.AbilityData(ability_id=RawAbilityId.SiegeMode_SiegeMode),
        data_pb2.AbilityData(ability_id=AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_1),
    ],
    upgrades=[data_pb2.UpgradeData(upgrade_id=UpgradeId.TERRAN_INFANTRY_WEAPONS_1, mineral_cost=100, vespene_cost=100)],
)


def _done(tag: int, unit_type: UnitTypeId, *orders: raw_pb2.UnitOrder, **fields: Any) -> raw_pb2.Unit:
    """One of this player's finished units of `unit_type`, carrying out `orders`."""
    return make_unit(tag, unit_type, orders=orders, build_progress=1.0, **fields)


def _making(ability: int, progress: float = 0.0, at: tuple[float, float] | None = None) -> raw_pb2.UnitOrder:
    """An order a unit shows, `progress` along, aimed at the point `at` if given."""
    order = raw_pb2.UnitOrder(ability_id=ability, progress=progress)
    if at is not None:
        order.target_world_space_pos.x, order.target_world_space_pos.y = at
    return order


def _made(state: _State, unit_type: UnitTypeId) -> list[tuple[int, float | None]]:
    """The tag of the unit each `unit_type` in production is read from, and its progress."""
    return [(item.unit.tag, item.progress) for item in state.production.of_types({unit_type})]


class TestProduction:
    def test_each_train_a_structure_shows_is_one_at_its_progress(self) -> None:
        train = AbilityId.BARRACKS_TRAIN_MARINE
        state = _Game(tables=_MAKING_TABLES).observe(
            units=[
                _done(1, UnitTypeId.BARRACKS, _making(train, 0.5), _making(train, 0.25), add_on_tag=2),
                _done(2, UnitTypeId.REACTOR_BARRACKS),
            ]
        )
        assert _made(state, UnitTypeId.MARINE) == [(1, 0.5), (1, 0.25)]

    def test_an_egg_is_what_it_becomes_at_its_progress(self) -> None:
        state = _Game(tables=_MAKING_TABLES).observe(
            units=[_done(1, UnitTypeId.EGG, _making(AbilityId.LARVA_MORPH_DRONE, 0.375))]
        )
        assert _made(state, UnitTypeId.DRONE) == [(1, 0.375)]
        assert _made(state, UnitTypeId.EGG) == []

    def test_a_worker_on_its_way_to_build_is_one_until_the_structure_stands_which_then_is(self) -> None:
        build = _making(AbilityId.SCV_BUILD_SUPPLY_DEPOT, at=(20.0, 20.0))
        game = _Game(tables=_MAKING_TABLES)
        walking = game.observe(units=[_done(1, UnitTypeId.SCV, build, at=(10.0, 10.0))])
        assert _made(walking, UnitTypeId.SUPPLY_DEPOT) == [(1, 0.0)]
        depot = make_unit(2, UnitTypeId.SUPPLY_DEPOT, at=(20.0, 20.0), build_progress=0.25)
        building = game.observe(16, units=[_done(1, UnitTypeId.SCV, build, at=(19.0, 20.0)), depot])
        assert _made(building, UnitTypeId.SUPPLY_DEPOT) == [(2, 0.25)]

    def test_an_add_on_is_one_by_its_order_until_it_stands_which_then_is(self) -> None:
        game = _Game(tables=_MAKING_TABLES)
        ordered = game.observe(units=[_done(1, UnitTypeId.BARRACKS, _making(AbilityId.BUILD_REACTOR))])
        assert _made(ordered, UnitTypeId.REACTOR_BARRACKS) == [(1, 0.0)]
        going_up = game.observe(
            16,
            units=[
                _done(1, UnitTypeId.BARRACKS, _making(AbilityId.BUILD_REACTOR), add_on_tag=2),
                make_unit(2, UnitTypeId.REACTOR_BARRACKS, build_progress=0.125),
            ],
        )
        assert _made(going_up, UnitTypeId.REACTOR_BARRACKS) == [(2, 0.125)]

    def test_a_structure_morphing_is_what_it_becomes_at_no_known_progress(self) -> None:
        state = _Game(tables=_MAKING_TABLES).observe(
            units=[_done(1, UnitTypeId.COMMAND_CENTER, _making(AbilityId.COMMAND_CENTER_MORPH_ORBITAL_COMMAND))]
        )
        assert _made(state, UnitTypeId.ORBITAL_COMMAND) == [(1, None)]

    def test_a_cocoon_is_what_it_becomes_at_no_known_progress(self) -> None:
        state = _Game(tables=_MAKING_TABLES).observe(
            units=[_done(1, UnitTypeId.BANELING_COCOON, _making(AbilityId.ZERGLING_MORPH_BANELING))]
        )
        assert _made(state, UnitTypeId.BANELING) == [(1, None)]

    def test_a_unit_warping_in_is_one_at_its_progress(self) -> None:
        state = _Game(tables=_MAKING_TABLES).observe(units=[make_unit(1, UnitTypeId.ZEALOT, build_progress=0.5)])
        assert _made(state, UnitTypeId.ZEALOT) == [(1, 0.5)]

    def test_a_free_order_that_makes_a_form_is_none(self) -> None:
        siege = _making(RawAbilityId.SiegeMode_SiegeMode)
        state = _Game(tables=_MAKING_TABLES).observe(units=[_done(1, UnitTypeId.SIEGE_TANK_SIEGED, siege)])
        assert _made(state, UnitTypeId.SIEGE_TANK_SIEGED) == []

    def test_an_enemys_production_is_none(self) -> None:
        enemy = make_unit(1, UnitTypeId.ZEALOT, build_progress=0.5, alliance=_ENEMY)
        state = _Game(tables=_MAKING_TABLES).observe(units=[enemy])
        assert _made(state, UnitTypeId.ZEALOT) == []

    def test_a_research_reads_its_progress_and_nothing_else_does(self) -> None:
        research = _making(AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_1, 0.25)
        state = _Game(tables=_MAKING_TABLES).observe(units=[_done(1, UnitTypeId.ENGINEERING_BAY, research)])
        assert state.production.progress(UpgradeId.TERRAN_INFANTRY_WEAPONS_1) == 0.25
        assert state.production.progress(UpgradeId.TERRAN_INFANTRY_WEAPONS_2) is None


class TestScore:
    def test_every_amount_is_read_as_the_game_reports_it(self) -> None:
        values = count(1)
        details = score_pb2.ScoreDetails()
        for field in score_pb2.ScoreDetails.DESCRIPTOR.fields:
            if field.message_type is None:
                setattr(details, field.name, next(values))
            else:
                for part in field.message_type.fields:
                    setattr(getattr(details, field.name), part.name, next(values))
        score = _Game().observe(score=score_pb2.Score(score=12345, score_details=details)).score

        assert score.score == 12345
        assert (score.total_value, score.killed_value) == (
            ValueScore(details.total_value_units, details.total_value_structures),
            ValueScore(details.killed_value_units, details.killed_value_structures),
        )
        assert (score.collected, score.collection_rate, score.spent) == (
            Resources(details.collected_minerals, details.collected_vespene),
            Resources(details.collection_rate_minerals, details.collection_rate_vespene),
            Resources(details.spent_minerals, details.spent_vespene),
        )
        # The scalar fields are all read above or in the test below, except the APM.
        singles = {field.name for field in score_pb2.ScoreDetails.DESCRIPTOR.fields if field.message_type is None}
        assert len(singles) == 14
        for field in score_pb2.ScoreDetails.DESCRIPTOR.fields:
            if field.message_type is not None:
                expected = getattr(details, field.name)
                parts = tuple(getattr(expected, part.name) for part in field.message_type.fields)
                expected = CategoryScore(*parts) if len(parts) == 5 else VitalScore(*parts)
                assert getattr(score, field.name) == expected, field.name

    def test_the_idle_times_are_in_steps(self) -> None:
        """Measured in game: two idle drones add 20 to the idle worker time over 160 steps."""
        details = score_pb2.ScoreDetails(idle_production_time=10.0, idle_worker_time=20.0)
        score = _Game().observe(score=score_pb2.Score(score_details=details)).score
        assert (score.idle_production_steps, score.idle_worker_steps) == (160, 320)

    def test_a_split_amount_totals_its_parts(self) -> None:
        assert CategoryScore(1.0, 2.0, 3.0, 4.0, 5.0).total == 15.0
        assert VitalScore(10.0, 20.0, 5.0).total == 35.0
        assert ValueScore(100.0, 400.0).total == 500.0


class TestCounters:
    def test_the_counters_are_the_players(self) -> None:
        common = sc2api_pb2.PlayerCommon(
            minerals=375,
            vespene=120,
            food_cap=46,
            food_used=31,
            food_army=12,
            food_workers=19,
            idle_worker_count=2,
            army_count=6,
            warp_gate_count=3,
        )
        state = _Game().observe(common=common)
        assert state.resources == Resources(375, 120)
        assert state.supply == Supply(used=31, cap=46, army=12, workers=19)
        assert state.ui_unit_counts == UiUnitCounts(idle_workers=2, army=6, warp_gates=3)

    def test_the_half_supply_the_game_rounds_away_is_added_back(self) -> None:
        """Measured in game: one zergling leaves the supply in use where it was, and a second adds one."""
        common = sc2api_pb2.PlayerCommon(food_used=16, food_army=4)
        units = [
            make_unit(1, UnitTypeId.ZERGLING),
            make_unit(2, UnitTypeId.ZERGLING),
            make_unit(3, UnitTypeId.BANELING),
            make_unit(4, UnitTypeId.ROACH),
            make_unit(5, UnitTypeId.ZERGLING, alliance=_ENEMY),
        ]
        state = _Game().observe(units=units, common=common)
        assert (state.supply.used, state.supply.army) == (16.5, 4.5)

    def test_the_half_supply_counts_the_units_first_seen_as_this_players_and_not_dead(self) -> None:
        game = _Game()
        game.observe(
            0,
            units=[
                make_unit(1, UnitTypeId.ZERGLING),
                make_unit(2, UnitTypeId.ZERGLING),
                make_unit(3, UnitTypeId.ZERGLING, alliance=_ENEMY),
            ],
        )
        # The first zergling goes into a transport, the second dies, and the enemy's is taken over.
        state = game.observe(
            16, units=[make_unit(3, UnitTypeId.ZERGLING)], dead=(2,), common=sc2api_pb2.PlayerCommon(food_used=10)
        )
        assert state.supply.used == 10.5

    def test_what_is_left_is_under_the_cap(self) -> None:
        assert Supply(used=14.5, cap=14, army=0.5, workers=14).left == -0.5


class TestTheMapAsItStands:
    _GAME_INFO = make_game_info(*("#" * 4,) * 4, playable=(1, 1, 3, 4))

    def test_vision_and_what_was_explored_cover_the_playable_area(self) -> None:
        visibility = make_bytes([0, 0, 0, 0], [0, 2, 1, 0], [0, 1, 0, 0], [0, 0, 2, 0])
        state = _Game(self._GAME_INFO).observe(visibility=visibility)
        for grid in (state.vision, state.explored):
            assert (grid.origin, grid.width, grid.height) == ((1, 1), 2, 3)
            assert grid.readonly
            assert grid[Point((0.5, 0.5))] is False
        # A row for each y from the bottom of the playable area up.
        assert state.vision.values.T.tolist() == [[False, False], [True, False], [False, False]]
        assert state.explored.values.T.tolist() == [[True, False], [True, True], [False, False]]

    def test_creep_is_read_from_an_image_of_a_bit_a_pixel(self) -> None:
        creep = make_bits("....", ".#..", "..#.", "....")
        grid = _Game(self._GAME_INFO).observe(creep=creep).creep
        assert grid.values.T.tolist() == [[False, True], [True, False], [False, False]]
        assert grid[Point((2.5, 1.5))] is True

    def test_upgrades_are_the_curated_ids(self) -> None:
        state = _Game().observe(upgrades=[UpgradeId.ZERG_MELEE_WEAPONS_1, UpgradeId.ZERG_GROUND_ARMOR_1])
        assert state.upgrades == {UpgradeId.ZERG_MELEE_WEAPONS_1, UpgradeId.ZERG_GROUND_ARMOR_1}

    def test_an_uncurated_upgrade_raises_as_the_observation_is_taken_in(self) -> None:
        """An upgrade this player holds belongs among the curated ids, so a missing one is a mistake to fix, not to
        skip."""
        with pytest.raises(UncuratedIdError):
            _Game().observe(upgrades=[RawUpgradeId.CarrierLaunchSpeedUpgrade])

    def test_an_effect_is_where_it_is_and_whose(self) -> None:
        spines = raw_pb2.Effect(
            effect_id=EffectId.LURKER_SPINES,
            pos=[common_pb2.Point2D(x=10.0, y=10.0), common_pb2.Point2D(x=11.0, y=10.5)],
            alliance=_ENEMY.value,
            owner=2,
            radius=0.5,
        )
        (effect,) = _Game().observe(effects=[spines]).effects
        assert effect == Effect(EffectId.LURKER_SPINES, (Point((10.0, 10.0)), Point((11.0, 10.5))), 0.5, _ENEMY, 2)

    def test_an_uncurated_effect_raises_when_read(self) -> None:
        uncurated = next(raw for raw in RawEffectId if raw.value not in {member.value for member in EffectId})
        state = _Game().observe(effects=[raw_pb2.Effect(effect_id=uncurated)])
        with pytest.raises(UncuratedIdError):
            _ = state.effects


def _command(ability: int, *tags: int, queued: bool = False, **target: Any) -> sc2api_pb2.Action:
    command = raw_pb2.ActionRawUnitCommand(ability_id=ability, unit_tags=tags, queue_command=queued, **target)
    return sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(unit_command=command), game_loop=15)


class TestWhatHappened:
    def test_a_unit_command_names_its_units_and_its_target(self) -> None:
        game = _Game()
        game.observe(0, units=[make_unit(1), make_unit(2), make_unit(9, alliance=_ENEMY)])
        present = game.tracker.unit_tracker.present
        marine, other, enemy = present.by_id(100001), present.by_id(100002), present.by_id(400001)
        actions = [
            # The id a move runs as, which reads as the move.
            _command(RawAbilityId.Move_Move, 1, 2, target_world_space_pos=common_pb2.Point2D(x=5.0, y=6.0)),
            _command(AbilityId.ATTACK, 1, target_unit_tag=9),
            _command(AbilityId.STOP, 2, queued=True),
        ]
        state = game.observe(16, units=[make_unit(1), make_unit(2)], dead=(9,), actions=actions)
        assert state.actions == (
            UnitCommand(15, AbilityId.MOVE, (marine, other), Point((5.0, 6.0)), queued=False),
            UnitCommand(15, AbilityId.ATTACK, (marine,), enemy, queued=False),
            UnitCommand(15, AbilityId.STOP, (other,), None, queued=True),
        )
        assert enemy.is_dead

    def test_an_autocast_toggle_and_a_camera_move(self) -> None:
        game = _Game()
        game.observe(0, units=[make_unit(1, UnitTypeId.MEDIVAC)])
        medivac: Unit[Any] = game.tracker.unit_tracker.present[0]
        toggle = raw_pb2.ActionRawToggleAutocast(ability_id=AbilityId.MEDIVAC_HEAL, unit_tags=[1])
        camera = raw_pb2.ActionRawCameraMove(center_world_space=common_pb2.Point(x=30.75, y=139.0))
        actions = [
            sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(toggle_autocast=toggle), game_loop=3),
            sc2api_pb2.Action(action_raw=raw_pb2.ActionRaw(camera_move=camera), game_loop=2),
            sc2api_pb2.Action(action_chat=sc2api_pb2.ActionChat(message="not a raw action"), game_loop=4),
        ]
        state = game.observe(16, units=[make_unit(1, UnitTypeId.MEDIVAC)], actions=actions)
        assert state.actions == (
            AutocastToggle(3, AbilityId.MEDIVAC_HEAL, (medivac,)),
            CameraMove(2, Point((30.75, 139.0))),
        )


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

    _READS = (
        "score",
        "resources",
        "supply",
        "ui_unit_counts",
        "upgrades",
        "vision",
        "explored",
        "creep",
        "effects",
    )

    @pytest.mark.parametrize("read", _READS)
    def test_before_any_game_each_read_raises(self, read: str) -> None:
        with pytest.raises(NotPlayingError):
            getattr(Api(), read)

    def test_each_read_answers_from_the_last_observation(self) -> None:
        api = Api()
        self._play(
            api,
            make_observation(0, common=sc2api_pb2.PlayerCommon(minerals=50, food_used=12)),
            make_observation(16, (1, Result.VICTORY), common=sc2api_pb2.PlayerCommon(minerals=75, food_used=13)),
        )
        assert api.resources == Resources(75, 0)
        assert api.supply.used == 13

    def test_nothing_is_read_out_of_an_observation_until_it_is_asked_for(self) -> None:
        """An observation without map state plays, and the grid it lacks raises only when read."""
        api = Api()
        self._play(api, make_observation(0, (1, Result.VICTORY)))
        with pytest.raises(ProtocolError):
            _ = api.vision


@pytest.mark.integration
def test_in_a_real_game_the_state_is_what_was_done_and_seen() -> None:
    """Run with `pytest -m integration`. Plays half a minute of a game as zerg."""
    try:
        game_map = MapFile.find("PylonAIE_v4")
    except MapNotFoundError as missing:
        pytest.skip(str(missing))

    with (
        GameProcess.launch(window=(640, 480)) as process,
        closing(Client(WebSocketTransport.connect(process.url))) as client,
    ):
        client.create_game(game_map.path, [Participant(), Computer(Race.TERRAN, Difficulty.VERY_EASY)])
        game = RealGame(client, client.join_game(Race.ZERG))
        units = game.turn(1)
        home = units.own.of_type(UnitTypeId.HATCHERY)[0].position
        middle = game.map.playable_area.center
        out_there = home.towards(middle, 15)

        # The map's current state.
        assert game.state.creep[home] and game.state.vision[home] and game.state.explored[home]
        (opponent_start,) = game.map.opponent_start_locations
        assert not game.state.explored[opponent_start]

        # The half supply the game rounds away.
        used = game.state.supply.used
        game.debug(game.create(UnitTypeId.ZERGLING, out_there))
        game.turn(2)
        assert (game.state.supply.used, game.state.supply.army) == (used + 0.5, 0.5)
        zergling = game.newest(UnitTypeId.ZERGLING)

        # A unit command, as the game runs it.
        drone = units.own.of_type(UnitTypeId.DRONE)[0]
        game.order(AbilityId.MOVE, drone, target=out_there)
        game.turn(1)
        (command,) = game.state.actions
        assert isinstance(command, UnitCommand)
        assert (command.ability, command.units) == (AbilityId.MOVE, (drone,))
        # The game sends points in single precision.
        assert command.target == pytest.approx(out_there, abs=1e-3)

        # A message sent to the chat comes back, once.
        chat = sc2api_pb2.ActionChat(channel=sc2api_pb2.ActionChat.Broadcast, message="gl hf")
        client.act([sc2api_pb2.Action(action_chat=chat)])
        messages = record(game.events, ChatEvent)
        game.turn(1)
        game.turn(1)
        assert messages == [ChatEvent(game.player, "gl hf", step=messages[0].step)]

        # A unit that dies is among the dead units in one observation only.
        game.debug(game.kill(zergling))
        while not zergling.is_dead:
            game.turn(1)
        assert game.tracker.last_changes.units_died == [zergling]
        game.turn(1)
        assert not game.tracker.last_changes.units_died

        # An upgrade once researched. The `tech_tree` cheat would grant dozens of upgrades besides.
        game.debug(
            debug_pb2.DebugCommand(game_state=debug_pb2.DebugGameState.free),
            debug_pb2.DebugCommand(game_state=debug_pb2.DebugGameState.fast_build),
            game.create(UnitTypeId.EVOLUTION_CHAMBER, game.open_ground(home.towards(middle, 9))),
        )
        game.turn(4)
        chamber = game.newest(UnitTypeId.EVOLUTION_CHAMBER)
        game.order(AbilityId.EVOLUTION_CHAMBER_RESEARCH_MELEE_WEAPONS_1, chamber)
        game.turn(1)
        (research,) = game.state.actions
        assert isinstance(research, UnitCommand)
        assert research.ability is AbilityId.EVOLUTION_CHAMBER_RESEARCH_MELEE_WEAPONS_1
        game.turn(240)
        assert game.state.upgrades == {UpgradeId.ZERG_MELEE_WEAPONS_1}
        client.leave_game()
        client.quit()
