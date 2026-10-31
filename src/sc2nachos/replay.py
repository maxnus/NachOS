"""Playing a replay back, the game's `.SC2Replay` file, on a client this library starts, or from a recording of one."""

from collections.abc import Iterator
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import final

from loguru import logger
from s2clientprotocol import sc2api_pb2

from sc2nachos._checks import check_output_files, check_time_limit
from sc2nachos.api import Api
from sc2nachos.game_time import GameTime
from sc2nachos.gamedata._techtree import TECH_TREE
from sc2nachos.launch import GameProcess, Installation, MapFile, MapNotFoundError
from sc2nachos.match import Result
from sc2nachos.protocol import Client, PlaybackTransport, Recording, WebSocketTransport
from sc2nachos.protocol._connection import connect_client


def run_replay(
    replay: Path | str,
    api: Api,
    *,
    player_id: int,
    disable_fog: bool = False,
    steps_per_turn: int = 1,
    time_limit: GameTime | None = None,
    record_to: Path | str | None = None,
    installation: Installation | None = None,
    window: tuple[int, int] = (1024, 768),
) -> Result:
    """Start a game client, play back the replay in the file `replay` for `api` as the player `player_id` saw it, a
    turn every `steps_per_turn` steps, and return how it ended for that player.

    The api sees what that player saw when the game was played (in game): `api.player` is that player, `api.units.own`
    its units, and each action it took comes as an `OwnActionEvent`. With `disable_fog`, it sees that too, and
    `api.all_units` holds every unit besides. The game takes no actions or debug commands in a replay, so a turn that
    orders, chats, draws or moves the camera raises `ProtocolError` once its handlers have run (in game). Leaving the
    replay, or reaching `time_limit`, stops it `Result.UNDECIDED`.

    The replay is played back as `open_replay` plays it, on its own build and with its map, and refused where that
    says. `record_to` is a file to record the whole conversation into, as for `run_local`, which `run_recording` plays
    back. However the replay ends, the client is stopped and its temporary directory removed.
    """
    check_time_limit(time_limit)
    with open_replay(
        replay,
        player_id=player_id,
        disable_fog=disable_fog,
        record_to=record_to,
        installation=installation,
        window=window,
    ) as playback:
        TECH_TREE.warn_if_not_swept_on(playback.info.base_build)
        return api.play(playback.client, steps_per_turn=steps_per_turn, time_limit=time_limit)


@final
@dataclass(frozen=True, slots=True)
class ReplayPlayback:
    """A replay being played back, on a game client or from a recording, for its observations to be read straight from
    the protocol."""

    client: Client
    """The connection, in the replay at its start, before its first observation. It answers what the game answers in a
    game, observations, steps, the game's info and data among them, as the player played for saw the game, and refuses
    every action and debug command (in game)."""
    info: sc2api_pb2.ResponseReplayInfo
    """What the game read from the replay: its map, its length, the build it was played on and its players."""


@contextmanager
def open_replay(
    replay: Path | str,
    *,
    player_id: int,
    disable_fog: bool = False,
    record_to: Path | str | None = None,
    installation: Installation | None = None,
    window: tuple[int, int] = (1024, 768),
) -> Iterator[ReplayPlayback]:
    """Start a game client, play back the replay in the file `replay` on it as the player `player_id` saw it, and give
    the playback, for its observations to be read straight from the protocol. `run_replay` plays one for an api.

    Raises `ValueError` for a `player_id` that is not one of the replay's players, as 0 is not. The replay is played
    on its own build, which must be installed, or `GameVersionError` is raised: the newest build installed reads which
    it is, and a second client is started when that is not the one. The game opens the replay's map by the path the
    replay names, which an AI Arena replay gives as a bare file name the game does not find (in game); so unless the
    path is an absolute path to a file here, the map it names under the installation's maps goes with the replay, or
    else the map of its name there. The game reports the raw interface, with cloaked units, burrowed units' shadows
    and the placeholders of structures ordered shown, on the whole map. `disable_fog` lists every unit, as
    `Client.start_replay` says.

    `record_to` is a file to record the whole conversation into, the replay's info included, which
    `open_replay_recording` and `run_recording` play back; it is complete once the playback ends. However the
    playback ends, the client leaves the replay and is stopped, and its temporary directory removed. Each playback
    starts a client of its own, so two can be open at once, one for each player.
    """
    check_output_files(record_to, None)
    replay_data = Path(replay).read_bytes()
    installation = installation or Installation.find()
    newest_build = installation.build_to_launch()
    with ExitStack() as running:
        game = running.enter_context(GameProcess.launch(installation, base_build=newest_build, window=window))
        with closing(Client(WebSocketTransport.connect(game.url))) as info_client:
            info_on_the_newest_build = info_client.replay_info(replay_data)
        _check_replay_player(info_on_the_newest_build, player_id)
        if info_on_the_newest_build.base_build != newest_build:
            replay_build = installation.build_to_launch(base_build=info_on_the_newest_build.base_build)
            game.terminate()
            game = running.enter_context(GameProcess.launch(installation, base_build=replay_build, window=window))
        map_data = _map_bytes_the_game_needs(info_on_the_newest_build.local_map_path, installation)
        with closing(connect_client(game.url, record_to)) as client:
            try:
                # Asked again on the connection recorded, so that a recording holds it.
                info = client.replay_info(replay_data)
                client.start_replay(replay_data, player_id=player_id, disable_fog=disable_fog, map_data=map_data)
                yield ReplayPlayback(client, info)
            finally:
                client.leave_game()
                client.quit()


@contextmanager
def open_replay_recording(recording: Recording | Path | str) -> Iterator[ReplayPlayback]:
    """Play back `recording`, a recording of a replay played back that `open_replay` or `run_replay` made, with no game
    client, and give the playback, as `open_replay` gives it. It answers what was asked when it was recorded, in the
    same order, and raises `ProtocolError` when asked for a request of another kind, as `PlaybackTransport` does.

    Raises `ValueError` for a recording of a game played rather than a replay played back, and for one of a replay
    made before NachOS recorded a replay's info, which plays back once recorded again.
    """
    if not isinstance(recording, Recording):
        recording = Recording(Path(recording))
    start_replay_request = _start_of_recorded_replay(recording)
    with closing(Client(PlaybackTransport(recording))) as client:
        # A recording answers each kind of request in turn without reading it, so the setup is repeated without its
        # details.
        info = client.replay_info(start_replay_request.replay_data)
        client.start_replay(
            start_replay_request.replay_data,
            player_id=start_replay_request.observed_player_id,
            disable_fog=start_replay_request.disable_fog,
            map_data=start_replay_request.map_data if start_replay_request.HasField("map_data") else None,
        )
        yield ReplayPlayback(client, info)


def _start_of_recorded_replay(recording: Recording) -> sc2api_pb2.RequestStartReplay:
    """The request that started the replay played back in `recording`, which must first read the replay's info.
    Raises `ValueError` for a recording of a game played, or of a replay made before the info was recorded."""
    start_replay_request = recording.start_replay_request
    if start_replay_request is None:
        raise ValueError(f"{recording.path} is a game played, not a replay played back")
    with closing(iter(recording)) as exchanges:
        first = next(exchanges)
    if not first.request.HasField("replay_info"):
        raise ValueError(
            f"{recording.path} holds no replay info, as a recording made before NachOS recorded one does: record it "
            "again"
        )
    return start_replay_request


def _map_bytes_the_game_needs(replay_map_path: str, installation: Installation) -> bytes | None:
    """The bytes of the map of a replay whose map is at `replay_map_path`, as its info gives it, for the game to play
    it on: the file a relative path names under `installation`'s maps, and otherwise the map of its name there. `None`
    where it is an absolute path to a file, which the game opens, and where no map of its name is installed, for the
    game to try the path itself."""
    if not replay_map_path:
        return None
    map_path = Path(replay_map_path)
    if map_path.is_absolute():
        if map_path.is_file():
            return None
    elif (under_the_maps := installation.maps / map_path).is_file():
        # That very file, rather than one of its name elsewhere, as a map pack installs beside the maps it replaces.
        return under_the_maps.read_bytes()
    # A replay played on Windows names its map with backslashes, which a Windows path reads on every platform.
    map_name = PureWindowsPath(replay_map_path).stem
    try:
        map_file = MapFile.find(map_name, installation=installation)
    except MapNotFoundError:
        logger.warning("The replay's map, {}, is not under {}; the game may not open it", map_name, installation.maps)
        return None
    return map_file.path.read_bytes()


def _check_replay_player(info: sc2api_pb2.ResponseReplayInfo, player_id: int) -> None:
    """Raise `ValueError` unless `player_id` is one of the players, observers aside, of the replay `info` describes."""
    players = sorted(
        entry.player_info.player_id
        for entry in info.player_info
        if entry.player_info.type != sc2api_pb2.PlayerType.Observer
    )
    if player_id not in players:
        listed = " and ".join(str(player) for player in players) or "none"
        raise ValueError(f"the replay's players are {listed}, and {player_id} is not one of them")
