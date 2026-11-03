"""Errors raised when asking the map for a place it does not have."""

from sc2nachos._shared.errors import NachOSError


class NoStartLocationError(NachOSError, LookupError):
    """No expansion stands where the player started, as on a mini-game's map with no townhall."""


class NoMainRampError(NachOSError, LookupError):
    """No ramp out of the start location's main can be walked to."""


class NoNaturalError(NachOSError, LookupError):
    """No expansion where no player starts can be walked to from the start location."""
