"""The vocabulary of a match."""

import pytest
from google.protobuf.descriptor import EnumDescriptor
from s2clientprotocol import common_pb2, sc2api_pb2

from sc2nachos.ids import UnitTypeId
from sc2nachos.match import AIBuild, Difficulty, Race, Result


@pytest.mark.parametrize(
    ("enum", "descriptor"),
    [
        (Race, common_pb2.Race.DESCRIPTOR),
        (Result, sc2api_pb2.Result.DESCRIPTOR),
        (Difficulty, sc2api_pb2.Difficulty.DESCRIPTOR),
        (AIBuild, sc2api_pb2.AIBuild.DESCRIPTOR),
    ],
)
def test_covers_every_value_the_protocol_defines(enum: type[Race], descriptor: EnumDescriptor) -> None:
    """Names are ours, values are the game's: a value the protocol adds must not go missing here."""
    protocol = {value.number for value in descriptor.values}
    assert {member.value for member in enum} == protocol


def test_members_are_the_integers_the_protocol_uses() -> None:
    assert Race.TERRAN == 1
    assert Result.VICTORY == 1
    assert Difficulty.CHEAT_INSANE == 10
    assert AIBuild.MACRO == 5


def test_they_print_as_names_not_numbers() -> None:
    """IntEnum formats as a bare number since 3.11, which makes logs unreadable."""
    assert str(Race.TERRAN) == "Race.TERRAN"
    assert f"{Result.VICTORY}" == "Result.VICTORY"
    assert f"{Race.TERRAN:d}" == "1"


@pytest.mark.parametrize(
    ("race", "unit_types"),
    [
        (Race.TERRAN, (UnitTypeId.SCV, UnitTypeId.COMMAND_CENTER, UnitTypeId.SUPPLY_DEPOT)),
        (Race.ZERG, (UnitTypeId.DRONE, UnitTypeId.HATCHERY, UnitTypeId.OVERLORD)),
        (Race.PROTOSS, (UnitTypeId.PROBE, UnitTypeId.NEXUS, UnitTypeId.PYLON)),
    ],
    ids=["terran", "zerg", "protoss"],
)
def test_a_race_names_its_worker_starting_townhall_and_supply(race: Race, unit_types: tuple[UnitTypeId, ...]) -> None:
    assert (race.worker_type, race.townhall_type, race.supply_type) == unit_types


@pytest.mark.parametrize("race", [Race.NONE, Race.RANDOM], ids=lambda race: race.name)
def test_a_race_no_player_is_given_has_no_unit_types(race: Race) -> None:
    for unit_type_of in (lambda: race.worker_type, lambda: race.townhall_type, lambda: race.supply_type):
        with pytest.raises(ValueError, match="no race a player is given"):
            unit_type_of()
