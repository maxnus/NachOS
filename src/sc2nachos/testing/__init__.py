"""Units for a bot's tests, made without a game: `MadeUpUnits` takes in the `UnitReport`s of one observation after
another and answers the units NachOS makes of them, on the game's data it ships, `load_game_data`, or on other data."""

from sc2nachos.testing._made_up_units import MadeUpUnits
from sc2nachos.testing._shipped_game_data import load_game_data
from sc2nachos.testing._unit_report import ReportedOrder, ReportedTarget, UnitReport

__all__ = ["MadeUpUnits", "ReportedOrder", "ReportedTarget", "UnitReport", "load_game_data"]
