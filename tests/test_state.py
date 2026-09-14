"""What an observation reports beyond its units: the score, the counters, the map as it stands, and what happened."""

from contextlib import closing
from itertools import count
from typing import Any

import pytest
from s2clientprotocol import common_pb2, data_pb2, debug_pb2, raw_pb2, sc2api_pb2, score_pb2

from sc2nachos import Api, NotPlayingError
from sc2nachos.gamedata import Resources
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, EffectId, UncuratedIdError, UnitTypeId, UpgradeId
from sc2nachos.ids.raw import RawEffectId, RawUpgradeId
from sc2nachos.launch import GameProcess, Map, MapNotFoundError
from sc2nachos.match import Computer, Difficulty, Participant, Race, Result
from sc2nachos.protocol import Client, ProtocolError, Status, WebSocketTransport
from sc2nachos.state import (
    AutocastToggle,
    CameraMove,
    CategoryScore,
    ChatMessage,
    Effect,
    Supply,
    UiUnitCounts,
    UnitCommand,
    ValueScore,
    VitalScore,
)
from sc2nachos.state._state import _State
from sc2nachos.units import Alliance, Unit
from sc2nachos.units._tracker import _UnitTracker
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
)

_TABLES = make_tables(
    data_pb2.UnitTypeData(unit_id=UnitTypeId.ZERGLING, food_required=0.5),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.BANELING, food_required=0.5),
    data_pb2.UnitTypeData(unit_id=UnitTypeId.ROACH, food_required=2),
)
_ENEMY = Alliance.ENEMY


class _Game:
    """The units and the rest of a game's observations, taken in one at a time, on an eight by eight map."""

    def __init__(self, game_info: sc2api_pb2.ResponseGameInfo | None = None) -> None:
        self.tracker = _UnitTracker(_TABLES)
        self.map = GameMap(game_info or make_game_info())

    def observe(self, step: int = 0, **fields: Any) -> _State:
        observation = make_observation(step, **fields)
        self.tracker.update(observation.observation.raw_data, step)
        return _State(observation, self.tracker, self.map)


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
        # The single amounts are all read above or in the test below, and the APM is left out.
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

    def test_an_uncurated_upgrade_raises_when_read(self) -> None:
        state = _Game().observe(upgrades=[RawUpgradeId.CarrierLaunchSpeedUpgrade])
        with pytest.raises(UncuratedIdError):
            _ = state.upgrades

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
    def test_chat_is_every_message_this_player_included(self) -> None:
        state = _Game().observe(chat=[(1, "gl hf"), (2, "you too")])
        assert state.chat == (ChatMessage(1, "gl hf"), ChatMessage(2, "you too"))

    def test_a_unit_command_names_its_units_and_its_target(self) -> None:
        game = _Game()
        game.observe(0, units=[make_unit(1), make_unit(2), make_unit(9, alliance=_ENEMY)])
        present = game.tracker.present_units
        marine, other, enemy = present.by_id(100001), present.by_id(100002), present.by_id(400001)
        actions = [
            _command(AbilityId.GENERAL_MOVE_EXACT, 1, 2, target_world_space_pos=common_pb2.Point2D(x=5.0, y=6.0)),
            _command(AbilityId.GENERAL_ATTACK, 1, target_unit_tag=9),
            _command(AbilityId.GENERAL_STOP, 2, queued=True),
        ]
        state = game.observe(16, units=[make_unit(1), make_unit(2)], dead=(9,), actions=actions)
        assert state.actions == (
            UnitCommand(15, AbilityId.GENERAL_MOVE_EXACT, (marine, other), Point((5.0, 6.0)), queued=False),
            UnitCommand(15, AbilityId.GENERAL_ATTACK, (marine,), enemy, queued=False),
            UnitCommand(15, AbilityId.GENERAL_STOP, (other,), None, queued=True),
        )
        assert enemy.is_dead

    def test_an_autocast_toggle_and_a_camera_move(self) -> None:
        game = _Game()
        game.observe(0, units=[make_unit(1, UnitTypeId.MEDIVAC)])
        medivac: Unit[Any] = game.tracker.present_units[0]
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
        """An observation without map state plays, and the grid it holds nothing for raises only when read."""
        api = Api()
        self._play(api, make_observation(0, (1, Result.VICTORY)))
        with pytest.raises(ProtocolError):
            _ = api.vision


@pytest.mark.integration
def test_in_a_real_game_the_state_is_what_was_done_and_seen() -> None:
    """Run with `pytest -m integration`. Starts the game as zerg and plays half a minute of it."""
    try:
        game_map = Map.find("PylonAIE_v4")
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

        # The map as it stands.
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
        game.order(AbilityId.GENERAL_MOVE, drone, target=out_there)
        game.turn(1)
        (command,) = game.state.actions
        assert isinstance(command, UnitCommand)
        assert (command.ability, command.units) == (AbilityId.GENERAL_MOVE_EXACT, (drone,))
        # The game sends points in single precision.
        assert command.target == pytest.approx(out_there, abs=1e-3)

        # A message sent to the chat comes back.
        chat = sc2api_pb2.ActionChat(channel=sc2api_pb2.ActionChat.Broadcast, message="gl hf")
        client.act([sc2api_pb2.Action(action_chat=chat)])
        game.turn(1)
        assert game.state.chat == (ChatMessage(game.player, "gl hf"),)
        game.turn(1)
        assert game.state.chat == ()

        # A unit that dies is among the dead units in one observation only.
        game.debug(game.kill(zergling))
        while not zergling.is_dead:
            game.turn(1)
        assert list(game.tracker.newly_dead_units) == [zergling]
        game.turn(1)
        assert not game.tracker.newly_dead_units

        # An upgrade once researched. The `tech_tree` cheat would grant dozens of upgrades besides.
        game.debug(
            debug_pb2.DebugCommand(game_state=debug_pb2.DebugGameState.free),
            debug_pb2.DebugCommand(game_state=debug_pb2.DebugGameState.fast_build),
            game.create(UnitTypeId.EVOLUTION_CHAMBER, game.open_ground(home.towards(middle, 9))),
        )
        game.turn(4)
        chamber = game.newest(UnitTypeId.EVOLUTION_CHAMBER)
        game.order(AbilityId.EVOLUTION_CHAMBER_RESEARCH_MELEE_WEAPONS, chamber)
        game.turn(1)
        (research,) = game.state.actions
        assert isinstance(research, UnitCommand)
        assert research.ability is AbilityId.EVOLUTION_CHAMBER_RESEARCH_MELEE_WEAPONS_1
        game.turn(240)
        assert game.state.upgrades == {UpgradeId.ZERG_MELEE_WEAPONS_1}
        client.leave_game()
        client.quit()
