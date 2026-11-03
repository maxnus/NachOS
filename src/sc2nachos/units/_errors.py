"""Errors raised when reading a unit the game has no answer for."""

from sc2nachos._shared.errors import NachOSError


class NotReportedError(NachOSError, LookupError):
    """The game has not reported what was asked: a field of a unit it has never shown visible, or which start location
    the enemy took on a map with more than two."""


class UnknownTagError(NachOSError, LookupError):
    """The game named a unit by a tag it has never reported a unit under."""


class UnitIdsExhaustedError(NachOSError, OverflowError):
    """A game has seen more units of one alliance than its ids can number."""
