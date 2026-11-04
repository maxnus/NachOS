"""What this player sees: where it can see now, where it has seen, and the step it last saw each tile at, stamped at
every observation."""

from pathlib import Path

import numpy
import pytest
from s2clientprotocol import common_pb2, sc2api_pb2

from sc2nachos import Api, GameTime, run_recording
from sc2nachos.events import GameStartEvent, TurnEvent
from sc2nachos.geometry import Circle, Grid, Point, Rectangle
from sc2nachos.match import Race, Result
from sc2nachos.protocol import Client, ProtocolError, Status
from support import FakeTransport, make_bytes, make_game_info, make_observation, make_response

CORPUS = sorted((Path(__file__).parent / "corpus").glob("*.sc2rec"))

# Tiles on the eight by eight map `make_game_info` draws.
_HOME = (1, 1)
_OUT_THERE = (5, 2)
_NEVER_SEEN = (6, 6)


def _at(tile: tuple[int, int]) -> Point:
    """The center of `tile`."""
    return Point((tile[0] + 0.5, tile[1] + 0.5))


def _visibility(
    *, in_sight: tuple[tuple[int, int], ...] = (), seen: tuple[tuple[int, int], ...] = ()
) -> common_pb2.ImageData:
    """The game's visibility image of the eight by eight map: the tiles `in_sight` now, and those `seen` before."""
    rows = [[0] * 8 for _ in range(8)]
    for x, y in seen:
        rows[7 - y][x] = 1
    for x, y in in_sight:
        rows[7 - y][x] = 2
    return make_bytes(*rows)


def _play(api: Api, *observations: sc2api_pb2.ResponseObservation) -> None:
    """Play `api` through `observations`, the last of which ends the game."""
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


# Home in sight for two observations, then out there in sight and home only seen before.
_HOME_THEN_OUT_THERE = (
    make_observation(0, visibility=_visibility(in_sight=(_HOME,))),
    make_observation(16, visibility=_visibility(in_sight=(_HOME,))),
    make_observation(32, visibility=_visibility(in_sight=(_OUT_THERE,), seen=(_HOME,))),
    make_observation(48, (1, Result.VICTORY)),
)


class TestLastInSight:
    def test_a_tile_reads_the_last_step_it_was_in_sight_and_minus_one_until_then_and_off_the_grid(self) -> None:
        api = Api()
        reads: list[tuple[int, ...]] = []

        @api.on(TurnEvent)
        def read(_: TurnEvent) -> None:
            grid = api.vision.last_in_sight
            reads.append((api.step, *(grid[_at(tile)] for tile in (_HOME, _OUT_THERE, _NEVER_SEEN)), grid[(20.5, 0.5)]))

        _play(api, *_HOME_THEN_OUT_THERE)
        assert reads == [(0, 0, -1, -1, -1), (16, 16, -1, -1, -1), (32, 16, 32, -1, -1)]

    def test_the_first_observation_is_stamped_before_any_handler_runs(self) -> None:
        api = Api()
        reads: list[tuple[int, GameTime | None]] = []

        @api.on(GameStartEvent)
        def read(_: GameStartEvent) -> None:
            reads.append((api.vision.last_in_sight[_at(_HOME)], api.vision.time_unseen(_at(_HOME))))

        _play(api, *_HOME_THEN_OUT_THERE)
        assert reads == [(0, GameTime.ZERO)]

    def test_a_grid_read_in_one_turn_keeps_its_values_and_refuses_writes(self) -> None:
        api = Api()
        held: list[Grid[int]] = []

        @api.on(TurnEvent)
        def hold(_: TurnEvent) -> None:
            assert api.vision.last_in_sight is api.vision.last_in_sight
            held.append(api.vision.last_in_sight)

        _play(api, *_HOME_THEN_OUT_THERE)
        assert [(grid[_at(_HOME)], grid[_at(_OUT_THERE)]) for grid in held] == [(0, -1), (16, -1), (16, 32)]
        with pytest.raises(TypeError):
            held[0][_at(_HOME)] = 48


class TestTimeUnseen:
    def test_a_tile_or_an_area_reads_how_long_ago_any_tile_of_it_was_in_sight(self) -> None:
        api = Api()
        reads: list[GameTime | None] = []

        @api.on(TurnEvent)
        def read(_: TurnEvent) -> None:
            if api.step == 32:
                reads.extend(
                    api.vision.time_unseen(where)
                    for where in (
                        _at(_HOME),
                        _at(_OUT_THERE),
                        _at(_NEVER_SEEN),
                        Rectangle(0, 0, 3, 3),
                        Rectangle(0, 0, 8, 8),
                        Circle(_at(_NEVER_SEEN), 1),
                    )
                )

        _play(api, *_HOME_THEN_OUT_THERE)
        assert reads == [GameTime(steps=16), GameTime.ZERO, None, GameTime(steps=16), GameTime.ZERO, None]

    def test_ground_off_the_grid_was_never_in_sight(self) -> None:
        api = Api()
        reads: list[GameTime | None] = []

        @api.on(TurnEvent)
        def read(_: TurnEvent) -> None:
            reads.extend((api.vision.time_unseen((20.5, 0.5)), api.vision.time_unseen(Rectangle(20, 20, 2, 2))))

        _play(
            api,
            make_observation(0, visibility=_visibility(in_sight=(_HOME,))),
            make_observation(16, (1, Result.VICTORY)),
        )
        assert reads == [None, None]


class TestWhatTheObservationReports:
    def test_in_sight_and_explored_are_the_observations_own(self) -> None:
        api = Api()
        reads: list[tuple[bool, ...]] = []

        @api.on(TurnEvent)
        def read(_: TurnEvent) -> None:
            home, out_there = _at(_HOME), _at(_OUT_THERE)
            vision = api.vision
            reads.append((vision.in_sight[home], vision.explored[home], vision.in_sight[out_there]))

        _play(api, *_HOME_THEN_OUT_THERE)
        assert reads == [(True, True, False), (True, True, False), (False, True, True)]

    def test_an_observation_without_vision_plays_and_its_turn_raises_only_when_reading_it(self) -> None:
        """The stamps of the observations before it are kept, and the next one with vision stamps on them."""
        api = Api()
        reads: list[tuple[int, int] | str] = []

        @api.on(TurnEvent)
        def read(_: TurnEvent) -> None:
            vision = api.vision
            if api.step == 16:
                for read_it in (
                    lambda: vision.in_sight,
                    lambda: vision.last_in_sight,
                    lambda: vision.time_unseen((1.5, 1.5)),
                ):
                    with pytest.raises(ProtocolError):
                        read_it()
                reads.append("raised")
            else:
                reads.append((vision.last_in_sight[_at(_HOME)], vision.last_in_sight[_at(_OUT_THERE)]))

        _play(
            api,
            make_observation(0, visibility=_visibility(in_sight=(_HOME,))),
            make_observation(16),
            make_observation(32, visibility=_visibility(in_sight=(_OUT_THERE,), seen=(_HOME,))),
            make_observation(48, (1, Result.VICTORY)),
        )
        assert reads == [(0, -1), "raised", (0, 32)]


@pytest.mark.parametrize("path", CORPUS[:1], ids=lambda path: path.stem)
def test_through_a_recorded_game_each_tile_in_sight_reads_the_turns_step_and_each_one_stamped_is_explored(
    path: Path,
) -> None:
    api = Api()
    turns: list[int] = []

    @api.on(TurnEvent)
    def check(_: TurnEvent) -> None:
        vision = api.vision
        steps = vision.last_in_sight.values
        assert numpy.array_equal(steps == api.step, vision.in_sight.values)
        assert not numpy.any((steps >= 0) & ~vision.explored.values)
        turns.append(api.step)

    run_recording(path, api)
    assert len(turns) > 100
