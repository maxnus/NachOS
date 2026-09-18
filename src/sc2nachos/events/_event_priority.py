"""Where among the handlers of an event one runs."""

from sc2nachos._enum import ReadableIntEnum


class EventPriority(ReadableIntEnum):
    """Where among the handlers of an event one runs: every handler of an earlier priority first, and those of one
    priority in the order they subscribed."""

    FIRST = 0
    EARLY = 1
    NORMAL = 2
    LATE = 3
    LAST = 4
