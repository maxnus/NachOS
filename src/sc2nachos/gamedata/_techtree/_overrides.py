"""Hand-written corrections to what the game's tables get wrong or leave out. Never generated.

`tools/generate_tech_tree.py` writes none of this, so a new sweep keeps every entry. Each entry is checked against the
sweep: the generator refuses to write the tech tree unless the sweep saw each creation ability here make its unit
type, so a patch that breaks one stops the regeneration. `tests/test_gamedata.py` fails once the game's table names an
ability here for its type, so the entry can go.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from sc2nachos.gamedata._cost import Cost
from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId

MISNAMED_RESEARCH_ABILITIES: Final[Mapping[UpgradeId, AbilityId]] = MappingProxyType(
    {
        # The upgrade table says these three are researched by the `ArmoryResearchSwarm` ids, which an armory is
        # never offered and which do nothing when ordered. Those ids are uncurated, so without this entry the upgrade
        # names no ability and the ability that does research it makes nothing. `ArmoryResearch` is what an armory
        # offers and runs (#23, #28; tested).
        UpgradeId.TERRAN_VEHICLE_AND_SHIP_ARMOR_1: AbilityId.ARMORY_RESEARCH_VEHICLE_AND_SHIP_ARMOR_1,
        UpgradeId.TERRAN_VEHICLE_AND_SHIP_ARMOR_2: AbilityId.ARMORY_RESEARCH_VEHICLE_AND_SHIP_ARMOR_2,
        UpgradeId.TERRAN_VEHICLE_AND_SHIP_ARMOR_3: AbilityId.ARMORY_RESEARCH_VEHICLE_AND_SHIP_ARMOR_3,
    }
)
"""The ability that researches each upgrade whose table row names a dead id.

A nuke has no entry here: `GHOST_ACADEMY_BUILD_NUKE` makes something the curated unit types leave out, so there is no
product to name, and NachOS reads it as an ability that makes nothing.
"""

UNNAMED_CREATION_ABILITIES: Final[Mapping[AbilityId, UnitTypeId]] = MappingProxyType(
    {
        # The table names MorphZerglingToBaneling (80), which a zergling is never offered and which does nothing.
        AbilityId.ZERGLING_MORPH_BANELING: UnitTypeId.BANELING,
        # The table names LurkerAspectMPFromHydraliskBurrowed (2104), offered to no hydralisk, standing or burrowed.
        AbilityId.HYDRALISK_MORPH_LURKER: UnitTypeId.LURKER,
        # The table names RavenBuild_AutoTurret (349), which no raven is ever offered.
        AbilityId.RAVEN_SPAWN_AUTO_TURRET: UnitTypeId.AUTO_TURRET,
        # The table names SpawnInfestedTerran_LocustMP (2018), which no swarm host is ever offered.
        AbilityId.SWARM_HOST_SPAWN_LOCUST: UnitTypeId.LOCUST,
        # The table names PurificationNovaMorph (2546), which no disruptor is ever offered.
        AbilityId.DISRUPTOR_PURIFICATION_NOVA: UnitTypeId.PURIFICATION_NOVA,
        # A structure on a rich geyser is built by the same ability as on any other geyser. The terran table names a
        # duplicate row an SCV is never offered, TerranBuild_Refinery_325; the protoss and zerg tables name nothing.
        AbilityId.SCV_BUILD_REFINERY: UnitTypeId.REFINERY_RICH,
        AbilityId.PROBE_BUILD_ASSIMILATOR: UnitTypeId.ASSIMILATOR_RICH,
        AbilityId.DRONE_MORPH_EXTRACTOR: UnitTypeId.EXTRACTOR_RICH,
        # A warp gate warps in what a gateway trains, and the table names only the gateway's training.
        AbilityId.WARP_GATE_WARP_IN_ADEPT: UnitTypeId.ADEPT,
        AbilityId.WARP_GATE_WARP_IN_DARK_TEMPLAR: UnitTypeId.DARK_TEMPLAR,
        AbilityId.WARP_GATE_WARP_IN_HIGH_TEMPLAR: UnitTypeId.HIGH_TEMPLAR,
        AbilityId.WARP_GATE_WARP_IN_SENTRY: UnitTypeId.SENTRY,
        AbilityId.WARP_GATE_WARP_IN_STALKER: UnitTypeId.STALKER,
        AbilityId.WARP_GATE_WARP_IN_ZEALOT: UnitTypeId.ZEALOT,
    }
)
"""The unit type each ability makes, each seen in game. Where the table names no working ability for the type, this
is its creation ability; otherwise the ability makes this type as well as the one the table names."""

# Each of these, ordered unqueued while a unit moved, was carried out while the move went on; every other ability a
# unit is offered replaced its orders (tool `sweep_orders`). `tools/generate_tech_tree.py` does not write this, and no
# ability belongs here that a sweep has not seen keep a moving unit's orders.
#
# This is not the whole of `OrderBehavior.KEEPS_ORDERS`: `gamedata/_ability_data.py` also reads an ability that makes
# nothing and is offered only to types with no move as keeping orders, which covers a structure's own rally, load,
# cancel and energy casts. The general ids these remap to are not measured; a general id keeps a unit's orders where an
# exact id that remaps to it does.
#
# A toggle is here in both halves: a unit is offered the off half only once the on half has taken, so each was given in
# turn to a moving unit, and both left its move first in its orders. A lurker's hold fire is the one toggle neither half
# of which could be given: it is offered only burrowed, and the game offers a burrowed lurker no move.
KEEPS_ORDERS_ABILITIES: Final[frozenset[AbilityId]] = frozenset(
    {
        AbilityId.ADEPT_SHADE,
        AbilityId.BANELING_ATTACK_STRUCTURES_OFF,
        AbilityId.BANELING_ATTACK_STRUCTURES_ON,
        AbilityId.BANSHEE_CLOAK_OFF,
        AbilityId.BANSHEE_CLOAK_ON,
        AbilityId.GHOST_CLOAK_OFF,
        AbilityId.GHOST_CLOAK_ON,
        AbilityId.GHOST_HOLD_FIRE_OFF,
        AbilityId.GHOST_HOLD_FIRE_ON,
        AbilityId.HYDRALISK_LUNGE,
        AbilityId.MARAUDER_STIM,
        AbilityId.MARINE_STIM,
        AbilityId.MEDIVAC_BOOST,
        AbilityId.MOTHERSHIP_CLOAK_FIELD,
        AbilityId.ORACLE_PULSAR_BEAM_OFF,
        AbilityId.ORACLE_PULSAR_BEAM_ON,
        AbilityId.OVERLORD_CREEP_OFF,
        AbilityId.OVERLORD_CREEP_ON,
        AbilityId.SENTRY_GUARDIAN_SHIELD,
        AbilityId.VOID_RAY_PRISMATIC_ALIGNMENT,
    }
)


COST_OVERRIDES: Final[Mapping[AbilityId, Cost]] = MappingProxyType(
    {
        # Interceptors are not a curated unit type, so nothing is derived for them. Each costs 15, charged when the
        # carrier is ordered to build it.
        AbilityId.CARRIER_BUILD_INTERCEPTORS: Cost(15, 0),
        # The nova's row carries the disruptor's 3 supply; a nova takes none.
        AbilityId.DISRUPTOR_PURIFICATION_NOVA: Cost(0, 0),
        # A drone's 50 is folded into the row of what it becomes (an extractor's row reads 75), so a hatchery's
        # should read 350. It reads 325, and taking off the drone leaves 275 where the game charges 300.
        AbilityId.DRONE_MORPH_HATCHERY: Cost(300, 0, -1),
        # A gateway turns itself into a warp gate once the research is done, for nothing. Its row holds the
        # gateway's 150 and names no source type.
        AbilityId.GATEWAY_MORPH_WARP_GATE: Cost(0, 0),
        # A nuke is not a unit, so nothing is derived for it.
        AbilityId.GHOST_ACADEMY_BUILD_NUKE: Cost(100, 100),
        # One order makes two zerglings; the row prices one, with its half supply.
        AbilityId.LARVA_MORPH_ZERGLING: Cost(50, 0, 1),
        # The transport's row reads 100, the same as an overlord's, so the derived difference is nothing.
        AbilityId.OVERLORD_MORPH_OVERLORD_TRANSPORT: Cost(25, 25),
        # A turret is paid for with the raven's energy; the row keeps a price the game no longer charges.
        AbilityId.RAVEN_SPAWN_AUTO_TURRET: Cost(0, 0),
    }
)
"""The cost of ordering an ability, where the tables get it wrong. Every other cost is derived as the product's cost
less its source type's, which is right for every other morph: an orbital command 150 of its row's 550, with its cancel
giving back 113 (tool `sweep_orders`), an extractor 25 of 75, a baneling 25/25. A nuke and an interceptor have no row
to derive from. These are the prices the game has always charged (stated). `tests/test_gamedata.py` checks that every
entry differs from what the tables derive, so an entry goes as soon as a patch makes it unnecessary."""
