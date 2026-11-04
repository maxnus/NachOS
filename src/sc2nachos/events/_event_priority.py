"""The order the handlers of an event run in."""

from sc2nachos._shared.readable_int_enum import ReadableIntEnum


class EventPriority(ReadableIntEnum):
    """A handler's place among an event's handlers: higher priorities run first, equal ones in subscription order.

    `BEFORE` and `AFTER` are an extension's, and the only ones it takes: a handler subscribed while NachOS builds the
    extensions runs at one of them, before or after every handler of the bot's of the events handed out with its own,
    as `Api.extensions` says, and no other handler may.
    """

    AFTER = -1
    LOWEST = 0
    VERY_LOW = 1
    LOW = 2
    MEDIUM = 3
    HIGH = 4
    VERY_HIGH = 5
    HIGHEST = 6
    BEFORE = 7
