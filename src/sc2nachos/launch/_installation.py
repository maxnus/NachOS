"""Where StarCraft II lives on this machine."""

import os
import platform
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from loguru import logger

from sc2nachos._errors import NachOSError

# The oldest build that speaks the raw interface this library is built on.
MINIMUM_BASE_BUILD = 55958

_VERSIONS = "Versions"
_BASE_PREFIX = "Base"


class UnsupportedPlatformError(NachOSError):
    """StarCraft II does not run on the platform asked for."""


class InstallationNotFoundError(NachOSError):
    """StarCraft II is not installed where this platform keeps it, and `SC2PATH` does not say otherwise."""


class GameVersionError(NachOSError):
    """The installation holds no version this library can drive."""


@dataclass(frozen=True, slots=True)
class _Platform:
    """The parts of an installation's layout that differ by operating system."""

    default_base: str
    executable: str
    working_directory: str | None
    execute_info: str | None


_PLATFORMS = {
    "Windows": _Platform(
        default_base="C:/Program Files (x86)/StarCraft II",
        executable="SC2_x64.exe",
        working_directory="Support64",
        execute_info="Documents/StarCraft II/ExecuteInfo.txt",
    ),
    "Linux": _Platform(
        default_base="~/StarCraftII",
        executable="SC2_x64",
        working_directory=None,
        execute_info=None,
    ),
    "Darwin": _Platform(
        default_base="/Applications/StarCraft II",
        executable="SC2.app/Contents/MacOS/SC2",
        working_directory=None,
        execute_info="Library/Application Support/Blizzard/StarCraft II/ExecuteInfo.txt",
    ),
}


def _settings(system: str) -> _Platform:
    """The layout of an installation on `system`. Raises `UnsupportedPlatformError` for any other system."""
    try:
        return _PLATFORMS[system]
    except KeyError:
        raise UnsupportedPlatformError(f"{system} is not a platform StarCraft II runs on") from None


@dataclass(frozen=True, slots=True)
class Installation:
    """A StarCraft II installation directory, and the things inside it this library needs.

    `system` decides the layout and defaults to the one this process runs on. Wine and WSL are not supported:
    run the native client for the platform you are on.
    """

    base: Path
    system: str = field(default_factory=platform.system)

    def __post_init__(self) -> None:
        _settings(self.system)

    @classmethod
    def find(cls, *, system: str | None = None) -> Self:
        """Locate the installation: `SC2PATH`, then the launcher's own record of it, then the platform default."""
        system = system or platform.system()
        settings = _settings(system)
        # An empty SC2PATH is no path at all, and `Path("")` would be the working directory.
        candidates = (os.environ.get("SC2PATH") or None, cls._from_execute_info(settings), settings.default_base)
        for candidate in candidates:
            if candidate is None:
                continue
            base = Path(candidate).expanduser()
            if base.is_dir():
                logger.info("Found StarCraft II at {}", base)
                return cls(base, system)
        raise InstallationNotFoundError(
            f"no StarCraft II installation at {settings.default_base}; set SC2PATH to where it is"
        )

    def executable(self, *, base_build: int | None = None) -> Path:
        """The client binary, for `base_build` or for the newest build installed."""
        builds = self.builds()
        if not builds:
            raise GameVersionError(f"{self.base / _VERSIONS} holds no {_BASE_PREFIX}<build> directory")
        if base_build is None:
            base_build = max(builds)
        elif base_build not in builds:
            raise GameVersionError(f"build {base_build} is not installed; {self.base} has {sorted(builds)}")
        if base_build < MINIMUM_BASE_BUILD:
            raise GameVersionError(
                f"build {base_build} predates the raw interface; {MINIMUM_BASE_BUILD} or newer is required"
            )
        return self.base / _VERSIONS / f"{_BASE_PREFIX}{base_build}" / _settings(self.system).executable

    def builds(self) -> frozenset[int]:
        """Every base build installed, by number."""
        versions = self.base / _VERSIONS
        if not versions.is_dir():
            return frozenset()
        return frozenset(
            int(entry.name[len(_BASE_PREFIX) :])
            for entry in versions.iterdir()
            if entry.is_dir() and re.fullmatch(rf"{_BASE_PREFIX}\d+", entry.name)
        )

    @property
    def working_directory(self) -> Path | None:
        """The directory the client must be started from, on a platform that demands one."""
        directory = _settings(self.system).working_directory
        return None if directory is None else self.base / directory

    @property
    def maps(self) -> Path:
        """The map directory. Blizzard's installers disagree about its capitalization."""
        lowercase = self.base / "maps"
        return lowercase if lowercase.is_dir() else self.base / "Maps"

    @property
    def replays(self) -> Path:
        """The directory the client writes replays to."""
        return self.base / "Replays"

    @staticmethod
    def _from_execute_info(settings: _Platform) -> str | None:
        """The install path the launcher last recorded, where a non-default install shows up."""
        if settings.execute_info is None:
            return None
        record = Path.home().expanduser() / settings.execute_info
        if not record.is_file():
            return None
        found = re.search(r" = (.*)Versions", record.read_text(encoding="utf-8", errors="replace"))
        if found is None:
            return None
        # The capture ends at the separator before `Versions`, and only the host's own separator counts as one.
        return found.group(1).rstrip("\\/")
