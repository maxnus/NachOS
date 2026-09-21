"""What the game's own tables get wrong or leave out, hand-written and never generated.

`tools/generate_tech_tree.py` writes none of this, so a new sweep keeps every entry. Each is held to the sweep that
found it: the generator refuses to write the tech tree unless the sweep saw each creation ability here make its unit
type, so a patch that breaks one stops the regeneration, and `tests/test_gamedata.py` fails once the game's table
names an ability here for its type, so the entry can go.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from sc2nachos.gamedata._resources import Resources
from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId

MISNAMED_RESEARCH_ABILITIES: Final[Mapping[UpgradeId, AbilityId]] = MappingProxyType(
    {
        # The upgrade table says these three are researched by the `ArmoryResearchSwarm` spelling, which an armory
        # is never offered and which does nothing when ordered; it is uncurated, so without this the upgrade names
        # no ability and the ability that does research it makes nothing. `ArmoryResearch` is what an armory offers
        # and runs (#23, #28; tested).
        UpgradeId.TERRAN_VEHICLE_AND_SHIP_ARMOR_1: AbilityId.ARMORY_RESEARCH_VEHICLE_AND_SHIP_ARMOR_1,
        UpgradeId.TERRAN_VEHICLE_AND_SHIP_ARMOR_2: AbilityId.ARMORY_RESEARCH_VEHICLE_AND_SHIP_ARMOR_2,
        UpgradeId.TERRAN_VEHICLE_AND_SHIP_ARMOR_3: AbilityId.ARMORY_RESEARCH_VEHICLE_AND_SHIP_ARMOR_3,
    }
)
"""The ability that researches each upgrade the game's own table names a dead id for.

A nuke has no entry of its own kind: `GHOST_ACADEMY_BUILD_NUKE` makes something the curated unit types leave out,
so there is no product to name it, and NachOS reads it as an ability that makes nothing.
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
        # A structure on a rich geyser is put up by the same ability as on any other. The terran table names a
        # duplicate row an SCV is never offered, TerranBuild_Refinery_325, and the other two name nothing.
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
"""The unit type each ability makes, each seen doing so in game. Where the table names no working ability for the type,
this one is its creation ability, and otherwise it makes the type besides the one the table names."""

# Ordered unqueued while a unit moves, each of these was carried out and the move went on, where every other ability
# a unit is offered replaced its orders (tool `sweep_orders`). `tools/generate_tech_tree.py` does not write this, and
# no ability belongs here that a sweep has not seen keep a moving unit's orders.
#
# This is not the whole of `OrderBehavior.KEEPS_ORDERS`: `gamedata/_ability.py` reads an ability that makes nothing
# and is offered only to a type the game offers no move as keeping its orders too, which is a structure's own rally,
# load, cancel and energy casts. The general ids these remap to are not measured; a general id keeps a unit's orders
# where an ability it stands for does.
#
# A toggle is here in both halves: a unit is offered the half that turns one off only once the half that turns it on
# has taken, so each was given in turn to a unit moving, and both left its move first in its orders. A lurker's hold
# fire is the toggle neither half of which could be given, since it is offered only burrowed, and the game offers a
# burrowed lurker no move.
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


# What the game charges for an ability, where its type's own row does not give it away. Everything else is read off
# the tables: what the ability makes costs, less what it is made out of, which is right for every other morph -- an
# orbital command 150 of its row's 550, an extractor 25 of 75, a baneling 25/25, each held to the three quarters a
# cancel was seen to give back (tool `sweep_orders`). These are the prices the game has always charged (stated), and
# `tests/test_gamedata.py` holds every entry to differing from what the tables derive, so one goes as soon as a patch
# makes it unnecessary.
COST_OVERRIDES: Final[Mapping[AbilityId, Resources]] = MappingProxyType(
    {
        # An interceptor is no unit type the curated ids name, so nothing is derived for it; each costs 15, charged
        # as the carrier is told to build it.
        AbilityId.CARRIER_BUILD_INTERCEPTORS: Resources(15, 0),
        # The game charges 300, but the hatchery's row reads 325, so taking off the 50 of the drone it is made out
        # of leaves 275.
        AbilityId.DRONE_MORPH_HATCHERY: Resources(300, 0),
        # A gateway turns itself into a warp gate once the research is in, and the game charges nothing. Its row
        # keeps the gateway's own 150 and names nothing it is made out of.
        AbilityId.GATEWAY_MORPH_WARP_GATE: Resources(0, 0),
        # A nuke is no unit type the curated ids name either, so nothing is derived for it.
        AbilityId.GHOST_ACADEMY_BUILD_NUKE: Resources(100, 100),
        # One order makes a pair, and the row prices one zergling.
        AbilityId.LARVA_MORPH_ZERGLING: Resources(50, 0),
        # The transport's row reads the same 100 as an overlord's, so the difference comes out as nothing.
        AbilityId.OVERLORD_MORPH_OVERLORD_TRANSPORT: Resources(25, 25),
        # A turret is paid for with the raven's energy; the row keeps a price the game no longer charges.
        AbilityId.RAVEN_SPAWN_AUTO_TURRET: Resources(0, 0),
    }
)
"""What ordering an ability takes, where the game's own rows do not say it."""

# What an ability takes of the supply cap, where its type's row does not give it away.
SUPPLY_OVERRIDES: Final[Mapping[AbilityId, float]] = MappingProxyType(
    {
        # Two zerglings take a supply between them, and the row holds the half one takes.
        AbilityId.LARVA_MORPH_ZERGLING: 1.0,
        # The nova's row carries the disruptor's own 3 supply, and a nova takes none.
        AbilityId.DISRUPTOR_PURIFICATION_NOVA: 0.0,
    }
)
"""What ordering an ability takes of the supply cap, where the game's own rows do not say it."""
