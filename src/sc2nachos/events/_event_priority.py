"""The order the handlers of an event run in."""

from sc2nachos._enum import ReadableIntEnum


class EventPriority(ReadableIntEnum):
    """A handler's place among an event's handlers: higher priorities run first, equal ones in subscription order."""

    LOWEST = 0
    VERY_LOW = 1
    LOW = 2
    MEDIUM = 3
    HIGH = 4
    VERY_HIGH = 5
    HIGHEST = 6
