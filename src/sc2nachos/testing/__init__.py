"""Units for a bot's tests, made without a game: `MadeUpUnits` takes in the `UnitReport`s of one observation after
another and answers the units NachOS makes of them, on the tables it ships, `shipped_tables`, or on others."""

from sc2nachos.testing._made_up_units import MadeUpUnits
from sc2nachos.testing._shipped_tables import shipped_tables
from sc2nachos.testing._unit_report import ReportedOrder, ReportedTarget, UnitReport

__all__ = ["MadeUpUnits", "ReportedOrder", "ReportedTarget", "UnitReport", "shipped_tables"]
