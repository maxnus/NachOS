"""The base of the exceptions the library raises for its own failures."""


class NachOSError(Exception):
    """A failure of the library's own. Misuse, such as a bad argument, raises a built-in instead."""
