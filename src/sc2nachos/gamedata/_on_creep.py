"""Whether a structure may stand on creep."""

from sc2nachos._enum import ReadableIntEnum


class OnCreep(ReadableIntEnum):
    """Whether a structure of a type may stand on creep, as the game answered its build on creep and off it."""

    REQUIRED = 1
    """Only on creep, every tile of it: a zerg structure but a hatchery or a nydus worm, and a queen's creep tumor."""
    REFUSED = 2
    """Only off creep: a terran or protoss structure but a stasis ward."""
    ALLOWED = 3
    """On creep or off it: a hatchery, a nydus worm and an oracle's stasis ward."""
