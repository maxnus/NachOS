"""The api and the two runners, tested without a game wherever possible."""

import itertools
from collections.abc import Iterator, Mapping
from contextlib import closing
from pathlib import Path

import pytest
from loguru import logger
from s2clientprotocol import error_pb2, sc2api_pb2

from sc2nachos import Api, NotPlayingError, run_ladder, run_local
from sc2nachos.events import (
    AnyUnitDiedEvent,
    Event,
    GameEndEvent,
    GameStartEvent,
    OwnUnitCreatedEvent,
    TurnEvent,
    TurnStartEvent,
)
from sc2nachos.gamedata._techtree import TECH_TREE
from sc2nachos.gamemap import Expansion
from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId
from sc2nachos.launch import GameProcess, Installation, MapFile, MapNotFoundError, free_port
from sc2nachos.match import Computer, Difficulty, Participant, Race, Result
from sc2nachos.protocol import (
    Client,
    ConnectionClosedError,
    PlaybackTransport,
    ProtocolError,
    Recording,
    Status,
    WebSocketTransport,
)
from sc2nachos.state import ActionResult, Research
from sc2nachos.units import Alliance, UnitType
from support import FakeTransport, make_game_info, make_observation, make_response, make_unit

# A map from the current AIE ladder pool, for the test games.
_LADDER_MAP = "PylonAIE_v4"


def _game(*steps: int, ending: Result | None = Result.VICTORY, stepped: bool = True) -> list[sc2api_pb2.Response]:
    """The game's side of a whole conversation: the map, the tables, then a turn at each of `steps`.

    With `ending` as `None` the game never reports a result, which lets a time limit end it.
    """
    responses = [
        make_response(game_info=make_game_info()),
        make_response(data=sc2api_pb2.ResponseData()),
    ]
    for index, step in enumerate(steps):
        final = ending is not None and index == len(steps) - 1
        results = [(1, ending)] if final and ending is not None else []
        status = Status.ENDED if final else Status.IN_GAME
        responses.append(make_response(status, observation=make_observation(step, *results)))
        if not final and stepped:
            responses.append(make_response(step=sc2api_pb2.ResponseStep()))
    return responses


def _game_of(
    *observations: sc2api_pb2.ResponseObservation, game_info: sc2api_pb2.ResponseGameInfo | None = None
) -> list[sc2api_pb2.Response]:
    """The game's side of a whole conversation on the map `game_info` gives, or eight by eight of open ground: a turn
    at each of `observations`, the last ending the game."""
    responses = [
        make_response(game_info=game_info or make_game_info()),
        make_response(data=sc2api_pb2.ResponseData()),
    ]
    for index, observation in enumerate(observations):
        final = index == len(observations) - 1
        responses.append(make_response(Status.ENDED if final else Status.IN_GAME, observation=observation))
        if not final:
            responses.append(make_response(step=sc2api_pb2.ResponseStep()))
    return responses


def _joined(*responses: sc2api_pb2.Response) -> tuple[Client, FakeTransport]:
    """A client already joined as player one, over a transport that then returns `responses`."""
    transport = FakeTransport(make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=1)), *responses)
    client = Client(transport)
    client.join_game(Race.TERRAN)
    return client, transport


def _ladder_transport() -> FakeTransport:
    """A transport for a join and two turns, enough to run a ladder game to its end."""
    return FakeTransport(
        make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=1)),
        *_game(0, 2),
        make_response(),
    )


class _FakeGame:
    """A `GameProcess` that never started anything."""

    url = "ws://127.0.0.1:0/sc2api"

    def __init__(self) -> None:
        self.terminated = False

    def __enter__(self) -> "_FakeGame":
        return self

    def __exit__(self, *_: object) -> None:
        self.terminated = True


def _local_transport() -> FakeTransport:
    """A transport for a creation, a join, two turns, and the teardown."""
    return FakeTransport(
        make_response(create_game=sc2api_pb2.ResponseCreateGame()),
        make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=1)),
        *_game(0, 2),
        make_response(),
        make_response(),
    )


def _no_real_game(monkeypatch: pytest.MonkeyPatch, transport: FakeTransport) -> _FakeGame:
    """Replace the installation, the game process and the socket with fakes, so a local game needs none of them. The
    installation holds the one build the tech tree was swept on."""
    game = _FakeGame()
    monkeypatch.setattr(Installation, "find", classmethod(lambda cls, **kwargs: Installation(Path("StarCraftII"))))
    monkeypatch.setattr(Installation, "builds", lambda self: frozenset({TECH_TREE.base_build}))
    monkeypatch.setattr(GameProcess, "launch", classmethod(lambda cls, *args, **kwargs: game))
    monkeypatch.setattr(WebSocketTransport, "connect", classmethod(lambda cls, url, **kwargs: transport))
    return game


def _somewhere() -> MapFile:
    """A map file that is never looked up, so no installation is needed."""
    return MapFile(Path("Somewhere.SC2Map"))


class TestBeforeAGame:
    @pytest.mark.parametrize("name", ["client", "map", "expansions", "data", "step", "time", "result", "units"])
    def test_what_belongs_to_a_game_says_there_is_none(self, name: str) -> None:
        """Zero is a valid step and `None` means a game still going, so neither can mean no game."""
        with pytest.raises(NotPlayingError, match="no game has been joined"):
            getattr(Api(), name)

    def test_a_handler_subscribes_before_any_game_so_it_can_at_import(self) -> None:
        api = Api()

        @api.on(TurnEvent)
        def on_turn(event: TurnEvent) -> None:
            pass

        assert api._events._subscriptions.by_type[TurnEvent]


class TestPlaying:
    def test_a_game_is_played_to_the_result_the_game_gives(self) -> None:
        client, _ = _joined(*_game(0, 2, 4))
        api = Api()
        assert api.play(client, steps_per_turn=2) is Result.VICTORY
        assert api.result is Result.VICTORY
        assert api.client is client
        assert api.map.name == "Somewhere"
        assert api.data.units == {}

    def test_the_map_and_the_tables_are_asked_for_once(self) -> None:
        """The map never changes, the tables are wanted before any upgrade, and game_info alone is 77 KB a request."""
        client, transport = _joined(*_game(0, 2, 4, 6, 8))
        Api().play(client, steps_per_turn=2)
        kinds = [request.WhichOneof("request") for request in transport.requests]
        assert kinds.count("game_info") == 1
        assert kinds.count("data") == 1

    def test_the_step_is_the_game_loop_last_observed(self) -> None:
        client, _ = _joined(*_game(0, 2, 4))
        api = Api()
        api.play(client, steps_per_turn=2)
        assert api.step == 4

    def test_a_game_over_when_first_observed_is_never_stepped(self) -> None:
        client, transport = _joined(*_game(0))
        api = Api()
        assert api.play(client) is Result.VICTORY
        assert api.step == 0
        assert not any(request.HasField("step") for request in transport.requests)

    def test_every_turn_steps_the_game_on_but_the_one_that_ends_it(self) -> None:
        client, transport = _joined(*_game(0, 8, 16))
        Api().play(client, steps_per_turn=8)
        assert [request.step.count for request in transport.requests if request.HasField("step")] == [8, 8]

    def test_the_time_played_is_the_step_in_seconds(self) -> None:
        client, _ = _joined(*_game(0, 224))
        api = Api()
        api.play(client)
        assert api.time == pytest.approx(10.0)

    def test_a_time_limit_ends_the_game_in_a_tie(self) -> None:
        client, transport = _joined(*_game(0, 112, ending=None))
        api = Api()
        assert api.play(client, steps_per_turn=112, time_limit=5) is Result.TIE
        assert api.step == 112
        assert [request.step.count for request in transport.requests if request.HasField("step")] == [112]

    def test_a_realtime_game_asks_for_the_step_it_wants_instead_of_stepping(self) -> None:
        """A realtime game runs on its own, so there is nothing to step."""
        client, transport = _joined(*_game(0, 4, 8, ending=Result.DEFEAT, stepped=False))
        assert Api().play(client, steps_per_turn=4, realtime=True) is Result.DEFEAT
        asked = [request.observation.game_loop for request in transport.requests if request.HasField("observation")]
        assert asked == [0, 4, 8]
        assert not any(request.HasField("step") for request in transport.requests)

    def test_a_stepped_game_played_in_real_time_is_refused_on_its_first_turn(self) -> None:
        """It never moves: asked for a step it has not reached, a stepped game answers at once, where it stands."""
        client, transport = _joined(*_game(*[0] * 20, stepped=False))
        with pytest.raises(ValueError, match="answered at step 0 when asked for step 4"):
            Api().play(client, steps_per_turn=4, realtime=True)
        assert sum(request.HasField("observation") for request in transport.requests) == 2

    def test_a_bot_that_leaves_sends_its_turn_then_leaves_and_loses(self) -> None:
        client, transport = _joined(
            make_response(game_info=make_game_info()),
            make_response(data=sc2api_pb2.ResponseData()),
            make_response(observation=make_observation(0)),
            make_response(action=sc2api_pb2.ResponseAction(result=[error_pb2.ActionResult.Success])),
            make_response(Status.LAUNCHED),
        )
        api = Api()
        ended: list[Result] = []

        @api.on(TurnEvent)
        def give_up(event: TurnEvent) -> None:
            api.chat("gg")
            api.leave_game()
            api.leave_game()

        @api.on(GameEndEvent)
        def end(event: GameEndEvent) -> None:
            ended.append(event.result)

        assert api.play(client) is Result.DEFEAT
        assert (api.result, ended) == (Result.DEFEAT, [Result.DEFEAT])
        asked = [request.WhichOneof("request") for request in transport.requests]
        assert asked == ["join_game", "game_info", "data", "observation", "action", "leave_game"]

    @pytest.mark.parametrize("realtime", [True, False])
    def test_a_game_that_ends_while_the_handlers_run_ends_in_its_result(self, realtime: bool) -> None:
        """The orders reach a game that is over, which refuses them (#14): a realtime game runs on while the
        handlers do."""
        over = make_response(Status.ENDED, error=["Game has already ended"])
        client, _ = _joined(
            make_response(game_info=make_game_info()),
            make_response(data=sc2api_pb2.ResponseData()),
            make_response(observation=make_observation(0)),
            over,
            make_response(Status.ENDED, observation=make_observation(4, (1, Result.VICTORY))),
        )
        api = Api()
        ended: list[Result] = []

        @api.on(TurnEvent)
        def look(event: TurnEvent) -> None:
            api.camera.move_to((4, 4))

        @api.on(GameEndEvent)
        def end(event: GameEndEvent) -> None:
            ended.append(event.result)

        assert api.play(client, steps_per_turn=4, realtime=realtime) is Result.VICTORY
        assert api.result is Result.VICTORY
        assert ended == [Result.VICTORY]

    def test_a_bot_that_asks_to_leave_as_the_game_ends_gets_its_result_and_sends_no_leave(self) -> None:
        over = make_response(Status.ENDED, error=["Game has already ended"])
        client, transport = _joined(
            make_response(game_info=make_game_info()),
            make_response(data=sc2api_pb2.ResponseData()),
            make_response(observation=make_observation(0)),
            over,
            make_response(Status.ENDED, observation=make_observation(4, (1, Result.VICTORY))),
        )
        api = Api()

        @api.on(TurnEvent)
        def look_and_give_up(event: TurnEvent) -> None:
            api.camera.move_to((4, 4))
            api.leave_game()

        assert api.play(client, steps_per_turn=4) is Result.VICTORY
        assert not any(request.HasField("leave_game") for request in transport.requests)

    def test_a_step_that_says_the_game_is_over_is_followed_by_asking_how(self) -> None:
        """Only an observation carries the result, so the status on a step response does not end the game."""
        client, _ = _joined(
            make_response(game_info=make_game_info()),
            make_response(data=sc2api_pb2.ResponseData()),
            make_response(observation=make_observation(0)),
            make_response(Status.ENDED, step=sc2api_pb2.ResponseStep()),
            make_response(Status.ENDED, observation=make_observation(2, (1, Result.DEFEAT))),
        )
        assert Api().play(client, steps_per_turn=2) is Result.DEFEAT

    def test_a_game_that_ends_without_saying_how_is_undecided(self) -> None:
        client, _ = _joined(
            make_response(game_info=make_game_info()),
            make_response(data=sc2api_pb2.ResponseData()),
            make_response(Status.ENDED, observation=make_observation(0)),
            make_response(Status.ENDED, observation=make_observation(0)),
        )
        assert Api().play(client) is Result.UNDECIDED

    def test_the_last_turn_s_refusals_are_listed_ahead_of_what_the_game_gave_up_on(self) -> None:
        marine = make_unit(1)
        given_up = sc2api_pb2.ActionError(
            unit_tag=1, ability_id=AbilityId.ATTACK, result=error_pb2.ActionResult.NotEnoughFood
        )
        client, _ = _joined(
            make_response(game_info=make_game_info()),
            make_response(data=sc2api_pb2.ResponseData()),
            make_response(observation=make_observation(0, units=[marine])),
            make_response(action=sc2api_pb2.ResponseAction(result=[error_pb2.ActionResult.NotSupported])),
            make_response(step=sc2api_pb2.ResponseStep()),
            make_response(observation=make_observation(2, units=[marine], action_errors=[given_up])),
            make_response(step=sc2api_pb2.ResponseStep()),
            make_response(Status.ENDED, observation=make_observation(4, (1, Result.VICTORY), units=[marine])),
        )
        api = Api()
        listed: list[list[tuple[int, int | None, AbilityId | None, ActionResult]]] = []

        @api.on(TurnEvent)
        def turn(event: TurnEvent) -> None:
            failures = api.action_failures
            listed.append([(f.step, f.unit and f.unit.tag, f.ability, f.action_result) for f in failures])
            if event.step == 0:
                api.orders.issue(api.units.own[0], AbilityId.MOVE, target=(20.0, 21.0))

        api.play(client, steps_per_turn=2)

        assert listed == [
            [],
            [
                (0, 1, AbilityId.MOVE, ActionResult.NOT_SUPPORTED),
                (2, 1, AbilityId.ATTACK, ActionResult.NOT_ENOUGH_FOOD),
            ],
        ]

    def test_what_is_in_production_is_read_by_a_type_a_group_an_id_or_several(self) -> None:
        warping = make_unit(1, UnitTypeId.ZEALOT, build_progress=0.5)
        client, _ = _joined(*_game_of(make_observation(0, units=[warping]), make_observation(2, (1, Result.VICTORY))))
        api = Api()
        counted: list[tuple[int, int, int, int]] = []
        zealots: list[tuple[UnitTypeId, int, float | None]] = []
        researches: list[Mapping[UpgradeId, Research]] = []

        @api.on(TurnEvent)
        def turn(event: TurnEvent) -> None:
            stalkers = (UnitTypeId.ZEALOT, UnitTypeId.STALKER)
            counted.append(
                (
                    len(api.in_production(UnitTypeId.ZEALOT)),
                    len(api.in_production(stalkers)),
                    len(api.in_production(UnitType.Protoss)),
                    len(api.in_production(UnitType.Stalker, UnitType.Adept)),
                )
            )
            zealots.extend((item.type_id, item.unit.tag, item.progress) for item in api.in_production(UnitType.Zealot))
            researches.append(api.researches)

        api.play(client)

        assert counted == [(1, 1, 1, 0)]
        assert zealots == [(UnitTypeId.ZEALOT, 1, 0.5)]
        assert researches == [{}]

    def test_the_expansions_are_found_once_at_the_start_holding_the_units_on_the_map(self) -> None:
        fields = [
            make_unit(tag, UnitTypeId.MINERAL_FIELD, at=(10.0 + 2 * tag, 20.5), alliance=Alliance.NEUTRAL)
            for tag in range(1, 7)
        ]
        observations = [
            make_observation(0, units=fields),
            make_observation(2, units=fields),
            make_observation(4, (1, Result.VICTORY), units=fields),
        ]
        client, _ = _joined(*_game_of(*observations, game_info=make_game_info(*["#" * 32] * 32)))
        api = Api()
        seen: list[tuple[Expansion, ...]] = []
        held: list[bool] = []

        @api.on(TurnEvent)
        def turn(event: TurnEvent) -> None:
            seen.append(api.expansions)
            held.extend(field in api.units for expansion in api.expansions for field in expansion.mineral_fields)

        api.play(client, steps_per_turn=2)

        assert len(seen) == 2 and seen[0] is seen[1]
        field_tags = [sorted(field.tag for field in expansion.mineral_fields) for expansion in seen[0]]
        assert field_tags == [[1, 2, 3, 4, 5, 6]]
        assert held == [True] * 12

    def test_one_api_plays_game_after_game_each_from_nothing(self) -> None:
        """An api at module scope plays every game of its process."""
        api = Api()
        api.play(_joined(*_game(0, 4, 8, ending=Result.DEFEAT))[0], steps_per_turn=4)
        client, transport = _joined(*_game(0, 4, stepped=False))
        assert api.play(client, steps_per_turn=4, realtime=True) is Result.VICTORY
        assert api.step == 4
        assert api.client is client
        asked = [request.observation.game_loop for request in transport.requests if request.HasField("observation")]
        assert asked == [0, 4]


def _production_is_asked_by_types_or_by_ids(api: Api) -> None:
    """What the type checker makes of asking what is in production. Never run."""
    api.in_production([UnitTypeId.ZEALOT], UnitType.Stalker)  # pyright: ignore[reportArgumentType]
    api.in_production(UnitTypeId.ZEALOT, UnitType.Stalker)  # pyright: ignore[reportArgumentType]


def _record(api: Api, seen: list[tuple[str, int]]) -> None:
    """Record every lifecycle event `api` hands out into `seen`, as the type name and the step."""
    for kind in (GameStartEvent, TurnStartEvent, TurnEvent, GameEndEvent):

        @api.on(kind)
        def note(event: Event) -> None:
            seen.append((type(event).__name__, event.step))


class TestEvents:
    def test_a_game_starts_takes_its_turns_and_ends_and_its_last_observation_gets_no_turn(self) -> None:
        client, _ = _joined(*_game(0, 2, 4, ending=Result.DEFEAT))
        api, seen, results = Api(), [], []
        _record(api, seen)
        api.on(GameEndEvent)(lambda event: results.append(event.result))
        api.play(client, steps_per_turn=2)
        assert seen == [
            ("GameStartEvent", 0),
            ("TurnStartEvent", 0),
            ("TurnEvent", 0),
            ("TurnStartEvent", 2),
            ("TurnEvent", 2),
            ("GameEndEvent", 4),
        ]
        assert results == [Result.DEFEAT]

    def test_every_handler_reads_the_game_as_of_its_events_step(self) -> None:
        client, _ = _joined(*_game(0, 2, 4))
        api, steps = Api(), []
        for kind in (GameStartEvent, TurnStartEvent, TurnEvent, GameEndEvent):
            api.on(kind)(lambda event: steps.append((event.step, api.step)))
        api.play(client, steps_per_turn=2)
        assert len(steps) == 6
        assert all(event_step == api_step for event_step, api_step in steps)

    def test_a_game_called_at_its_time_limit_ends_in_a_tie_all_the_same(self) -> None:
        client, _ = _joined(*_game(0, 112, ending=None))
        api, results = Api(), []
        api.on(GameEndEvent)(lambda event: results.append((event.step, event.result)))
        api.play(client, steps_per_turn=112, time_limit=5)
        assert results == [(112, Result.TIE)]

    def test_what_subscribes_before_or_during_a_game_handles_the_next_one_too(self) -> None:
        """A handler subscribed at import works in every game its process plays, and a `once` handler fires once per
        game."""
        api = Api()
        fired: list[tuple[str, int]] = []

        @api.on(TurnEvent, at_step=2)
        def function(event: TurnEvent) -> None:
            fired.append(("function", event.step))

        class Handlers:
            def __init__(self, name: str) -> None:
                self.name = name
                api.subscribe(self)

            @api.on(TurnEvent, once=True)
            def on_turn(self, event: TurnEvent) -> None:
                fired.append((self.name, event.step))

        Handlers("before")

        @api.on(GameStartEvent)
        def make(event: GameStartEvent) -> None:
            # Created in the first game only, subscribed from then on.
            Handlers("during")
            api.unsubscribe(make)

        api.play(_joined(*_game(0, 2, 4))[0], steps_per_turn=2)
        first = list(fired)
        fired.clear()
        api.play(_joined(*_game(0, 2, 4))[0], steps_per_turn=2)
        assert first == fired == [("before", 0), ("during", 0), ("function", 2)]

    def test_handler_timings_count_a_games_calls_for_an_api_made_to_time_handlers(self) -> None:
        api = Api(time_handlers=True)
        api.on(TurnEvent)(lambda event: None)
        api.play(_joined(*_game(0, 2, 4))[0], steps_per_turn=2)
        ((_, timing),) = api.handler_timings[TurnEvent].items()
        assert timing.calls == 2
        with pytest.raises(RuntimeError, match="time_handlers=True"):
            _ = Api().handler_timings

    def test_what_happened_comes_within_each_turn_and_the_last_observations_never_does(self) -> None:
        api = Api()
        seen: list[tuple[str, int]] = []
        _record(api, seen)
        for kind in (OwnUnitCreatedEvent, AnyUnitDiedEvent):
            api.on(kind)(lambda event: seen.append((type(event).__name__, event.step)))

        def game() -> list[sc2api_pb2.Response]:
            marine, scv = make_unit(1, build_progress=1.0), make_unit(2, build_progress=1.0)
            return _game_of(
                make_observation(0, units=[marine]),
                make_observation(2, units=[marine, scv]),
                make_observation(4, (1, Result.VICTORY), units=[scv], dead=(1,)),
            )

        api.play(_joined(*game())[0], steps_per_turn=2)
        first = list(seen)
        seen.clear()
        api.play(_joined(*game())[0], steps_per_turn=2)
        assert (
            first
            == seen
            == [
                ("GameStartEvent", 0),
                ("TurnStartEvent", 0),
                ("OwnUnitCreatedEvent", 0),
                ("TurnEvent", 0),
                ("TurnStartEvent", 2),
                ("OwnUnitCreatedEvent", 2),
                ("TurnEvent", 2),
                ("GameEndEvent", 4),
            ]
        )

    def test_a_handler_that_raises_ends_the_game_by_raising(self) -> None:
        client, _ = _joined(*_game(0, 2, 4))
        api = Api()

        @api.on(TurnEvent, at_step=2)
        def failing(event: TurnEvent) -> None:
            raise RuntimeError("the handler failed")

        with pytest.raises(RuntimeError, match="the handler failed"):
            api.play(client, steps_per_turn=2)
        assert api.step == 2
        assert api.result is None

    def test_after_a_handler_raises_no_game_gives_an_event_its_step(self) -> None:
        client, _ = _joined(*_game(0, 2, 4))
        api = Api()

        @api.on(TurnEvent, at_step=2)
        def failing(event: TurnEvent) -> None:
            raise RuntimeError("the handler failed")

        with pytest.raises(RuntimeError, match="the handler failed"):
            api.play(client, steps_per_turn=2)
        with pytest.raises(ValueError, match="no game is being played"):
            api.emit(TurnEvent())


_REPLAY = b"MPQ\x1b and the rest of a replay"
# The game answers a save with its status: in game while the game goes on, ended after it (in game).
_SAVED_IN_GAME = make_response(Status.IN_GAME, save_replay=sc2api_pb2.ResponseSaveReplay(data=_REPLAY))
_SAVED_AFTER_THE_END = make_response(Status.ENDED, save_replay=sc2api_pb2.ResponseSaveReplay(data=_REPLAY))


@pytest.fixture
def warnings_logged() -> Iterator[list[str]]:
    """The warnings logged while the test runs."""
    messages: list[str] = []
    sink = logger.add(messages.append, format="{message}", level="WARNING")
    yield messages
    logger.remove(sink)


def _requests_asked(transport: FakeTransport) -> list[str | None]:
    """The kind of each request `transport` was sent, in order."""
    return [request.WhichOneof("request") for request in transport.requests]


class TestSavingAReplay:
    def test_a_game_that_ends_is_saved_once_it_has_ended(self, tmp_path: Path) -> None:
        client, transport = _joined(*_game(0, 2), _SAVED_AFTER_THE_END)
        path = tmp_path / "game.SC2Replay"
        assert Api().play(client, steps_per_turn=2, save_replay_to=path) is Result.VICTORY
        assert path.read_bytes() == _REPLAY
        assert _requests_asked(transport)[-2:] == ["observation", "save_replay"]

    def test_a_game_the_bot_leaves_is_saved_before_it_leaves(self, tmp_path: Path) -> None:
        """The game gives no replay to a client that has left (in game)."""
        client, transport = _joined(
            *_game(0, ending=None, stepped=False), _SAVED_IN_GAME, make_response(Status.LAUNCHED)
        )
        api = Api()
        api.on(TurnEvent)(lambda _: api.leave_game())
        path = tmp_path / "game.SC2Replay"
        assert api.play(client, save_replay_to=str(path)) is Result.DEFEAT
        assert path.read_bytes() == _REPLAY
        assert _requests_asked(transport)[-3:] == ["observation", "save_replay", "leave_game"]

    def test_a_game_called_a_tie_at_its_time_limit_is_saved(self, tmp_path: Path) -> None:
        client, _ = _joined(*_game(0, ending=None, stepped=False), _SAVED_IN_GAME)
        path = tmp_path / "game.SC2Replay"
        assert Api().play(client, time_limit=0, save_replay_to=path) is Result.TIE
        assert path.read_bytes() == _REPLAY

    def test_a_game_a_handler_ends_by_raising_is_saved_and_the_error_raised(self, tmp_path: Path) -> None:
        client, _ = _joined(*_game(0, ending=None, stepped=False), _SAVED_IN_GAME)
        api = Api()

        @api.on(TurnEvent)
        def fail(event: TurnEvent) -> None:
            raise RuntimeError("the bot's own bug")

        path = tmp_path / "game.SC2Replay"
        with pytest.raises(RuntimeError, match="the bot's own bug"):
            api.play(client, save_replay_to=path)
        assert path.read_bytes() == _REPLAY

    def test_a_save_the_game_refuses_is_logged_and_the_game_ends_as_it_did(
        self, tmp_path: Path, warnings_logged: list[str]
    ) -> None:
        client, _ = _joined(*_game(0, 2), make_response(Status.LAUNCHED, error=["Not in a game"]))
        path = tmp_path / "game.SC2Replay"
        assert Api().play(client, steps_per_turn=2, save_replay_to=path) is Result.VICTORY
        assert not path.exists()
        assert any("No replay was saved" in message and "Not in a game" in message for message in warnings_logged)

    def test_a_save_that_fails_after_an_error_leaves_the_error_raised(
        self, tmp_path: Path, warnings_logged: list[str]
    ) -> None:
        refused = make_response(Status.LAUNCHED, error=["Not in a game"])
        client, _ = _joined(*_game(0, ending=None, stepped=False), refused)
        api = Api()

        @api.on(TurnEvent)
        def fail(event: TurnEvent) -> None:
            raise RuntimeError("the bot's own bug")

        with pytest.raises(RuntimeError, match="the bot's own bug"):
            api.play(client, save_replay_to=tmp_path / "game.SC2Replay")
        assert any("No replay was saved" in message for message in warnings_logged)

    @pytest.mark.parametrize(
        ("replay", "error"),
        [("missing/game.SC2Replay", FileNotFoundError), (".", IsADirectoryError)],
        ids=["no folder", "a folder"],
    )
    def test_a_replay_with_no_file_to_go_in_is_refused_before_the_game_starts(
        self, tmp_path: Path, replay: str, error: type[OSError]
    ) -> None:
        client, transport = _joined(*_game(0, 2))
        with pytest.raises(error, match="to save the replay into"):
            Api().play(client, save_replay_to=tmp_path / replay)
        assert _requests_asked(transport) == ["join_game"]

    def test_run_local_saves_it(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        transport = FakeTransport(
            make_response(create_game=sc2api_pb2.ResponseCreateGame()),
            make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=1)),
            *_game(0, 2),
            _SAVED_AFTER_THE_END,
            make_response(),
            make_response(),
        )
        _no_real_game(monkeypatch, transport)
        path = tmp_path / "game.SC2Replay"
        run_local(_somewhere(), Api(), race=Race.TERRAN, steps_per_turn=2, save_replay_to=path)
        assert path.read_bytes() == _REPLAY

    def test_run_local_refuses_a_replay_with_no_file_to_go_in_before_the_game_starts(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        launched: list[bool] = []
        monkeypatch.setattr(GameProcess, "launch", classmethod(lambda cls, *args, **kwargs: launched.append(True)))
        with pytest.raises(FileNotFoundError, match="to save the replay into"):
            run_local(_somewhere(), Api(), race=Race.TERRAN, save_replay_to=tmp_path / "missing" / "game.SC2Replay")
        assert not launched

    def test_run_ladder_saves_it(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        transport = FakeTransport(
            make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=1)),
            *_game(0, 2),
            _SAVED_AFTER_THE_END,
            make_response(),
        )
        monkeypatch.setattr(WebSocketTransport, "connect", classmethod(lambda cls, url, **kwargs: transport))
        path = tmp_path / "game.SC2Replay"
        run_ladder(Api(), race=Race.TERRAN, host="127.0.0.1", port=8000, steps_per_turn=2, save_replay_to=path)
        assert path.read_bytes() == _REPLAY


class TestRunningLocally:
    def test_the_bot_is_a_bare_slot_and_the_opponent_carries_how_it_plays(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A participant slot carries no race: the join settles who fills it."""
        transport = _local_transport()
        _no_real_game(monkeypatch, transport)

        run_local(_somewhere(), Api(), Computer(race=Race.ZERG), race=Race.TERRAN, name="NachOS", steps_per_turn=2)

        setups = transport.requests[0].create_game.player_setup
        assert [setup.type for setup in setups] == [sc2api_pb2.Participant, sc2api_pb2.Computer]
        assert not setups[0].HasField("race")
        assert setups[1].race == Race.ZERG.value

    def test_a_bot_without_an_opponent_plays_the_map_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = _local_transport()
        _no_real_game(monkeypatch, transport)
        run_local(_somewhere(), Api(), race=Race.TERRAN, steps_per_turn=2)
        setups = transport.requests[0].create_game.player_setup
        assert [setup.type for setup in setups] == [sc2api_pb2.Participant]

    def test_the_join_carries_the_race_and_the_name_the_bot_plays_under(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = _local_transport()
        _no_real_game(monkeypatch, transport)
        run_local(_somewhere(), Api(), Computer(), race=Race.TERRAN, name="NachOS", steps_per_turn=2)
        joined = transport.requests[1].join_game
        assert joined.race == Race.TERRAN.value
        assert joined.player_name == "NachOS"

    def test_the_client_this_library_started_is_stopped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = _local_transport()
        game = _no_real_game(monkeypatch, transport)
        result = run_local(_somewhere(), Api(), Computer(), race=Race.TERRAN, steps_per_turn=2)
        assert result is Result.VICTORY
        assert any(request.HasField("quit") for request in transport.requests)
        assert transport.closed
        assert game.terminated

    def test_a_game_that_could_not_be_created_raises_why_and_is_still_torn_down(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The game refuses to leave a game it never started; that refusal must not hide why creation failed."""
        refusal = sc2api_pb2.ResponseCreateGame(error=sc2api_pb2.ResponseCreateGame.InvalidMapPath)
        transport = FakeTransport(
            make_response(Status.LAUNCHED, create_game=refusal),
            make_response(Status.LAUNCHED, error=["A game has not been started yet"]),
            make_response(Status.QUIT),
        )
        game = _no_real_game(monkeypatch, transport)
        with pytest.raises(ProtocolError, match="InvalidMapPath"):
            run_local(_somewhere(), Api(), Computer(), race=Race.TERRAN)
        assert any(request.HasField("quit") for request in transport.requests)
        assert transport.closed
        assert game.terminated

    def test_the_recording_is_finished_even_when_leaving_fails(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        transport = FakeTransport(
            make_response(create_game=sc2api_pb2.ResponseCreateGame()),
            make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=1)),
            *_game(0, 2),
            make_response(error=["Something no game has said yet"]),
        )
        _no_real_game(monkeypatch, transport)
        path = tmp_path / "game.sc2rec"
        with pytest.raises(ProtocolError, match="Something no game has said yet"):
            run_local(_somewhere(), Api(), Computer(), race=Race.TERRAN, steps_per_turn=2, record_to=path)
        assert transport.closed
        assert [exchange.request.WhichOneof("request") for exchange in Recording(path)][-2:] == [
            "observation",
            "leave_game",
        ]

    @pytest.mark.parametrize(
        ("recording", "error"),
        [("missing/game.sc2rec", FileNotFoundError), (".", IsADirectoryError)],
        ids=["no folder", "a folder"],
    )
    def test_a_recording_with_no_file_to_go_in_is_refused_before_the_game_starts(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, recording: str, error: type[OSError]
    ) -> None:
        launched: list[bool] = []
        monkeypatch.setattr(GameProcess, "launch", classmethod(lambda cls, *args, **kwargs: launched.append(True)))
        with pytest.raises(error, match="to record into"):
            run_local(_somewhere(), Api(), race=Race.TERRAN, record_to=str(tmp_path / recording))
        assert not launched

    def test_a_recording_named_by_a_string_is_written(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _no_real_game(monkeypatch, _local_transport())
        path = tmp_path / "game.sc2rec"
        assert run_local(_somewhere(), Api(), race=Race.TERRAN, steps_per_turn=2, record_to=str(path)) is (
            Result.VICTORY
        )
        assert [exchange.request.WhichOneof("request") for exchange in Recording(path)][:2] == [
            "create_game",
            "join_game",
        ]


class TestRunningOnALadder:
    def test_the_join_carries_the_ports_the_ladder_handed_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = _ladder_transport()
        monkeypatch.setattr(WebSocketTransport, "connect", classmethod(lambda cls, url, **kwargs: transport))

        result = run_ladder(Api(), race=Race.TERRAN, host="127.0.0.1", port=8000, start_port=1000, steps_per_turn=2)

        assert result is Result.VICTORY
        joined = transport.requests[0].join_game
        assert (joined.server_ports.game_port, joined.server_ports.base_port) == (1002, 1003)
        assert [(pair.game_port, pair.base_port) for pair in joined.client_ports] == [(1004, 1005)]

    def test_the_join_carries_the_race_and_the_name_the_bot_plays_under(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = _ladder_transport()
        monkeypatch.setattr(WebSocketTransport, "connect", classmethod(lambda cls, url, **kwargs: transport))
        run_ladder(Api(), race=Race.PROTOSS, name="NachOS", host="127.0.0.1", port=8000, steps_per_turn=2)
        joined = transport.requests[0].join_game
        assert joined.race == Race.PROTOSS.value
        assert joined.player_name == "NachOS"

    def test_the_opponents_ladder_id_is_the_enemys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = _ladder_transport()
        monkeypatch.setattr(WebSocketTransport, "connect", classmethod(lambda cls, url, **kwargs: transport))
        api = Api()
        run_ladder(api, race=Race.TERRAN, host="127.0.0.1", port=8000, enemy_ladder_id="5a1f", steps_per_turn=2)
        assert api.enemy.ladder_id == "5a1f"

    def test_no_game_is_created_because_the_ladder_already_made_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = _ladder_transport()
        monkeypatch.setattr(WebSocketTransport, "connect", classmethod(lambda cls, url, **kwargs: transport))
        run_ladder(Api(), race=Race.TERRAN, host="127.0.0.1", port=8000, steps_per_turn=2)
        assert not any(request.HasField("create_game") for request in transport.requests)
        assert not transport.requests[0].join_game.HasField("server_ports")

    def test_the_client_the_ladder_started_is_left_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The ladder owns the process it started and decides when it stops."""
        transport = _ladder_transport()
        monkeypatch.setattr(WebSocketTransport, "connect", classmethod(lambda cls, url, **kwargs: transport))
        run_ladder(Api(), race=Race.TERRAN, host="127.0.0.1", port=8000, steps_per_turn=2)
        assert not any(request.HasField("quit") for request in transport.requests)
        assert any(request.HasField("leave_game") for request in transport.requests)
        assert transport.closed

    def test_a_recording_that_fails_to_open_closes_the_connection(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        transport = _ladder_transport()
        monkeypatch.setattr(WebSocketTransport, "connect", classmethod(lambda cls, url, **kwargs: transport))

        def refuse_to_open(*_: object) -> None:
            raise PermissionError("the file cannot be opened")

        monkeypatch.setattr("sc2nachos.run.RecordingTransport", refuse_to_open)
        with pytest.raises(PermissionError):
            run_ladder(Api(), race=Race.TERRAN, host="127.0.0.1", port=8000, record_to=tmp_path / "game.sc2rec")
        assert transport.closed

    @pytest.mark.parametrize("file_for", ["recording", "replay"])
    def test_a_file_with_no_folder_to_go_in_is_refused_before_connecting(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, file_for: str
    ) -> None:
        connected: list[str] = []
        monkeypatch.setattr(
            WebSocketTransport, "connect", classmethod(lambda cls, url, **kwargs: connected.append(url))
        )
        missing = tmp_path / "missing" / "x"
        with pytest.raises(FileNotFoundError, match="there is no folder"):
            run_ladder(
                Api(),
                race=Race.TERRAN,
                host="127.0.0.1",
                port=8000,
                record_to=missing if file_for == "recording" else None,
                save_replay_to=missing if file_for == "replay" else None,
            )
        assert not connected

    def test_the_connection_is_closed_even_when_leaving_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = FakeTransport(
            make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=1)),
            *_game(0, 2),
            make_response(error=["Something no game has said yet"]),
        )
        monkeypatch.setattr(WebSocketTransport, "connect", classmethod(lambda cls, url, **kwargs: transport))
        with pytest.raises(ProtocolError, match="Something no game has said yet"):
            run_ladder(Api(), race=Race.TERRAN, host="127.0.0.1", port=8000, steps_per_turn=2)
        assert transport.closed


@pytest.mark.integration
class TestAgainstTheRealGame:
    """Run with `pytest -m integration`. Plays whole games, so it is slow and needs the game installed."""

    def test_a_bare_api_plays_a_full_game_and_the_recording_replays_it(self, tmp_path: Path) -> None:
        try:
            MapFile.find(_LADDER_MAP)
        except MapNotFoundError as missing:
            pytest.skip(str(missing))

        path = tmp_path / "game.sc2rec"
        opponent = Computer(race=Race.ZERG, difficulty=Difficulty.VERY_HARD)
        api = Api()
        result = run_local(
            _LADDER_MAP,
            api,
            opponent,
            race=Race.TERRAN,
            name="NachOS",
            steps_per_turn=16,
            record_to=path,
            window=(640, 480),
        )
        assert result in (Result.VICTORY, Result.DEFEAT)
        assert api.time > 60

        # A recording holds the setup too, so replaying it replays the setup.
        replayed = Api()
        client = Client(PlaybackTransport(Recording(path)))
        client.create_game(_LADDER_MAP, [Participant(), opponent])
        client.join_game(Race.TERRAN)
        assert replayed.play(client, steps_per_turn=16) is result
        assert replayed.step == api.step

    def test_a_bare_api_plays_a_full_game_joined_as_on_a_ladder(self) -> None:
        try:
            game_map = MapFile.find(_LADDER_MAP)
        except MapNotFoundError as missing:
            pytest.skip(str(missing))

        port = free_port()
        with GameProcess.launch(port=port, window=(640, 480)) as game:
            # The ladder creates the match and disconnects; the client serves one connection at a time.
            with closing(Client(WebSocketTransport.connect(game.url))) as ladder:
                ladder.create_game(game_map.path, [Participant(), Computer(Race.ZERG, Difficulty.VERY_HARD)])
                with pytest.raises(ConnectionClosedError, match="another connection holds it"):
                    WebSocketTransport.connect(game.url)

            api = Api()
            result = run_ladder(api, race=Race.TERRAN, name="NachOS", host="127.0.0.1", port=port, steps_per_turn=16)
            assert result in (Result.VICTORY, Result.DEFEAT)
            assert api.time > 60

            with closing(Client(WebSocketTransport.connect(game.url))) as ladder:
                ladder.ping()
                assert ladder.status is Status.LAUNCHED


@pytest.mark.integration
def test_in_a_real_game_called_a_tie_at_its_time_limit_the_replay_is_saved(tmp_path: Path) -> None:
    """Run with `pytest -m integration`. Plays ten seconds of a game."""
    try:
        MapFile.find(_LADDER_MAP)
    except MapNotFoundError as missing:
        pytest.skip(str(missing))
    replay = tmp_path / "game.SC2Replay"
    opponent = Computer(Race.ZERG, Difficulty.VERY_EASY)
    result = run_local(
        _LADDER_MAP, Api(), opponent, race=Race.TERRAN, steps_per_turn=16, time_limit=10, save_replay_to=replay
    )
    assert result is Result.TIE
    assert replay.read_bytes()[:4] == b"MPQ\x1b"


@pytest.mark.integration
def test_in_a_real_game_against_the_computer_a_bot_that_leaves_loses() -> None:
    """Run with `pytest -m integration`. Plays a second of a game."""
    try:
        MapFile.find(_LADDER_MAP)
    except MapNotFoundError as missing:
        pytest.skip(str(missing))

    api = Api()

    @api.on(TurnEvent)
    def give_up(event: TurnEvent) -> None:
        if event.step >= 16:
            api.leave_game()

    opponent = Computer(Race.ZERG, Difficulty.VERY_EASY)
    result = run_local(_LADDER_MAP, api, opponent, race=Race.TERRAN, steps_per_turn=8, window=(640, 480))

    assert result is Result.DEFEAT
    assert api.step == 16


@pytest.mark.integration
def test_in_a_real_realtime_game_each_turn_comes_at_or_after_the_step_asked_for() -> None:
    """Run with `pytest -m integration`. Plays some ten seconds of a realtime game, whose turns the game paces."""
    try:
        MapFile.find(_LADDER_MAP)
    except MapNotFoundError as missing:
        pytest.skip(str(missing))

    api, steps = Api(), []
    api.on(TurnEvent)(lambda event: steps.append(event.step))
    opponent = Computer(Race.ZERG, Difficulty.VERY_EASY)
    result = run_local(
        _LADDER_MAP, api, opponent, race=Race.TERRAN, steps_per_turn=8, realtime=True, time_limit=10, window=(640, 480)
    )

    assert result is Result.TIE
    assert len(steps) > 10
    assert all(later - earlier >= 8 for earlier, later in itertools.pairwise(steps))
