"""The enemy player: who it is, what is known of it, and how much of that NachOS infers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from s2clientprotocol import sc2api_pb2

from sc2nachos._enum import ReadableIntEnum
from sc2nachos.match import Race

if TYPE_CHECKING:
    from sc2nachos.ids import UpgradeId


class UpgradeInference(ReadableIntEnum):
    """How much of `Enemy.upgrades` NachOS infers. Each level includes the ones below it."""

    NONE = 0
    """Nothing. Only the bot changes the upgrades."""
    BASIC = 1
    """The attack, armor and shield levels visible enemy units report. `UpgradeReader.read_basic_upgrades` reads them
    as levels of each unit type's own upgrade lines."""
    INTERMEDIATE = 2
    """Also what only an upgrade can cause, read by `UpgradeReader.read_intermediate_upgrades`: Burrow, Warp Gate,
    Stimpack, Combat Shield, Concussive Shells, Charge, Psionic Storm, Neural Parasite, Interference Matrix,
    Nanomuscular Swell, Cloaking Field and Personal Cloaking. This reads the buffs on every visible unit, so a buff
    the curated ids leave out raises `UncuratedIdError`, which the lower levels never do."""


@final
class Enemy:
    """The other player, and what NachOS has learned or been told about it. Each game has its own."""

    __slots__ = ("_id", "_name", "_race", "_requested_race", "_upgrades")

    def __init__(self, *, player_id: int, requested_race: Race, name: str | None, race: Race | None) -> None:
        self._id = player_id
        self._requested_race = requested_race
        self._name = name
        self._race = race
        # Replaced whole on every change, since units read it as part of a key many times a step.
        self._upgrades: frozenset[UpgradeId] = frozenset()

    def __repr__(self) -> str:
        race = None if self._race is None else self._race.name
        return f"Enemy(id={self._id}, race={race}, requested_race={self._requested_race.name}, name={self._name!r})"

    @classmethod
    def _from_game(cls, info: sc2api_pb2.ResponseGameInfo, player_id: int) -> Self | None:
        """The one player of the game `info` describes, observers aside, who is not `player_id`, or `None` if there is
        not one. Its race is the one the game gives, else the one it asked for unless that is Random."""
        others = [
            entry
            for entry in info.player_info
            if entry.player_id != player_id and entry.type != sc2api_pb2.PlayerType.Observer
        ]
        if len(others) != 1:
            return None
        (entry,) = others
        played = (Race.TERRAN, Race.ZERG, Race.PROTOSS)
        requested = Race(entry.race_requested)
        actual = Race(entry.race_actual)
        race = actual if actual in played else requested if requested in played else None
        return cls(player_id=entry.player_id, requested_race=requested, name=entry.player_name or None, race=race)

    @property
    def id(self) -> int:
        """Its id in the game, which its units read as their `owner_id`."""
        return self._id

    @property
    def requested_race(self) -> Race:
        """The race it asked for, `RANDOM` included."""
        return self._requested_race

    @property
    def race(self) -> Race | None:
        """The race it plays, never `RANDOM`, and `None` until it is known.

        A game of two players gives it from the start (tool `sweep_requests`), and so does any enemy that did not ask
        for Random. Against a computer that did, it is known once the first enemy unit is seen, as that unit's race:
        `EnemyRaceKnownEvent` comes on the turn it is known. A unit of this player's that a neural parasite holds is
        the enemy's meanwhile, so if it is the first seen, the race reads as this player's own.
        """
        return self._race

    @property
    def name(self) -> str | None:
        """Its name as the game gives it, and `None` where the game gives none, as in every game seen so far, though it
        joined with one (tool `sweep_requests`)."""
        return self._name

    def _learn_race(self, race: Race) -> None:
        """Take `race` as the race it plays."""
        self._race = race

    @property
    def upgrades(self) -> frozenset[UpgradeId]:
        """Every upgrade the enemy is known or assumed to have.

        The game never reports an enemy upgrade outright. It reports the attack, armor and shield levels of each
        visible unit, and what only an upgrade can cause, such as a burrowed zergling or a stimmed marine.
        `UpgradeReader` reads both, and NachOS adds them here as far as the `Api` was told to with `UpgradeInference`.
        Every enemy unit, in sight or not, counts what is held here. Anything else, such as Grooved Spines or Metabolic
        Boost, the bot adds with `assume_upgrades` when it works it out.
        """
        return self._upgrades

    def assume_upgrades(self, *upgrades: UpgradeId) -> None:
        """Assume the enemy has `upgrades` from now on."""
        self._upgrades |= frozenset(upgrades)

    def forget_upgrades(self, *upgrades: UpgradeId) -> None:
        """Assume the enemy no longer has `upgrades`.

        If NachOS infers upgrades, an enemy unit that shows one again puts it back.
        """
        self._upgrades -= frozenset(upgrades)
