"""A StarCraft II client this library started."""

import atexit
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from types import TracebackType
from typing import Self

from loguru import logger

from sc2nachos._errors import NachOSError
from sc2nachos.launch._installation import Installation


class GameLaunchError(NachOSError):
    """The client did not start, or died before it began listening."""


def free_port() -> int:
    """A port nothing is listening on, which the client is asked to take."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return int(port)


def launch_command(
    executable: Path,
    *,
    data_directory: Path,
    temp_directory: Path,
    host: str,
    port: int,
    fullscreen: bool,
    window: tuple[int, int],
    window_position: tuple[int, int] | None,
) -> list[str]:
    """The command line that starts a client listening for this library."""
    command = [
        str(executable),
        "-listen",
        host,
        "-port",
        str(port),
        "-dataDir",
        str(data_directory),
        "-tempDir",
        str(temp_directory),
        "-displayMode",
        str(int(fullscreen)),
    ]
    if not fullscreen:
        command += ["-windowwidth", str(window[0]), "-windowheight", str(window[1])]
        if window_position is not None:
            command += ["-windowx", str(window_position[0]), "-windowy", str(window_position[1])]
    return command


class GameProcess:
    """A StarCraft II client started by this library, and the temporary directory it was given.

    Use it as a context manager, or call `terminate` when the game is over. A process still running at
    interpreter exit is terminated, so a crashed run does not leave a client holding the graphics card.
    """

    def __init__(self, process: "subprocess.Popen[bytes]", *, url: str, temp_directory: Path) -> None:
        """Adopt an already-started client reachable at `url`, owning `temp_directory` until termination."""
        self._process = process
        self._url = url
        self._temp_directory = temp_directory
        atexit.register(self.terminate)

    @classmethod
    def launch(
        cls,
        installation: Installation | None = None,
        *,
        host: str = "127.0.0.1",
        port: int | None = None,
        base_build: int | None = None,
        fullscreen: bool = False,
        window: tuple[int, int] = (1024, 768),
        window_position: tuple[int, int] | None = None,
    ) -> Self:
        """Start a client and return it once it is listening."""
        installation = installation or Installation.find()
        # Resolved before anything is created, so a build that does not exist leaves nothing to clean up.
        executable = installation.executable(base_build=base_build)
        port = port or free_port()
        temp_directory = Path(tempfile.mkdtemp(prefix="sc2nachos-"))
        command = launch_command(
            executable,
            data_directory=installation.base,
            temp_directory=temp_directory,
            host=host,
            port=port,
            fullscreen=fullscreen,
            window=window,
            window_position=window_position,
        )
        logger.info("Starting StarCraft II on port {}", port)
        try:
            process = subprocess.Popen(command, cwd=installation.working_directory, stderr=subprocess.DEVNULL)
        except OSError as error:
            shutil.rmtree(temp_directory, ignore_errors=True)
            raise GameLaunchError(f"could not start {command[0]}: {error}") from error

        game = cls(process, url=f"ws://{host}:{port}/sc2api", temp_directory=temp_directory)
        try:
            game.wait_until_listening(host=host, port=port)
        except BaseException:
            game.terminate()
            raise
        return game

    @property
    def url(self) -> str:
        """The websocket address the client is listening on."""
        return self._url

    @property
    def is_running(self) -> bool:
        """Whether the client is still alive."""
        return self._process.poll() is None

    def wait_until_listening(self, *, host: str, port: int, timeout: float = 180) -> None:
        """Block until the client accepts connections, and raise if it dies or never does."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_running:
                raise GameLaunchError(f"StarCraft II exited with code {self._process.returncode} before listening")
            try:
                with socket.create_connection((host, port), timeout=1):
                    logger.info("StarCraft II is listening on {}", self._url)
                    return
            except OSError:
                time.sleep(0.2)
        raise GameLaunchError(f"StarCraft II did not listen on port {port} within {timeout:.0f} seconds")

    def terminate(self, *, timeout: float = 5) -> None:
        """Stop the client and remove its temporary directory. Doing this twice is not an error."""
        atexit.unregister(self.terminate)
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning("StarCraft II ignored the request to stop; killing it")
                self._process.kill()
                self._process.wait()
        shutil.rmtree(self._temp_directory, ignore_errors=True)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.terminate()
