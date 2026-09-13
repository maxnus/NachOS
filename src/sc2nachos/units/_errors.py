"""What reading a unit raises when the game has no answer."""

from sc2nachos._errors import NachOSError


class NotReportedError(NachOSError, LookupError):
    """The game has never shown this unit in sight, so it never reported this about it."""


class UnknownTagError(NachOSError, LookupError):
    """The game named a unit by a tag it never reported a unit under."""
