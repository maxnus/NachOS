"""Errors raised when asking the map for a place it does not have."""

from sc2nachos._shared.errors import NachOSError


class NoMainRampError(NachOSError, LookupError):
    """No ramp out of the start location's main can be walked to."""


class NoNaturalError(NachOSError, LookupError):
    """No expansion where no player starts can be walked to from the start location."""


class NoThirdError(NachOSError, LookupError):
    """Fewer than two full bases where no player starts, beside the natural, can be walked to from the start
    location's natural."""
