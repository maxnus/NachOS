"""Where among the handlers of an event one runs."""

from sc2nachos._enum import ReadableIntEnum


class EventPriority(ReadableIntEnum):
    """Where among the handlers of an event one runs: every handler of a higher priority first, and those of one
    priority in the order they subscribed."""

    LOWEST = 0
    VERY_LOW = 1
    LOW = 2
    MEDIUM = 3
    HIGH = 4
    VERY_HIGH = 5
    HIGHEST = 6
