"""Whether a structure may stand on creep."""

from sc2nachos._enum import ReadableIntEnum


class CreepRequirement(ReadableIntEnum):
    """Whether a structure of a type may stand on creep, as the game answered its placement on creep and off it."""

    ON_CREEP = 1
    """Only on creep, under every tile: a zerg structure but a hatchery or a nydus worm, and a creep tumor."""
    OFF_CREEP = 2
    """Only off creep: a terran or protoss structure but a raven's auto turret or an oracle's stasis ward."""
    EITHER = 3
    """On creep or off it: a hatchery, a nydus worm, a raven's auto turret and an oracle's stasis ward."""
