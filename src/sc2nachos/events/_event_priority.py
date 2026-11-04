"""The order the handlers of an event run in."""

from sc2nachos._shared.readable_int_enum import ReadableIntEnum


class EventPriority(ReadableIntEnum):
    """A handler's place among an event's handlers: higher priorities run first, equal ones in subscription order.

    `BEFORE` and `AFTER` are an extension's, and the only ones it takes: a handler subscribed while NachOS builds the
    extensions runs at one of them, before or after every handler of the bot's of the events handed out with its own,
    as `Api.extensions` says, and no other handler may.
    """

    AFTER = 0
    LOWEST = 1
    VERY_LOW = 2
    LOW = 3
    MEDIUM = 4
    HIGH = 5
    VERY_HIGH = 6
    HIGHEST = 7
    BEFORE = 8
