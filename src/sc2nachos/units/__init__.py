"""The units of a game, each under one id for the whole game, and collections of them."""

from sc2nachos._shared.errors import NotReportedError
from sc2nachos.gamedata._unit_type import UnitType, UnitTypeKind
from sc2nachos.units._errors import UnitIdsExhaustedError, UnknownTagError
from sc2nachos.units._own_unit import OwnUnit
from sc2nachos.units._progress import Progress
from sc2nachos.units._unit import Unit
from sc2nachos.units._unit_dict import UnitDict
from sc2nachos.units._units import Units
from sc2nachos.units._values import (
    Alliance,
    CloakState,
    Order,
    Passenger,
    RallyTarget,
    Target,
    Visibility,
    VitalType,
)

__all__ = [
    "Alliance",
    "CloakState",
    "UnitType",
    "UnitTypeKind",
    "NotReportedError",
    "Order",
    "OwnUnit",
    "Passenger",
    "Progress",
    "RallyTarget",
    "Target",
    "Unit",
    "UnitDict",
    "UnitIdsExhaustedError",
    "Units",
    "UnknownTagError",
    "Visibility",
    "VitalType",
]
