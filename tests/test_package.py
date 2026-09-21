"""Scaffold tests: the package imports, and its dependencies are present and self-owned."""

import sc2nachos
from sc2nachos import enemy, match


def test_version_is_exposed() -> None:
    """The package exposes a version string."""
    assert isinstance(sc2nachos.__version__, str)
    assert sc2nachos.__version__


def test_protobuf_protocol_is_importable() -> None:
    """The s2clientprotocol bindings NachOS is built on are installed."""
    from s2clientprotocol import sc2api_pb2

    assert sc2api_pb2.Request is not None


def test_no_dependency_on_burnysc2() -> None:
    """NachOS must never import the library it replaces.

    AvocaDOS installs both during the migration, so an accidental import would otherwise go unnoticed until
    burnysc2 is finally removed.
    """
    import sys

    assert "sc2" not in sys.modules, "importing sc2nachos must not pull in burnysc2"


def test_what_starts_a_game_is_exported_from_the_top_level() -> None:
    """The README's first example runs on `sc2nachos` alone: the api, the runner, the match vocabulary and the one
    setting `Api` takes an enum for."""
    assert sc2nachos.Race is match.Race
    assert sc2nachos.Computer is match.Computer
    assert sc2nachos.Difficulty is match.Difficulty
    assert sc2nachos.AIBuild is match.AIBuild
    assert sc2nachos.Result is match.Result
    assert sc2nachos.UpgradeInference is enemy.UpgradeInference
    assert set(sc2nachos.__all__) >= {"Api", "ApiBot", "run_local", "run_ladder", "Race", "Computer", "Difficulty"}
