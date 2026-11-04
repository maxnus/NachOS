"""The base of the exceptions the library raises for its own failures, and the errors more than one package raises."""


class NachOSError(Exception):
    """A failure of the library's own. Misuse, such as a bad argument, raises a built-in instead."""


class NotReportedError(NachOSError, LookupError):
    """The game has not reported what was asked: a field of a unit it has never shown visible, or which start location
    the enemy took on a map with more than two."""
