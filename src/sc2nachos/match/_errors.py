"""Errors raised when asking where a player started."""

from sc2nachos._shared.errors import NachOSError


class NoStartLocationError(NachOSError, LookupError):
    """No expansion stands where the player started, as on a mini-game's map with no townhall."""
