"""How a game's observations are read into units that last across them: which unit each tag is, who builds what,
the upgrades each side has, and what changed from one observation to the next."""

from sc2nachos.units._tracking._tracker import _Tracker

__all__ = ["_Tracker"]
