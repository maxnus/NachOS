"""The debug cheats that are on or off for a whole game."""

from s2clientprotocol import debug_pb2

from sc2nachos._enum import ReadableIntEnum


class Cheat(ReadableIntEnum):
    """A debug cheat that is on or off for the whole game, as `api.debug.turn_on` and `turn_off` take it.
    `docs/cheats.md` says what several do to what the game reports."""

    SHOW_MAP = debug_pb2.show_map
    """Shows the whole map, the fog lifted."""
    CONTROL_ENEMY = debug_pb2.control_enemy
    """Lets this player order the enemy's units (stated)."""
    FOOD = debug_pb2.food
    """Lifts the supply cap."""
    FREE = debug_pb2.free
    """Makes everything cost nothing, a spell's energy included."""
    GOD = debug_pb2.god
    """Keeps this player's units from taking damage, and multiplies every weapon's damage in the game's tables by 10."""
    COOLDOWN = debug_pb2.cooldown
    """Takes the cooldowns off abilities (stated)."""
    TECH_TREE = debug_pb2.tech_tree
    """Waives every requirement, and grants 42 upgrades."""
    FAST_BUILD = debug_pb2.fast_build
    """Makes building, training and research take seconds."""
