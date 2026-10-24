"""Playing a game on a client this library starts, or on one a ladder started."""

import json
import random
from collections.abc import Sequence
from contextlib import ExitStack, closing
from pathlib import Path

from loguru import logger
from s2clientprotocol import sc2api_pb2

from sc2nachos._checks import check_file_to_write
from sc2nachos._command_line_arguments import _CommandLineArguments
from sc2nachos.api import Api, NotPlayingError
from sc2nachos.gamedata._techtree import TECH_TREE
from sc2nachos.launch import GameProcess, Installation, MapFile, MapNotFoundError
from sc2nachos.match import Computer, Participant, Player, Race, Result
from sc2nachos.protocol import (
    Client,
    ConnectionClosedError,
    GamePorts,
    PermissivePlaybackTransport,
    PlaybackTransport,
    Recording,
    RecordingTransport,
    Transport,
    WebSocketTransport,
)


def run_local(
    map_file: MapFile | str,
    api: Api,
    opponent: Computer | None = None,
    *,
    race: Race,
    name: str | None = None,
    steps_per_turn: int = 1,
    realtime: bool = False,
    time_limit: float | None = None,
    random_seed: int | None = None,
    record_to: Path | str | None = None,
    save_replay_to: Path | str | None = None,
    installation: Installation | None = None,
    window: tuple[int, int] = (1024, 768),
) -> Result:
    """Start a game client, create a match on `map_file` for `api` against `opponent`, and play it out.

    `map_file` is a file or the name of one under the installation. The api takes the first slot, playing `race` under
    `name`, and without an `opponent` it plays the map alone. `realtime` creates the game in real time and plays it so.
    `record_to` is a file to record the whole conversation into, and `save_replay_to` one to save the game's replay to,
    as `Api.play` says. Each must be in a folder that exists, or `FileNotFoundError` is raised before the game starts,
    and `IsADirectoryError` if it names a folder. However the game ends, the client is stopped and its temporary
    directory removed. The newest build installed is played, with a warning if it is not the one the tech tree was swept
    on.
    """
    _check_output_files(record_to, save_replay_to)
    installation = installation or Installation.find()
    if isinstance(map_file, str):
        map_file = MapFile.find(map_file, installation=installation)
    # A participant tells the game a client will fill the slot; who fills it is settled at the join.
    players: list[Player] = [Participant()] if opponent is None else [Participant(), opponent]
    base_build = installation.build_to_launch()
    _check_tech_tree_build(base_build)

    with (
        GameProcess.launch(installation, base_build=base_build, window=window) as game,
        closing(_connect(game.url, record_to)) as client,
    ):
        try:
            client.create_game(map_file.path, players, realtime=realtime, random_seed=random_seed)
            client.join_game(race, name=name)
            return api.play(
                client,
                steps_per_turn=steps_per_turn,
                realtime=realtime,
                time_limit=time_limit,
                save_replay_to=save_replay_to,
            )
        finally:
            client.leave_game()
            client.quit()


def run_ladder(
    api: Api,
    *,
    race: Race,
    name: str | None = None,
    host: str,
    port: int,
    start_port: int | None = None,
    enemy_ladder_id: str | None = None,
    steps_per_turn: int = 1,
    realtime: bool = False,
    record_to: Path | str | None = None,
    save_replay_to: Path | str | None = None,
) -> Result:
    """Join the game a ladder has set up for `api`, playing `race` under `name`, and play it out.

    The ladder starts the client, creates the match, and passes the bot the client's address, `start_port` and the
    opponent's `enemy_ladder_id` on the command line. A game against the built-in computer has no `start_port`.
    `realtime` must match the ladder's `--RealTime` flag, as `Api.play` says. There is no time limit: a bot can only
    end a game early by leaving it, which concedes. `record_to` and `save_replay_to` are as for `run_local`.
    """
    _check_output_files(record_to, save_replay_to)
    with closing(_connect(f"ws://{host}:{port}/sc2api", record_to)) as client:
        try:
            ports = GamePorts.from_start_port(start_port) if start_port is not None else None
            client.join_game(race, name=name, ports=ports)
            return api.play(
                client,
                steps_per_turn=steps_per_turn,
                realtime=realtime,
                enemy_ladder_id=enemy_ladder_id,
                save_replay_to=save_replay_to,
            )
        finally:
            # The client is the ladder's, so it is left running.
            client.leave_game()


def run_replay(
    replay: Path | str,
    api: Api,
    *,
    player_id: int,
    steps_per_turn: int = 1,
    time_limit: float | None = None,
    record_to: Path | str | None = None,
    installation: Installation | None = None,
    window: tuple[int, int] = (1024, 768),
) -> Result:
    """Start a game client, play back the replay in the file `replay` for `api` as the player `player_id` saw it, a
    turn every `steps_per_turn` steps, and return how it ended for that player.

    The api sees what that player saw when the game was played (in game): `api.player` is that player, `api.units.own`
    its units, and each action it took comes as an `OwnActionEvent`. The game takes no actions or debug commands in a
    replay, so a turn that orders, chats, draws or moves the camera raises `ProtocolError` once its handlers have run
    (in game). Leaving the replay, or reaching `time_limit` in game seconds, stops it `Result.UNDECIDED`.

    Raises `ValueError` for a `player_id` that is not one of the replay's players, as 0 is not. The replay is played
    on its own build, which must be installed, or `GameVersionError` is raised: the newest build installed reads which
    it is, and a second client is started when that is not the one. `record_to` is a file to record the whole
    conversation into, as for `run_local`, which `run_recording` plays back. However the replay ends, the client is
    stopped and its temporary directory removed.
    """
    _check_output_files(record_to, None)
    replay_data = Path(replay).read_bytes()
    installation = installation or Installation.find()
    newest_build = installation.build_to_launch()
    with ExitStack() as running:
        game = running.enter_context(GameProcess.launch(installation, base_build=newest_build, window=window))
        with closing(Client(WebSocketTransport.connect(game.url))) as reader:
            info = reader.replay_info(replay_data)
        _check_replay_player(info, player_id)
        if info.base_build != newest_build:
            replay_build = installation.build_to_launch(base_build=info.base_build)
            game.terminate()
            game = running.enter_context(GameProcess.launch(installation, base_build=replay_build, window=window))
        _check_tech_tree_build(info.base_build)
        with closing(_connect(game.url, record_to)) as client:
            try:
                client.start_replay(replay_data, player_id=player_id)
                return api.play(client, steps_per_turn=steps_per_turn, time_limit=time_limit)
            finally:
                client.leave_game()
                client.quit()


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


def run_recording(
    recording: Recording | Path | str,
    api: Api,
    *,
    permissive: bool = False,
    time_limit: float | None = None,
    enemy_ladder_id: str | None = None,
    save_replay_to: Path | str | None = None,
) -> Result:
    """Play `api` through the game recorded in `recording`, with no game client, a turn at each recorded observation,
    and return how it ended for the player recorded, or `TIE` at `time_limit`, in game seconds. `enemy_ladder_id` and
    `save_replay_to` are as for `Api.play`.

    By default the bot must ask what was asked when the game was recorded, and gets the game's own answers, as a
    `PlaybackTransport` gives them: a request of another kind raises `ProtocolError`. The game is created if the
    recording created it, and played in real time if it was, as `Recording.created_its_game` and `played_in_real_time`
    say. `time_limit`, `enemy_ladder_id` and `save_replay_to` must be what the game was played with: a game its time
    limit ended raises at its end without it, and so does one the bot left after saving its replay without
    `save_replay_to`, which then holds the replay recorded.

    With `permissive`, any bot plays the recording through, though nothing it does changes what comes next: what it
    asks of its own accord is answered as `PermissivePlaybackTransport` makes it up. It returns `UNDECIDED` if the
    recording stops before the game's end, as one made with a time limit does, and `DEFEAT` if the bot leaves first,
    and saves no replay, logging a save asked for as failed. A request the recording cannot answer raises
    `ProtocolError`.

    A replay played back by `run_replay` is played back again as it was recorded, as the player it was played for,
    by default only: `permissive` raises `ValueError` for one.
    """
    if not isinstance(recording, Recording):
        recording = Recording(Path(recording))
    start_replay_request = recording.start_replay_request
    if start_replay_request is not None and permissive:
        raise ValueError(f"{recording.path} is a replay played back, which plays back only as it was recorded")
    transport: Transport = PermissivePlaybackTransport(recording) if permissive else PlaybackTransport(recording)
    with closing(Client(transport)) as client:
        # A recording answers each kind of request in turn without reading it, so the setup is repeated without its
        # details. A permissive one makes up a create and every step.
        if start_replay_request is not None:
            client.start_replay(start_replay_request.replay_data, player_id=start_replay_request.observed_player_id)
        else:
            if permissive or recording.created_its_game:
                client.create_game("recorded", [Participant(), Computer()])
            client.join_game(Race.RANDOM)
        return api.play(
            client,
            realtime=not permissive and start_replay_request is None and recording.played_in_real_time,
            time_limit=time_limit,
            enemy_ladder_id=enemy_ladder_id,
            save_replay_to=save_replay_to,
        )


def run_from_command_line(
    api: Api,
    argv: Sequence[str] | None = None,
    *,
    race: Race,
    name: str | None = None,
    steps_per_turn: int = 1,
) -> Result:
    """Play one game for `api` as the command line asks, a ladder's or a local one against the built-in computer,
    playing `race` under `name`, a turn every `steps_per_turn` steps, and write how it went.

    With `--GamePort` it joins the game a ladder has set up, as `run_ladder` does, with the ladder's `--LadderServer`
    (127.0.0.1 if not given), `--StartPort` and `--OpponentId`, which `Enemy.ladder_id` reads. It takes the ladder's
    `--ComputerOpponent`, `--ComputerRace` and `--ComputerDifficulty` and uses none of them. A connection the ladder
    closes before the game ends is logged as a warning, and the game's result is `Result.UNDECIDED`.

    Without it, it plays a local game against the built-in computer, as NachOS's match workflow,
    `.github/workflows/match.yml`, asks: `--ComputerRace`, `--ComputerDifficulty` and `--ComputerBuild` as member names
    of `Race`, `Difficulty` and `AIBuild` (`ZERG`, `VERY_EASY`, `RANDOM`; by default `RANDOM`, `VERY_HARD` and
    `RANDOM`), `--Map`, by default one at random from the installation's, and `--TimeLimit` in game seconds. A value
    that names no member stops the run, as an argument argparse refuses does.

    In either, `--RealTime` plays the game in real time, `--ReplayFile` is a file to save its replay to, `--RecordFile`
    one to record the whole conversation with the game into, as `record_to` does, for `run_recording` to play back, and
    `--ResultFile` one to write the JSON the match workflow reads: the result, the map, as its file's name in a local
    game and as its display name in a ladder game, and the game time, each of those two `null` if the ladder closed the
    connection before the game began. An argument of the other kind of game is logged as ignored, and one NachOS does
    not read is left for the bot, listed in the log. `argv` defaults to `sys.argv[1:]`.
    """
    command_line_arguments = _CommandLineArguments(argv)
    command_line_arguments.log_arguments_not_used()
    if command_line_arguments.is_ladder_game:
        result, map_name = _play_ladder_game(
            api, command_line_arguments, race=race, name=name, steps_per_turn=steps_per_turn
        )
    else:
        result, map_name = _play_local_game(
            api, command_line_arguments, race=race, name=name, steps_per_turn=steps_per_turn
        )
    if command_line_arguments.result_file is not None:
        outcome = {
            "result": result.name,
            "map": map_name,
            "game_time": _game_time(api),
        }
        command_line_arguments.result_file.write_text(json.dumps(outcome), encoding="utf-8")
    return result


def _play_ladder_game(
    api: Api, command_line_arguments: _CommandLineArguments, *, race: Race, name: str | None, steps_per_turn: int
) -> tuple[Result, str | None]:
    """Join the ladder's game as `command_line_arguments` say, and return how it ended and the map's display name,
    `None` if the ladder closed the connection before the game began."""
    try:
        result = run_ladder(
            api,
            race=race,
            name=name,
            host=command_line_arguments.ladder_server,
            port=command_line_arguments.game_port,
            start_port=command_line_arguments.start_port,
            enemy_ladder_id=command_line_arguments.opponent_id,
            steps_per_turn=steps_per_turn,
            realtime=command_line_arguments.realtime,
            record_to=command_line_arguments.record_file,
            save_replay_to=command_line_arguments.replay_file,
        )
    except ConnectionClosedError as closed:
        # A ladder ends a game early by closing the connection, as when either bot crashes or fails to start, and its
        # own result says whose fault it was (AvocaDOS, on the ladder).
        logger.warning("The ladder closed the connection before the game ended: {}", closed)
        result = Result.UNDECIDED
    try:
        return result, api.map.name
    except NotPlayingError:
        return result, None


def _game_time(api: Api) -> float | None:
    """The game seconds `api`'s last game lasted, to the millisecond, `None` if it began none."""
    try:
        return round(api.time, 3)
    except NotPlayingError:
        return None


def _play_local_game(
    api: Api, command_line_arguments: _CommandLineArguments, *, race: Race, name: str | None, steps_per_turn: int
) -> tuple[Result, str]:
    """Play a local game against the built-in computer as `command_line_arguments` say, and return how it ended and
    the map file's name."""
    computer_opponent = command_line_arguments.create_computer_opponent()
    installation = Installation.find()
    if command_line_arguments.map_name is not None:
        map_file = MapFile.find(command_line_arguments.map_name, installation=installation)
    else:
        names = MapFile._names(installation)
        if not names:
            raise MapNotFoundError(f"{installation.maps} holds no maps to pick from")
        map_file = MapFile.find(random.choice(names), installation=installation)
    result = run_local(
        map_file,
        api,
        computer_opponent,
        race=race,
        name=name,
        steps_per_turn=steps_per_turn,
        realtime=command_line_arguments.realtime,
        time_limit=command_line_arguments.time_limit,
        record_to=command_line_arguments.record_file,
        save_replay_to=command_line_arguments.replay_file,
        installation=installation,
    )
    return result, map_file.name


def _check_tech_tree_build(base_build: int) -> None:
    """Log a warning if `base_build` is not the tech tree's."""
    if base_build != TECH_TREE.base_build:
        logger.warning(
            "Playing build {}, but the tech tree is build {}'s: what each ability needs, who is offered it, how it "
            "takes a unit's orders and what each upgrade changes come from that build",
            base_build,
            TECH_TREE.base_build,
        )


def _check_output_files(record_to: Path | str | None, save_replay_to: Path | str | None) -> None:
    """Raise `FileNotFoundError` or `IsADirectoryError`, before a game starts, for a file to record into or to save
    the replay into that cannot be written."""
    if record_to is not None:
        check_file_to_write(Path(record_to), "record into")
    if save_replay_to is not None:
        check_file_to_write(Path(save_replay_to), "save the replay into")


def _connect(url: str, record_to: Path | str | None) -> Client:
    """A client connected to the game at `url`, recording the whole conversation to `record_to` if given. A recording
    that fails to open closes the connection."""
    transport: Transport = WebSocketTransport.connect(url)
    if record_to is not None:
        try:
            transport = RecordingTransport(transport, record_to)
        except BaseException:
            transport.close()
            raise
    return Client(transport)
