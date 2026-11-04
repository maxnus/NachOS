"""Errors raised when asking the map for a place it does not have."""

from sc2nachos._shared.errors import NachOSError


class NoMainRampError(NachOSError, LookupError):
    """No ramp out of the start location's main can be walked to."""


class NoExpansionError(NachOSError, LookupError):
    """No expansion of the kind asked for, the natural or a third, can be walked to from the start location."""
