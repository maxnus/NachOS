"""Tests for this player and the enemy as a game describes them: `api.player`, `api.enemy` and
`EnemyRaceKnownEvent`."""

import pytest
from s2clientprotocol import common_pb2, data_pb2, sc2api_pb2

from sc2nachos import Api, Computer, Difficulty, NoEnemyError, Race, Result, UnitTypeId, run_local
from sc2nachos.debug import Cheat
from sc2nachos.events import EnemyRaceKnownEvent, TurnEvent
from sc2nachos.launch import MapFile, MapNotFoundError
from sc2nachos.player import Player
from sc2nachos.protocol import Client, Status
from sc2nachos.units import Alliance
from support import FakeTransport, make_game_info, make_observation, make_response, make_unit

_ME = 1
_OTHER = 2
_LADDER_MAP = "PylonAIE_v4"


def _participant(player_id: int, requested: Race, *, actual: Race = Race.NONE, name: str = "") -> sc2api_pb2.PlayerInfo:
    return sc2api_pb2.PlayerInfo(
        player_id=player_id,
        type=sc2api_pb2.PlayerType.Participant,
        race_requested=requested.value,
        race_actual=actual.value,
        player_name=name,
    )


def _computer(player_id: int, requested: Race) -> sc2api_pb2.PlayerInfo:
    return sc2api_pb2.PlayerInfo(
        player_id=player_id,
        type=sc2api_pb2.PlayerType.Computer,
        race_requested=requested.value,
        difficulty=sc2api_pb2.Difficulty.VeryEasy,
    )


def _observer(player_id: int) -> sc2api_pb2.PlayerInfo:
    return sc2api_pb2.PlayerInfo(player_id=player_id, type=sc2api_pb2.PlayerType.Observer)


_ME_AS_TERRAN = _participant(_ME, Race.TERRAN, actual=Race.TERRAN)


# The tables of the game played: what a unit seen reads its race from.
_ZERGLING_ROW = data_pb2.UnitTypeData(unit_id=UnitTypeId.ZERGLING, race=common_pb2.Race.Zerg)


def _play(
    api: Api,
    players: list[sc2api_pb2.PlayerInfo],
    *observations: sc2api_pb2.ResponseObservation,
    enemy_ladder_id: str | None = None,
) -> None:
    """Play a game of `players`, as player 1, through `observations`, the last ending it, as a ladder game if given
    the ladder's id for the enemy."""
    responses = [
        make_response(game_info=make_game_info(players=players)),
        make_response(data=sc2api_pb2.ResponseData(units=[_ZERGLING_ROW])),
    ]
    for index, observation in enumerate(observations):
        final = index == len(observations) - 1
        responses.append(make_response(Status.ENDED if final else Status.IN_GAME, observation=observation))
        if not final:
            responses.append(make_response(step=sc2api_pb2.ResponseStep()))
    transport = FakeTransport(make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=_ME)), *responses)
    client = Client(transport)
    client.join_game(Race.TERRAN)
    api.play(client, enemy_ladder_id=enemy_ladder_id)


def _short_game(*later: sc2api_pb2.ResponseObservation) -> tuple[sc2api_pb2.ResponseObservation, ...]:
    """A first observation, `later` ones, and one ending the game in a victory."""
    return (make_observation(0), *later, make_observation(16 * (len(later) + 1), (_ME, Result.VICTORY)))


class TestThisPlayer:
    def test_it_is_read_from_the_game_with_the_race_it_plays(self) -> None:
        api = Api()
        _play(api, [_participant(_ME, Race.RANDOM, actual=Race.PROTOSS), _computer(_OTHER, Race.ZERG)], *_short_game())
        assert api.player == Player(id=_ME, race=Race.PROTOSS, requested_race=Race.RANDOM, name=None)

    @pytest.mark.parametrize("requested", [Race.ZERG, Race.RANDOM])
    def test_as_a_computer_whose_race_the_game_never_gives_it_plays_the_race_it_asked_for(
        self, requested: Race
    ) -> None:
        """As a replay played back as the computer it is."""
        api = Api()
        _play(api, [_computer(_ME, requested), _participant(_OTHER, Race.TERRAN, actual=Race.TERRAN)], *_short_game())
        assert (api.player.race, api.player.requested_race) == (requested, requested)

    def test_a_game_that_lists_no_entry_for_it_is_refused(self) -> None:
        with pytest.raises(ValueError, match="the game lists no player 1"):
            _play(Api(), [_computer(_OTHER, Race.ZERG)], *_short_game())

    def test_its_name_is_the_one_the_game_gives(self) -> None:
        api = Api()
        me = _participant(_ME, Race.TERRAN, actual=Race.TERRAN, name="Alpha")
        _play(api, [me, _computer(_OTHER, Race.ZERG)], *_short_game())
        assert api.player.name == "Alpha"


class TestTheEnemy:
    def test_one_that_asked_for_a_race_plays_it_from_the_start(self) -> None:
        api = Api()
        _play(api, [_ME_AS_TERRAN, _computer(_OTHER, Race.ZERG)], *_short_game())
        enemy = api.enemy
        assert (enemy.id, enemy.race, enemy.requested_race, enemy.name) == (_OTHER, Race.ZERG, Race.ZERG, None)

    def test_a_game_of_two_gives_a_random_players_race_from_the_start(self) -> None:
        api = Api()
        other = _participant(_OTHER, Race.RANDOM, actual=Race.TERRAN, name="Beta")
        _play(api, [_ME_AS_TERRAN, other], *_short_game())
        enemy = api.enemy
        assert (enemy.race, enemy.requested_race, enemy.name) == (Race.TERRAN, Race.RANDOM, "Beta")

    def test_a_random_computers_race_is_known_once_its_first_unit_is_seen(self) -> None:
        api = Api()
        races: list[Race | None] = []
        known: list[EnemyRaceKnownEvent] = []
        api.on(TurnEvent)(lambda _: races.append(api.enemy.race))
        api.on(EnemyRaceKnownEvent)(lambda event: known.append(event))
        zergling = make_unit(5, UnitTypeId.ZERGLING, alliance=Alliance.ENEMY)
        _play(
            api, [_ME_AS_TERRAN, _computer(_OTHER, Race.RANDOM)], *_short_game(make_observation(16, units=[zergling]))
        )
        assert races == [None, Race.ZERG]
        assert known == [EnemyRaceKnownEvent(Race.ZERG, step=16)]

    def test_a_race_known_from_the_start_comes_once_on_the_first_turn(self) -> None:
        api = Api()
        known: list[EnemyRaceKnownEvent] = []
        api.on(EnemyRaceKnownEvent)(lambda event: known.append(event))
        _play(api, [_ME_AS_TERRAN, _computer(_OTHER, Race.PROTOSS)], *_short_game(make_observation(16)))
        assert known == [EnemyRaceKnownEvent(Race.PROTOSS, step=0)]

    def test_a_ladder_game_gives_the_ladders_id_for_it(self) -> None:
        api = Api()
        _play(api, [_ME_AS_TERRAN, _participant(_OTHER, Race.ZERG)], *_short_game(), enemy_ladder_id="5a1f")
        assert api.enemy.ladder_id == "5a1f"

    def test_outside_a_ladder_game_it_has_no_ladder_id(self) -> None:
        api = Api()
        _play(api, [_ME_AS_TERRAN, _computer(_OTHER, Race.ZERG)], *_short_game())
        assert api.enemy.ladder_id is None

    def test_an_observer_is_no_enemy(self) -> None:
        api = Api()
        _play(api, [_ME_AS_TERRAN, _observer(3), _computer(_OTHER, Race.ZERG)], *_short_game())
        assert api.enemy.id == _OTHER

    def test_a_game_of_one_player_has_no_enemy_to_read_and_no_race_comes(self) -> None:
        api = Api()
        known: list[EnemyRaceKnownEvent] = []
        api.on(EnemyRaceKnownEvent)(lambda event: known.append(event))
        _play(api, [_ME_AS_TERRAN], *_short_game(make_observation(16)))
        with pytest.raises(NoEnemyError, match="no other player"):
            _ = api.enemy
        assert not known


def test_a_race_is_a_protocol_race() -> None:
    """`Race` reads the protocol's races, `NONE` for a player the game gives no race."""
    assert {race.value for race in Race} == set(common_pb2.Race.values())


@pytest.mark.integration
def test_in_a_real_game_against_a_random_computer_its_race_is_known_with_its_first_unit() -> None:
    """Run with `pytest -m integration`. Plays the first minute or so of a game, until an enemy unit is seen."""
    try:
        MapFile.find(_LADDER_MAP)
    except MapNotFoundError as missing:
        pytest.skip(str(missing))
    api = Api()
    known: list[EnemyRaceKnownEvent] = []
    races_before: list[Race | None] = []

    @api.on(TurnEvent)
    def read_race(event: TurnEvent) -> None:
        if not known:
            races_before.append(api.enemy.race)

    @api.on(EnemyRaceKnownEvent)
    def found(event: EnemyRaceKnownEvent) -> None:
        known.append(event)
        api.leave_game()

    # The whole map in sight, so that the enemy's first units are seen at once.
    @api.on(TurnEvent, at_step=8)
    def reveal(event: TurnEvent) -> None:
        api.debug.turn_on(Cheat.SHOW_MAP)

    opponent = Computer(Race.RANDOM, Difficulty.VERY_EASY)
    run_local(_LADDER_MAP, api, opponent, race=Race.RANDOM, steps_per_turn=8, time_limit=60, window=(640, 480))

    assert api.player.requested_race is Race.RANDOM
    assert api.player.race in (Race.TERRAN, Race.ZERG, Race.PROTOSS)
    enemy = api.enemy
    assert enemy.requested_race is Race.RANDOM
    assert set(races_before) == {None}
    (event,) = known
    assert event.race is enemy.race
    assert event.race in (Race.TERRAN, Race.ZERG, Race.PROTOSS)
