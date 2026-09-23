"""The players of a match, how the computer opponent plays, and how the match ended."""

from dataclasses import dataclass

from s2clientprotocol import common_pb2, sc2api_pb2

from sc2nachos._enum import ReadableIntEnum


class Race(ReadableIntEnum):
    """A player's race. `RANDOM` is what a player picks, not what they are given."""

    NONE = common_pb2.Race.NoRace
    TERRAN = common_pb2.Race.Terran
    ZERG = common_pb2.Race.Zerg
    PROTOSS = common_pb2.Race.Protoss
    RANDOM = common_pb2.Race.Random


class Result(ReadableIntEnum):
    """How a match ended, from one player's point of view."""

    VICTORY = sc2api_pb2.Result.Victory
    DEFEAT = sc2api_pb2.Result.Defeat
    TIE = sc2api_pb2.Result.Tie
    UNDECIDED = sc2api_pb2.Result.Undecided


class Difficulty(ReadableIntEnum):
    """How strongly the built-in computer opponent plays. The `CHEAT_` levels break the game's own rules."""

    VERY_EASY = sc2api_pb2.Difficulty.VeryEasy
    EASY = sc2api_pb2.Difficulty.Easy
    MEDIUM = sc2api_pb2.Difficulty.Medium
    MEDIUM_HARD = sc2api_pb2.Difficulty.MediumHard
    HARD = sc2api_pb2.Difficulty.Hard
    HARDER = sc2api_pb2.Difficulty.Harder
    VERY_HARD = sc2api_pb2.Difficulty.VeryHard
    CHEAT_VISION = sc2api_pb2.Difficulty.CheatVision
    CHEAT_MONEY = sc2api_pb2.Difficulty.CheatMoney
    CHEAT_INSANE = sc2api_pb2.Difficulty.CheatInsane


class AIBuild(ReadableIntEnum):
    """Which opening the built-in computer opponent favors."""

    RANDOM = sc2api_pb2.AIBuild.RandomBuild
    RUSH = sc2api_pb2.AIBuild.Rush
    TIMING = sc2api_pb2.AIBuild.Timing
    POWER = sc2api_pb2.AIBuild.Power
    MACRO = sc2api_pb2.AIBuild.Macro
    AIR = sc2api_pb2.AIBuild.Air


@dataclass(frozen=True, slots=True)
class Participant:
    """A slot for a client to join. Its race and name are set at the join."""


@dataclass(frozen=True, slots=True)
class Computer:
    """The built-in computer opponent. It needs no client."""

    race: Race = Race.RANDOM
    difficulty: Difficulty = Difficulty.VERY_HARD
    build: AIBuild = AIBuild.RANDOM
    name: str | None = None


type Player = Participant | Computer
