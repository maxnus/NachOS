"""Errors raised when reading a unit the game has no answer for."""

from sc2nachos._errors import NachOSError


class NotReportedError(NachOSError, LookupError):
    """The game has never shown this unit in sight, so it has never reported this field of it."""


class UnknownTagError(NachOSError, LookupError):
    """The game named a unit by a tag it has never reported a unit under."""
