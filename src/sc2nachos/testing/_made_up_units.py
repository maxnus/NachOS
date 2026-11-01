"""Units for a bot's tests, made as NachOS makes them from a game's observations."""

from collections.abc import Iterable
from typing import Any

from s2clientprotocol import raw_pb2

from sc2nachos.enemy import Enemy
from sc2nachos.gamedata import GameData
from sc2nachos.match import Race
from sc2nachos.testing._shipped_tables import shipped_tables
from sc2nachos.testing._unit_report import ENEMY_PLAYER_ID, UnitReport
from sc2nachos.units import Unit, Units
from sc2nachos.units._tracking import _Tracker


class MadeUpUnits:
    """The units of a game made up for a test, taken in one observation after another, as NachOS takes them in from a
    game: the same `Unit` and `OwnUnit` objects, kept up to date, with no game client.

    This player is player 1, and the enemy player 2. The tables are those NachOS ships unless `tables` are given.
    """

    def __init__(self, tables: GameData | None = None) -> None:
        enemy = Enemy(player_id=ENEMY_PLAYER_ID, requested_race=Race.RANDOM, name=None, race=None, ladder_id=None)
        self._tracker = _Tracker(shipped_tables() if tables is None else tables, enemy)
        self._step: int | None = None

    @property
    def tables(self) -> GameData:
        """The game's tables the units read their types from."""
        return self._tracker.game_data

    @property
    def units(self) -> Units[Unit[Any]]:
        """Every unit the latest observation listed."""
        return self._tracker.unit_tracker.present

    def observe(
        self, *reports: UnitReport, step: int | None = None, dead: Iterable[UnitReport | int] = ()
    ) -> list[Any]:
        """Take in an observation at `step` that lists the units of `reports` and reports `dead` dead, by their reports
        or tags, and return the units of `reports` in their order: this player's as `OwnUnit`, the others as `Unit`.

        `step` is by default the step after the latest observation's, and 0 for the first. A unit reported under a
        tag already seen is the same object as before, read anew; one seen before and not reported now is no longer
        listed. Raises `ValueError` for a step not after the latest observation's, as a game's steps only go on.
        """
        if step is None:
            step = 0 if self._step is None else self._step + 1
        elif self._step is not None and step <= self._step:
            raise ValueError(f"an observation at step {step} cannot follow one at step {self._step}")
        dead_tags = [report.tag if isinstance(report, UnitReport) else report for report in dead]
        observation = raw_pb2.ObservationRaw(
            units=[report._as_reported() for report in reports], event=raw_pb2.Event(dead_units=dead_tags)
        )
        self._tracker.update(observation, step)
        self._step = step
        unit_tracker = self._tracker.unit_tracker
        return [unit_tracker.by_tag(report.tag) for report in reports]
