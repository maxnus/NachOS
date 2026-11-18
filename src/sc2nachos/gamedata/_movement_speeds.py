"""How fast units move on creep and under the buffs that change it, which the game's tables hold nothing of."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from sc2nachos.ids import BuffId, UnitTypeId, UpgradeId

if TYPE_CHECKING:
    from collections.abc import Mapping

# Each type creep speeds up, its speed on creep in distance per second at the Faster speed, without an upgrade: most
# Zerg ground types 1.3 times their speed off it, a locust 1.4 times and a queen 2.66 times, to within the rounding of
# a step's distance. A type left out moves as fast on creep as off it: a drone, a broodling, a changeling but as a
# zergling, and anything that flies (in game, tool `sweep_movement`, build 75689).
CREEP_SPEEDS: Final[Mapping[UnitTypeId, float]] = MappingProxyType(
    {
        UnitTypeId.BANELING: 4.55,
        UnitTypeId.CHANGELING_ZERGLING: 5.3703,
        UnitTypeId.CHANGELING_ZERGLING_WINGS: 5.3703,
        UnitTypeId.HYDRALISK: 4.0906,
        UnitTypeId.INFESTOR: 4.0906,
        UnitTypeId.INFESTOR_BURROWED: 3.6367,
        UnitTypeId.LOCUST: 3.6695,
        UnitTypeId.LURKER: 5.3703,
        UnitTypeId.QUEEN: 3.4945,
        UnitTypeId.RAVAGER: 5.0039,
        UnitTypeId.ROACH: 4.0906,
        UnitTypeId.SWARM_HOST: 4.0906,
        UnitTypeId.ULTRALISK: 5.3703,
        UnitTypeId.ZERGLING: 5.3703,
    }
)

# The upgrade that changes the speed of a type creep speeds up, by type, with the type's speed on creep once it has it:
# Muscular Augments and Anabolic Synthesis add less on creep than off it, the others 1.3 times as much (in game, tool
# `sweep_movement`, build 75689). Each was measured as its type's only speed upgrade, as none of these types has a
# second; one that had would need its speed with both measured.
UPGRADED_CREEP_SPEEDS: Final[Mapping[UnitTypeId, tuple[UpgradeId, float]]] = MappingProxyType(
    {
        UnitTypeId.BANELING: (UpgradeId.BANELING_SPEED, 5.3703),
        UnitTypeId.HYDRALISK: (UpgradeId.HYDRALISK_SPEED, 4.8289),
        UnitTypeId.ROACH: (UpgradeId.ROACH_SPEED, 5.4578),
        UnitTypeId.ULTRALISK: (UpgradeId.ULTRALISK_SPEED, 5.5344),
        UnitTypeId.ZERGLING: (UpgradeId.ZERGLING_SPEED, 8.5477),
    }
)

# How many times as fast each buff makes its wearer move, flying or not; two at once, and creep, multiply. A medivac's
# afterburners and a hydralisk's lunge were measured alone; an inhibitor zone's flying buff and an acceleration zone's
# are left out, unmeasured (in game, tool `sweep_movement`, build 75689).
MOVEMENT_SPEED_BUFFS: Final[Mapping[BuffId, float]] = MappingProxyType(
    {
        BuffId.HYDRALISK_LUNGE: 2.0,
        BuffId.INFESTOR_FUNGAL_GROWTH: 0.25,
        BuffId.INHIBITOR_ZONE: 0.65,
        BuffId.MARAUDER_CONCUSSIVE_SHELLS_SLOW: 0.5,
        BuffId.MARAUDER_STIMMED: 1.5,
        BuffId.MARINE_STIMMED: 1.5,
        BuffId.MEDIVAC_BOOST: 1.7,
        BuffId.MOTHERSHIP_TIME_WARP: 0.6,
    }
)

# The buffs that set their wearer's speed to a share of its type's speed before any upgrade, whatever its upgrades: a
# void ray in prismatic alignment moves at 0.75 of its 3.85 with Flux Vanes or without, and a zealot charging, which it
# can only with Charge, some 3.3 times its 3.15. Measured alone (in game, tool `sweep_movement`, build 75689).
MOVEMENT_SPEED_SHARE_BUFFS: Final[Mapping[BuffId, float]] = MappingProxyType(
    {
        BuffId.VOID_RAY_PRISMATIC_ALIGNMENT: 0.75,
        BuffId.ZEALOT_CHARGING: 3.2973,
    }
)
