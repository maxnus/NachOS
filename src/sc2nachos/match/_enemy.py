"""The enemy player: who it is and what is known of it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from s2clientprotocol import sc2api_pb2

from sc2nachos._shared.errors import NotReportedError
from sc2nachos.match._errors import NoStartLocationError
from sc2nachos.match._match import Race

if TYPE_CHECKING:
    from sc2nachos.gamedata import GameData
    from sc2nachos.gamemap import StartLocation
    from sc2nachos.ids import UpgradeId


@final
class Enemy:
    """The other player, and what NachOS has learned or been told about it. `game_data` says which upgrades each upgrade
    needs researched first."""

    __slots__ = (
        "_game_data",
        "_id",
        "_ladder_id",
        "_name",
        "_race",
        "_requested_race",
        "_start_location",
        "_start_locations",
        "_upgrades",
    )

    def __init__(
        self,
        *,
        player_id: int,
        requested_race: Race,
        name: str | None,
        race: Race | None,
        ladder_id: str | None,
        game_data: GameData,
    ) -> None:
        self._game_data = game_data
        self._id = player_id
        self._requested_race = requested_race
        self._name = name
        self._race = race
        self._ladder_id = ladder_id
        # Replaced whole on every change, since units read it as part of a key many times a step.
        self._upgrades: frozenset[UpgradeId] = frozenset()
        # Where it may have started, the map's start locations but this player's: `None` for one no expansion stands at.
        self._start_locations: tuple[StartLocation | None, ...] = ()
        # Where it started: the other start on a map of two, else the one the bot named, if it has.
        self._start_location: StartLocation | None = None

    def __repr__(self) -> str:
        race = None if self._race is None else self._race.name
        return (
            f"Enemy(id={self._id}, race={race}, requested_race={self._requested_race.name}, name={self._name!r}, "
            f"ladder_id={self._ladder_id!r})"
        )

    @classmethod
    def _from_game(
        cls, info: sc2api_pb2.ResponseGameInfo, player_id: int, *, game_data: GameData, ladder_id: str | None = None
    ) -> Self | None:
        """The one player of the game `info` describes, observers aside, who is not `player_id`, or `None` if there is
        not one, with `ladder_id`, the ladder's id for it, if the game is a ladder's. Its race is the one the game
        gives, else the one it asked for unless that is Random."""
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
        return cls(
            player_id=entry.player_id,
            requested_race=requested,
            name=entry.player_name or None,
            race=race,
            ladder_id=ladder_id,
            game_data=game_data,
        )

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

    @property
    def ladder_id(self) -> str | None:
        """The ladder's id for it, the same in every game the ladder plays against it, as the ladder passes it in
        `--OpponentId`; `None` outside a ladder game."""
        return self._ladder_id

    def _learn_race(self, race: Race) -> None:
        """Take `race` as the race it plays."""
        self._race = race

    @property
    def start_location(self) -> StartLocation:
        """Where it started: on a map of two start locations the one but this player's, and on a map of more the one
        `assume_start_location` last named.

        Raises `NotReportedError` on a map with more than two start locations until the bot names one with
        `assume_start_location`, since the game does not say which the enemy took, and NachOS does not scout for it.
        Raises `NoStartLocationError` where no expansion stands at its start, as on a mini-game's map, or where the map
        has no start location but this player's.
        """
        if self._start_location is not None:
            return self._start_location
        if len(self._start_locations) > 1:
            raise NotReportedError(
                f"the map has {len(self._start_locations)} start locations but this player's, and the game does not "
                "say which the enemy took: assume_start_location names one"
            )
        if not self._start_locations:
            raise NoStartLocationError("the map has no start location but this player's")
        raise NoStartLocationError("no expansion stands where the enemy started")

    def assume_start_location(self, start_location: StartLocation) -> None:
        """Assume the enemy started at `start_location` from now on, in place of any start assumed before, so that
        `start_location` reads it on a map with more than two start locations. Raises `ValueError` for one that is not
        among the map's start locations but this player's."""
        if start_location not in self._start_locations:
            raise ValueError(f"{start_location.center} is not among the map's start locations but this player's")
        self._start_location = start_location

    def _learn_start_locations(self, start_locations: tuple[StartLocation | None, ...]) -> None:
        """Take `start_locations`, one for each start location on the map but this player's, as where it may have
        started: `None` for one no expansion stands at. Where there is one, it started there."""
        self._start_locations = start_locations
        if len(start_locations) == 1 and (start_location := start_locations[0]) is not None:
            self.assume_start_location(start_location)

    @property
    def upgrades(self) -> frozenset[UpgradeId]:
        """Every upgrade the enemy is known or assumed to have.

        The game never reports an enemy upgrade outright. NachOS adds here the attack, armor and shield levels each
        visible enemy unit reports, as levels of its type's own upgrade lines: a marine at attack level 2 shows the
        first two Terran Infantry Weapons. What only an upgrade can cause, such as a burrowed zergling or a stimmed
        marine, and the speed upgrades enemy units move at, such as Metabolic Boost,
        `sc2nachos.extensions.upgrade_inference.UpgradeInference` adds, if the bot builds it. Anything else, such as
        Grooved Spines, the bot adds with `assume_upgrades` when it works it out.

        Every enemy unit, in sight or not, counts what is held here. It never holds an upgrade without those the game's
        tables say it needs researched first, as level 2 of a line needs level 1.
        """
        return self._upgrades

    def assume_upgrades(self, *upgrades: UpgradeId) -> None:
        """Assume the enemy has `upgrades` from now on, and every upgrade each needs researched first: level 2 of a
        line brings level 1."""
        if new := frozenset(upgrades) - self._upgrades:
            self._upgrades = self._upgrades.union(*map(self._with_the_upgrades_it_needs, new))

    def forget_upgrades(self, *upgrades: UpgradeId) -> None:
        """Assume the enemy no longer has `upgrades`, nor any upgrade that needs one of them researched first: level 1
        of a line takes levels 2 and 3 with it.

        An enemy unit that shows one of them again puts it back: a level it reports does, and so does what
        `sc2nachos.extensions.upgrade_inference.UpgradeInference` reads, if the bot built it.
        """
        forgotten = frozenset(upgrades)
        self._upgrades = frozenset(
            held for held in self._upgrades if self._with_the_upgrades_it_needs(held).isdisjoint(forgotten)
        )

    def _with_the_upgrades_it_needs(self, upgrade: UpgradeId) -> frozenset[UpgradeId]:
        """`upgrade`, every upgrade it needs researched first, and those they need in turn."""
        if (row := self._game_data.upgrades.get(upgrade)) is None:
            return frozenset({upgrade})
        return frozenset({upgrade}).union(*map(self._with_the_upgrades_it_needs, row.tech_requirements.upgrades))
