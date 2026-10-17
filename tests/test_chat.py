"""Chat a bot sends: what goes out with the turn's request, what is refused, and what comes back."""

import pytest
from s2clientprotocol import error_pb2, sc2api_pb2

from sc2nachos import Api, NotPlayingError
from sc2nachos.events import ChatEvent, TurnEvent
from sc2nachos.ids import AbilityId
from sc2nachos.launch import MapFile, MapNotFoundError
from sc2nachos.match import Computer, Difficulty, Race, Result
from sc2nachos.protocol import Client, Status
from sc2nachos.run import ApiBot, run_local
from support import FakeTransport, make_game_info, make_observation, make_response, make_unit

_BROADCAST = sc2api_pb2.ActionChat.Broadcast
_TEAM = sc2api_pb2.ActionChat.Team


def _two_turns(api: Api, *, answers: int) -> FakeTransport:
    """Play a game of two turns of 2 steps with `api`, the game answering the first turn's request with `answers`
    results, and return its transport."""
    marine = make_unit(1)
    transport = FakeTransport(
        make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=1)),
        make_response(game_info=make_game_info()),
        make_response(data=sc2api_pb2.ResponseData()),
        make_response(observation=make_observation(0, units=[marine])),
        make_response(action=sc2api_pb2.ResponseAction(result=[error_pb2.Success] * answers)),
        make_response(step=sc2api_pb2.ResponseStep()),
        make_response(Status.ENDED, observation=make_observation(2, (1, Result.VICTORY), units=[marine])),
    )
    client = Client(transport)
    client.join_game(Race.TERRAN)
    api.play(client, steps_per_turn=2)
    return transport


def test_a_turns_chat_goes_out_in_order_after_its_orders_and_the_camera_move() -> None:
    api = Api()

    @api.events.on(TurnEvent)
    def talk(event: TurnEvent) -> None:
        api.chat("gl hf")
        api.orders.issue(api.units.own[0], AbilityId.MOVE, target=(20.0, 21.0))
        api.camera.move_to((5.0, 6.0))
        api.chat("to the team", team_only=True)

    transport = _two_turns(api, answers=4)

    (request,) = [request.action for request in transport.requests if request.HasField("action")]
    kinds = [
        "chat" if action.HasField("action_chat") else "camera" if action.action_raw.HasField("camera_move") else "unit"
        for action in request.actions
    ]
    assert kinds == ["unit", "camera", "chat", "chat"]
    chat = [(action.action_chat.channel, action.action_chat.message) for action in request.actions[2:]]
    assert chat == [(_BROADCAST, "gl hf"), (_TEAM, "to the team")]


@pytest.mark.parametrize(
    ("text", "refused"),
    [("", "cannot be empty"), ("é" * 520, "1040 bytes or more kills the game client, and this one is 1040")],
    ids=["empty", "1040 bytes"],
)
def test_a_message_that_is_empty_or_would_kill_the_game_client_raises(text: str, refused: str) -> None:
    api = Api()
    raised: list[str] = []

    @api.events.on(TurnEvent)
    def talk(event: TurnEvent) -> None:
        with pytest.raises(ValueError, match=refused) as error:
            api.chat(text)
        raised.append(str(error.value))
        api.chat("é" * 519)

    transport = _two_turns(api, answers=1)

    assert len(raised) == 1
    (request,) = [request.action for request in transport.requests if request.HasField("action")]
    assert [len(action.action_chat.message.encode()) for action in request.actions] == [1038]


def test_chat_outside_a_game_raises() -> None:
    with pytest.raises(NotPlayingError):
        Api().chat("gg")


@pytest.mark.integration
def test_in_a_real_game_a_message_comes_back_as_this_players_chat_event() -> None:
    """Run with `pytest -m integration`. Plays a few seconds of a game."""
    try:
        MapFile.find("PylonAIE_v4")
    except MapNotFoundError as missing:
        pytest.skip(str(missing))

    api = Api()
    heard: list[tuple[int, str]] = []

    @api.events.on(TurnEvent)
    def talk(event: TurnEvent) -> None:
        if event.step == 0:
            api.chat("gl hf ✓")

    @api.events.on(ChatEvent)
    def listen(event: ChatEvent) -> None:
        heard.append((event.player_id, event.text))

    opponent = Computer(Race.ZERG, Difficulty.VERY_EASY)
    run_local("PylonAIE_v4", ApiBot(api, Race.TERRAN), opponent, steps_per_turn=8, time_limit=5, window=(640, 480))

    assert heard == [(1, "gl hf ✓")]
