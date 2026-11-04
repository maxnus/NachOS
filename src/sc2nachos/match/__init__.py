"""The players of a match, this player and the enemy as the game describes them, how the computer opponent plays,
and how the match ended."""

from sc2nachos.match._enemy import Enemy
from sc2nachos.match._errors import NoStartLocationError
from sc2nachos.match._match import AIBuild, Computer, Difficulty, Participant, PlayerSetup, Race, Result
from sc2nachos.match._player import Player

__all__ = [
    "AIBuild",
    "Computer",
    "Difficulty",
    "Enemy",
    "NoStartLocationError",
    "Participant",
    "Player",
    "PlayerSetup",
    "Race",
    "Result",
]
