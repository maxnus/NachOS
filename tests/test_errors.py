"""The exceptions the library raises for failures of its own."""

import importlib
import inspect
import pkgutil

import pytest

import sc2nachos
from sc2nachos import NachOSError, NotPlayingError
from sc2nachos.launch import (
    GameLaunchError,
    GameVersionError,
    InstallationNotFoundError,
    MapNotFoundError,
    UnsupportedPlatformError,
)
from sc2nachos.protocol import ConnectionClosedError, ConnectionTimeoutError


def test_every_exception_the_library_defines_is_one_of_its_own() -> None:
    modules = [importlib.import_module(info.name) for info in pkgutil.walk_packages(sc2nachos.__path__, "sc2nachos.")]
    defined = {
        member
        for module in [sc2nachos, *modules]
        for _, member in inspect.getmembers(module, inspect.isclass)
        if issubclass(member, BaseException) and member.__module__.startswith("sc2nachos")
    }
    assert NotPlayingError in defined
    assert sorted(error.__name__ for error in defined if not issubclass(error, NachOSError)) == []


@pytest.mark.parametrize(
    ("error", "builtin"),
    [
        (ConnectionClosedError, ConnectionError),
        (ConnectionTimeoutError, TimeoutError),
        (GameLaunchError, OSError),
        (GameVersionError, LookupError),
        (InstallationNotFoundError, FileNotFoundError),
        (MapNotFoundError, LookupError),
        (NotPlayingError, RuntimeError),
        (UnsupportedPlatformError, ValueError),
    ],
)
def test_an_error_that_is_a_case_of_a_builtin_is_caught_as_one(
    error: type[NachOSError], builtin: type[Exception]
) -> None:
    with pytest.raises(builtin, match="^what went wrong$"):
        raise error("what went wrong")
