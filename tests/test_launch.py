"""Finding and starting StarCraft II, without StarCraft II."""

import socket
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from sc2nachos.launch import (
    MINIMUM_BASE_BUILD,
    GameLaunchError,
    GameProcess,
    GameVersionError,
    Installation,
    InstallationNotFoundError,
    MapFile,
    MapNotFoundError,
    UnsupportedPlatformError,
    free_port,
)
from sc2nachos.launch._game_process import launch_command
from sc2nachos.match import Computer, Difficulty, Participant, Race
from sc2nachos.protocol import Client, PlaybackTransport, RecordingTransport, Status, WebSocketTransport

# A map from the current AIE ladder pool, for the test games.
_LADDER_MAP = "PylonAIE_v4"


def make_install(
    root: Path, *builds: int, maps: str = "Maps", map_files: Sequence[str] = (), system: str = "Windows"
) -> Installation:
    """A directory tree shaped like an installation, with empty files for `builds` and `map_files`."""
    for build in builds:
        version = root / "Versions" / f"Base{build}"
        version.mkdir(parents=True)
        (version / "SC2_x64.exe").write_bytes(b"")
        (version / "SC2_x64").write_bytes(b"")
    (root / maps).mkdir(parents=True, exist_ok=True)
    for name in map_files:
        game_map = root / maps / name
        game_map.parent.mkdir(parents=True, exist_ok=True)
        game_map.write_bytes(b"")
    return Installation(root, system)


@pytest.fixture
def sleeper() -> Iterator["subprocess.Popen[bytes]"]:
    """A process that outlives the test unless something stops it."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield process
    if process.poll() is None:
        process.kill()
        process.wait()


class TestFinding:
    def test_sc2path_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SC2PATH", str(tmp_path))
        assert Installation.find(system="Windows").base == tmp_path

    @pytest.mark.parametrize(
        ("system", "location", "separator"),
        [
            ("Windows", "Documents/StarCraft II/ExecuteInfo.txt", "\\"),
            ("Darwin", "Library/Application Support/Blizzard/StarCraft II/ExecuteInfo.txt", "/"),
        ],
    )
    def test_the_launchers_own_record_is_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, system: str, location: str, separator: str
    ) -> None:
        """A non-default install is recorded in ExecuteInfo.txt, which the launcher rewrites as it runs.

        Each platform writes its own path separator, which the host running this test need not share.
        """
        home, install = tmp_path / "home", tmp_path / "games" / "StarCraft II"
        install.mkdir(parents=True)
        record = home / location
        record.parent.mkdir(parents=True)
        executable = separator.join([str(install), "Versions", "Base95841", "SC2_x64.exe"])
        record.write_text(f"executable = {executable}\n", encoding="utf-8")

        monkeypatch.delenv("SC2PATH", raising=False)
        monkeypatch.setenv("USERPROFILE", str(home))
        monkeypatch.setenv("HOME", str(home))
        assert Installation.find(system=system).base == install

    def test_a_missing_installation_says_where_it_looked(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SC2PATH", raising=False)
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(InstallationNotFoundError, match="Applications"):
            Installation.find(system="Darwin")

    def test_an_unsupported_platform_is_named(self) -> None:
        with pytest.raises(UnsupportedPlatformError, match="Java"):
            Installation.find(system="Java")

    def test_an_unsupported_platform_is_refused_at_construction(self, tmp_path: Path) -> None:
        """The platform decides the layout, so an installation cannot be on an unsupported one."""
        with pytest.raises(UnsupportedPlatformError, match="Plan9"):
            Installation(tmp_path, "Plan9")

    def test_an_empty_sc2path_is_not_a_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """`Path("")` is the working directory, which would otherwise pass as an installation."""
        monkeypatch.setenv("SC2PATH", "")
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(InstallationNotFoundError):
            Installation.find(system="Darwin")


class TestVersions:
    def test_the_newest_build_is_the_default(self, tmp_path: Path) -> None:
        install = make_install(tmp_path, 75689, 95841, 93333)
        assert install.builds() == {75689, 93333, 95841}
        assert install.executable().parent.name == "Base95841"

    def test_a_build_can_be_asked_for(self, tmp_path: Path) -> None:
        install = make_install(tmp_path, 75689, 95841, system="Linux")
        assert install.executable(base_build=75689).parent.name == "Base75689"

    def test_an_absent_build_lists_what_is_there(self, tmp_path: Path) -> None:
        install = make_install(tmp_path, 95841)
        with pytest.raises(GameVersionError, match=r"95299 is not installed.*\[95841\]"):
            install.executable(base_build=95299)

    def test_a_build_older_than_the_raw_interface_is_refused(self, tmp_path: Path) -> None:
        install = make_install(tmp_path, MINIMUM_BASE_BUILD - 1)
        with pytest.raises(GameVersionError, match="raw interface"):
            install.executable()

    def test_an_installation_without_versions_is_not_one(self, tmp_path: Path) -> None:
        install = Installation(tmp_path, "Windows")
        assert install.builds() == frozenset()
        with pytest.raises(GameVersionError, match="Base<build>"):
            install.executable()

    def test_directories_that_are_not_builds_are_ignored(self, tmp_path: Path) -> None:
        install = make_install(tmp_path, 95841)
        (tmp_path / "Versions" / "Shaders").mkdir()
        (tmp_path / "Versions" / "Base").mkdir()
        assert install.builds() == {95841}

    def test_the_executable_differs_by_platform(self, tmp_path: Path) -> None:
        assert make_install(tmp_path / "w", 95841, system="Windows").executable().name == "SC2_x64.exe"
        assert make_install(tmp_path / "l", 95841, system="Linux").executable().name == "SC2_x64"
        assert make_install(tmp_path / "d", 95841, system="Darwin").executable().name == "SC2"


class TestLayout:
    def test_either_capitalization_of_the_map_directory_is_found(self, tmp_path: Path) -> None:
        """Blizzard's installers disagree on the capitalization, and only a case-sensitive filesystem cares."""
        assert make_install(tmp_path / "upper", 95841, maps="Maps").maps.is_dir()
        assert make_install(tmp_path / "lower", 95841, maps="maps").maps.is_dir()

    def test_only_windows_needs_a_working_directory(self, tmp_path: Path) -> None:
        assert Installation(tmp_path, "Windows").working_directory == tmp_path / "Support64"
        assert Installation(tmp_path, "Linux").working_directory is None
        assert Installation(tmp_path, "Darwin").working_directory is None


class TestMaps:
    def test_a_map_is_found_by_name(self, tmp_path: Path) -> None:
        install = make_install(tmp_path, map_files=["Ladder2019Season3/AcropolisLE.SC2Map"])
        found = MapFile.find("AcropolisLE", installation=install)
        assert found.name == "AcropolisLE"
        assert found.path == install.maps / "Ladder2019Season3" / "AcropolisLE.SC2Map"

    def test_the_extension_is_optional_and_the_case_is_not_read(self, tmp_path: Path) -> None:
        install = make_install(tmp_path, map_files=["PylonAIE.SC2Map"])
        for asked in ("PylonAIE", "PylonAIE.SC2Map", "pylonaie", "PYLONAIE.sc2map"):
            assert MapFile.find(asked, installation=install).name == "PylonAIE"

    def test_the_shallowest_of_several_copies_wins(self, tmp_path: Path) -> None:
        """Map packs install alongside the maps they replace, so one name can match twice."""
        install = make_install(tmp_path, map_files=["AIE/TorchesAIE.SC2Map", "TorchesAIE.SC2Map"])
        assert MapFile.find("TorchesAIE", installation=install).path == install.maps / "TorchesAIE.SC2Map"

    def test_copies_at_one_depth_are_broken_alphabetically(self, tmp_path: Path) -> None:
        install = make_install(tmp_path, map_files=["b/LeyLinesAIE.SC2Map", "a/LeyLinesAIE.SC2Map"])
        assert MapFile.find("LeyLinesAIE", installation=install).path.parent.name == "a"

    def test_a_missing_map_lists_what_is_there(self, tmp_path: Path) -> None:
        install = make_install(tmp_path, map_files=["AIE/PylonAIE.SC2Map", "Custom/plain64.SC2Map"])
        with pytest.raises(MapNotFoundError, match=r"no map called Acropolis.*plain64"):
            MapFile.find("Acropolis", installation=install)

    def test_a_map_outside_the_installation_needs_no_lookup(self, tmp_path: Path) -> None:
        assert MapFile(tmp_path / "elsewhere" / "Handmade.SC2Map").name == "Handmade"


class TestCommand:
    def _command(self, **overrides: object) -> list[str]:
        arguments: dict = {
            "data_directory": Path("/sc2"),
            "temp_directory": Path("/tmp/x"),
            "host": "127.0.0.1",
            "port": 5000,
            "fullscreen": False,
            "window": (1024, 768),
            "window_position": None,
        }
        arguments.update(overrides)
        return launch_command(Path("/sc2/SC2_x64"), **arguments)

    def test_the_client_is_told_where_to_listen(self) -> None:
        command = self._command()
        assert command[0] == str(Path("/sc2/SC2_x64"))
        assert command[command.index("-listen") + 1] == "127.0.0.1"
        assert command[command.index("-port") + 1] == "5000"

    def test_a_windowed_game_carries_its_size(self) -> None:
        command = self._command(window=(800, 600))
        assert command[command.index("-displayMode") + 1] == "0"
        assert command[command.index("-windowwidth") + 1] == "800"
        assert command[command.index("-windowheight") + 1] == "600"

    def test_a_fullscreen_game_carries_no_window(self) -> None:
        command = self._command(fullscreen=True)
        assert command[command.index("-displayMode") + 1] == "1"
        assert "-windowwidth" not in command

    def test_a_window_position_is_optional(self) -> None:
        assert "-windowx" not in self._command()
        assert self._command(window_position=(20, 30))[-1] == "30"


class TestProcess:
    def test_a_free_port_is_free(self) -> None:
        port = free_port()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))

    def test_terminating_stops_the_client_and_takes_its_directory(
        self, tmp_path: Path, sleeper: "subprocess.Popen[bytes]"
    ) -> None:
        temp_directory = tmp_path / "sc2nachos-test"
        temp_directory.mkdir()
        game = GameProcess(sleeper, url="ws://127.0.0.1:5000/sc2api", temp_directory=temp_directory)
        assert game.is_running
        game.terminate()
        assert not game.is_running
        assert not temp_directory.exists()

    def test_terminating_twice_is_not_an_error(self, tmp_path: Path, sleeper: "subprocess.Popen[bytes]") -> None:
        game = GameProcess(sleeper, url="ws://127.0.0.1:5000/sc2api", temp_directory=tmp_path / "gone")
        game.terminate()
        game.terminate()

    def test_the_context_manager_terminates(self, tmp_path: Path, sleeper: "subprocess.Popen[bytes]") -> None:
        with GameProcess(sleeper, url="ws://127.0.0.1:5000/sc2api", temp_directory=tmp_path / "gone") as game:
            assert game.is_running
        assert not game.is_running

    def test_waiting_returns_once_something_answers(self, tmp_path: Path, sleeper: "subprocess.Popen[bytes]") -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            game = GameProcess(sleeper, url="ws://127.0.0.1/sc2api", temp_directory=tmp_path / "gone")
            game.wait_until_listening(host="127.0.0.1", port=port, timeout=5)
            game.terminate()

    def test_a_client_that_dies_is_not_waited_out(self, tmp_path: Path) -> None:
        process = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
        game = GameProcess(process, url="ws://127.0.0.1:1/sc2api", temp_directory=tmp_path / "gone")
        with pytest.raises(GameLaunchError, match="exited with code 3"):
            game.wait_until_listening(host="127.0.0.1", port=free_port(), timeout=10)

    def test_a_client_that_never_listens_times_out(self, tmp_path: Path, sleeper: "subprocess.Popen[bytes]") -> None:
        game = GameProcess(sleeper, url="ws://127.0.0.1/sc2api", temp_directory=tmp_path / "gone")
        with pytest.raises(GameLaunchError, match="did not listen"):
            game.wait_until_listening(host="127.0.0.1", port=free_port(), timeout=0.5)
        game.terminate()


@pytest.mark.integration
class TestAgainstTheRealGame:
    """Run with `pytest -m integration`. Starts a client, so it is slow and needs the game installed."""

    def test_a_launched_client_answers(self) -> None:
        with GameProcess.launch(window=(640, 480)) as game:
            client = Client(WebSocketTransport.connect(game.url))
            ping = client.ping()
            assert ping.base_build >= MINIMUM_BASE_BUILD
            assert ping.game_version.endswith(str(ping.base_build))
            client.quit()
            client.close()
        assert not game.is_running

    def test_a_created_game_is_joined_and_stepped(self) -> None:
        """One client against one computer: the shortest game that exercises the whole conversation."""
        try:
            game_map = MapFile.find(_LADDER_MAP)
        except MapNotFoundError as missing:
            pytest.skip(str(missing))

        with GameProcess.launch(window=(640, 480)) as game:
            client = Client(WebSocketTransport.connect(game.url))
            opponent = Computer(race=Race.ZERG, difficulty=Difficulty.VERY_EASY)
            client.create_game(game_map.path, [Participant(), opponent])
            assert client.status is Status.INIT_GAME

            assert client.join_game(Race.TERRAN, name="NachOS") == 1
            assert client.in_game

            info = client.game_info()
            assert info.map_name
            # Only this player's race comes back, the one the join asked for.
            assert {player.player_id: player.race_actual for player in info.player_info}[1] == Race.TERRAN.value

            before = client.observation().observation.game_loop
            client.step(16)
            assert client.observation().observation.game_loop >= before + 16

            client.leave_game()
            client.quit()
            client.close()
        assert not game.is_running

    def test_a_recorded_game_replays_exactly(self, tmp_path: Path) -> None:
        """Recordings are the corpus everything above the protocol is tested against, so they must be faithful."""
        try:
            game_map = MapFile.find(_LADDER_MAP)
        except MapNotFoundError as missing:
            pytest.skip(str(missing))

        opponent = Computer(race=Race.ZERG, difficulty=Difficulty.VERY_EASY)
        path = tmp_path / "game.sc2rec"
        with GameProcess.launch(window=(640, 480)) as game:
            recorder = RecordingTransport(WebSocketTransport.connect(game.url), path)
            client = Client(recorder)
            client.create_game(game_map.path, [Participant(), opponent])
            client.join_game(Race.TERRAN, name="NachOS")
            info = client.game_info()
            loops = []
            for _ in range(20):
                loops.append(client.observation().observation.game_loop)
                client.step(16)
            client.leave_game()
            client.quit()
            client.close()

        # Twenty observations are well over a megabyte on the wire, which is why recordings are compressed.
        assert path.stat().st_size < 500_000

        replayed = Client(PlaybackTransport(recorder.recording))
        replayed.create_game(game_map.path, [Participant(), opponent])
        assert replayed.join_game(Race.TERRAN) == 1
        assert replayed.game_info() == info
        for loop in loops:
            assert replayed.observation().observation.game_loop == loop
            replayed.step(16)


class TestLaunchCleansUpAfterItself:
    def test_a_build_that_does_not_resolve_leaves_nothing_behind(self, tmp_path: Path) -> None:
        """The temporary directory is created only once there is something to put in it."""
        temporary = Path(tempfile.gettempdir())
        before = set(temporary.glob("sc2nachos-*"))
        with pytest.raises(GameVersionError):
            GameProcess.launch(make_install(tmp_path, 95841), base_build=75689)
        assert set(temporary.glob("sc2nachos-*")) == before
