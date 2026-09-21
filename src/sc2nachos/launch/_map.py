"""The maps an installation can play on."""

from dataclasses import dataclass
from pathlib import Path
from typing import Self

from loguru import logger

from sc2nachos._errors import NachOSError
from sc2nachos.launch._installation import Installation

_SUFFIX = ".SC2Map"


class MapNotFoundError(NachOSError, LookupError):
    """The installation holds no map by the name asked for."""


@dataclass(frozen=True, slots=True)
class Map:
    """A map file the game can load.

    Construct one directly to play a map that lives outside the installation.
    """

    path: Path

    @property
    def name(self) -> str:
        """The file name without its extension, which is what the map is known by."""
        return self.path.stem

    @classmethod
    def find(cls, name: str, *, installation: Installation | None = None) -> Self:
        """The map called `name`, anywhere under the installation's map directory.

        The extension is optional and the name is matched without regard to case. Map packs install alongside
        the maps they replace, so a name can match more than once: the shallowest match wins, ties alphabetically.
        """
        installation = installation or Installation.find()
        wanted = name[: -len(_SUFFIX)] if name.lower().endswith(_SUFFIX.lower()) else name
        matches = sorted(
            (path for path in installation.maps.rglob(f"*{_SUFFIX}") if path.stem.lower() == wanted.lower()),
            key=lambda path: (len(path.parts), path),
        )
        if not matches:
            raise MapNotFoundError(
                f"{installation.maps} holds no map called {wanted}; it has {cls._names(installation)}"
            )
        if len(matches) > 1:
            logger.debug("{} maps are called {}; playing {}", len(matches), wanted, matches[0])
        return cls(matches[0])

    @staticmethod
    def _names(installation: Installation) -> list[str]:
        return sorted({path.stem for path in installation.maps.rglob(f"*{_SUFFIX}")})
