"""Hand-written corrections to what the game's tables get wrong or leave out. Never generated.

`tools/generate_tech_tree.py` writes none of this, so a new sweep keeps every entry. Each entry is checked against the
sweep: the generator refuses to write the tech tree unless the sweep saw each creation ability here make its unit
type, so a patch that breaks one stops the regeneration. `tests/test_gamedata.py` fails once the game's table names an
ability here for its type, so the entry can go.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from sc2nachos.game_time import GameTime
from sc2nachos.gamedata._cost import Cost
from sc2nachos.gamedata._sent_as import SentAs
from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId
from sc2nachos.ids.raw import RawAbilityId

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
        # A structure on a rich geyser is built by the same ability as on any other geyser, and the tables name none.
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

SELF_MORPHS: Final[Mapping[AbilityId, UnitTypeId]] = MappingProxyType(
    {
        # The game orders every gateway to turn into a warp gate once the research is done, so the tech tree's sweep,
        # which never turned one back, saw no gateway offered it. One turned back from a warp gate is offered it, and
        # takes it (in game, #159).
        AbilityId.GATEWAY_MORPH_WARP_GATE: UnitTypeId.GATEWAY,
    }
)
"""The unit type that carries out each ability the tech tree's sweep never saw it offered, turning itself into the
ability's product, which takes it as its source."""

PRODUCERS_FALLBACK: Final[Mapping[UnitTypeId, frozenset[UnitTypeId]]] = MappingProxyType(
    {
        # A templar alone is never offered `MORPH_ARCHON` (tool `sweep_tech_tree`), so the tech tree records nothing
        # making an archon, though two given it in one command, high or dark, merge into one (in game).
        UnitTypeId.ARCHON: frozenset({UnitTypeId.HIGH_TEMPLAR, UnitTypeId.DARK_TEMPLAR}),
    }
)
"""The unit types that make each unit type the tech tree records nothing making."""

# Each of these, ordered unqueued while a unit moved, was carried out while the move went on; every other ability a
# unit is offered replaced its orders (tool `sweep_orders`). `tools/generate_tech_tree.py` does not write this, and no
# ability belongs here that a sweep has not seen keep a moving unit's orders.
#
# This is not the whole of `OrderBehavior.KEEPS_ORDERS`: `gamedata/_ability_data.py` also reads an ability that makes
# nothing and is offered only to types that hold no order of their own as keeping orders, which covers a structure's
# own rally, load, cancel and energy casts. An action several types perform is here where it keeps every type's orders;
# where it keeps only some types', those are in `KEEPS_ORDERS_BY_TYPE`.
#
# A toggle is here in both halves: a unit is offered the off half only once the on half has taken, so each was given in
# turn to a moving unit, and both left its move first in its orders. Hold fire keeps a ghost's orders and takes a
# burrowed lurker off its attack, so it is in `KEEPS_ORDERS_BY_TYPE`; the lurker's could not be given while it moved,
# since the game offers a burrowed lurker no move.
KEEPS_ORDERS_ABILITIES: Final[frozenset[AbilityId]] = frozenset(
    {
        AbilityId.ADEPT_SHADE,
        AbilityId.BANELING_ATTACK_STRUCTURES_OFF,
        AbilityId.BANELING_ATTACK_STRUCTURES_ON,
        AbilityId.CLOAK_OFF,
        AbilityId.CLOAK_ON,
        AbilityId.STIM,
        AbilityId.HYDRALISK_LUNGE,
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

# Each of these, given to a unit of these types while it held an order, left that order alone (tool `sweep_orders`,
# `keeps-terran`, `held-orders` and `structure-orders`), where the tables alone read it as replacing them. A ghost's
# hold fire, both halves, keeps a moving ghost's move first in its orders. A planetary fortress attacks
# and stops with the ids every unit does, yet goes on training through both, the attack shown behind its trains. A
# command center and a planetary fortress go on training while they unload, by an id the tables offer to nobody, since
# unload is offered only to a loaded transport, and while they load. Every structure that trains goes on training
# through a smart, at a point or a mineral field, which sets its rally. The ids are offered to units too, which they
# take off their orders, and load-all to a flying command center, which was not tried.
#
# A bunker is in neither set: its own load and unload are offered only to it, a type that holds no order of its own,
# so `gamedata/_ability_data.py` already reads them as keeping its orders, and it is offered no load-all.
_PRODUCTION_STRUCTURES = frozenset(
    {
        UnitTypeId.BARRACKS,
        UnitTypeId.FACTORY,
        UnitTypeId.STARPORT,
        UnitTypeId.COMMAND_CENTER,
        UnitTypeId.ORBITAL_COMMAND,
        UnitTypeId.PLANETARY_FORTRESS,
        UnitTypeId.NEXUS,
        UnitTypeId.GATEWAY,
        UnitTypeId.ROBOTICS_FACILITY,
        UnitTypeId.STARGATE,
        UnitTypeId.HATCHERY,
        UnitTypeId.LAIR,
        UnitTypeId.HIVE,
    }
)
_LOAD_ALL_STRUCTURES = frozenset({UnitTypeId.COMMAND_CENTER, UnitTypeId.PLANETARY_FORTRESS})
KEEPS_ORDERS_BY_TYPE: Final[Mapping[AbilityId, frozenset[UnitTypeId]]] = MappingProxyType(
    {
        AbilityId.ATTACK: frozenset({UnitTypeId.PLANETARY_FORTRESS}),
        AbilityId.HOLD_FIRE_OFF: frozenset({UnitTypeId.GHOST}),
        AbilityId.HOLD_FIRE_ON: frozenset({UnitTypeId.GHOST}),
        AbilityId.LOAD_ALL: _LOAD_ALL_STRUCTURES,
        AbilityId.SMART: _PRODUCTION_STRUCTURES,
        AbilityId.STOP: frozenset({UnitTypeId.PLANETARY_FORTRESS}),
        AbilityId.UNLOAD: _LOAD_ALL_STRUCTURES,
    }
)


# A medivac, a warp prism and a transport overlord given their unload at a point aimed at themselves unload where they
# are and leave a move going; aimed at a point, they fly there first. Each answers UnloadAll `Error`, which a bunker, a
# command center, a planetary fortress and a nydus take (tool `sweep_orders`, `held-orders`).
_UNLOADS_IN_PLACE = SentAs(AbilityId.UNLOAD_AT, aimed_at_itself=True)
_TRANSPORTS = (UnitTypeId.MEDIVAC, UnitTypeId.WARP_PRISM, UnitTypeId.WARP_PRISM_PHASING, UnitTypeId.OVERLORD_TRANSPORT)

ABILITIES_SENT_AS_ANOTHER: Final[Mapping[AbilityId, Mapping[UnitTypeId, SentAs]]] = MappingProxyType(
    {
        AbilityId.UNLOAD: MappingProxyType(dict.fromkeys(_TRANSPORTS, _UNLOADS_IN_PLACE)),
        # A liberator's siege is `LIBERATOR_SIEGE_AT`, aimed at the zone it guards. A form given its own siege again is
        # sent it too, and the game answers as it does.
        AbilityId.SIEGE: MappingProxyType(
            {
                **dict.fromkeys(
                    (UnitTypeId.SIEGE_TANK, UnitTypeId.SIEGE_TANK_SIEGED),
                    SentAs(RawAbilityId.SiegeMode_SiegeMode),
                ),
                **dict.fromkeys(
                    (UnitTypeId.OBSERVER, UnitTypeId.OBSERVER_SIEGED),
                    SentAs(RawAbilityId.Morph_SurveillanceMode),
                ),
                **dict.fromkeys(
                    (UnitTypeId.OVERSEER, UnitTypeId.OVERSEER_SIEGED),
                    SentAs(RawAbilityId.Morph_OversightMode),
                ),
            }
        ),
        AbilityId.UNSIEGE: MappingProxyType(
            {
                **dict.fromkeys(
                    (UnitTypeId.SIEGE_TANK_SIEGED, UnitTypeId.SIEGE_TANK),
                    SentAs(RawAbilityId.Unsiege_Unsiege),
                ),
                **dict.fromkeys(
                    (UnitTypeId.LIBERATOR_SIEGED, UnitTypeId.LIBERATOR),
                    SentAs(RawAbilityId.Morph_LiberatorAAMode),
                ),
                **dict.fromkeys(
                    (UnitTypeId.OBSERVER_SIEGED, UnitTypeId.OBSERVER),
                    SentAs(RawAbilityId.Morph_ObserverMode),
                ),
                **dict.fromkeys(
                    (UnitTypeId.OVERSEER_SIEGED, UnitTypeId.OVERSEER),
                    SentAs(RawAbilityId.Morph_OverseerMode),
                ),
            }
        ),
    }
)
"""The game's ability each ability goes out as for the unit types named, and what it is aimed at; any other type is
sent the ability itself. A custom id names every type it can be given to. A group of several types gets one command
for each ability it goes out as, or one per unit where that is aimed at the unit itself."""

COST_OVERRIDES: Final[Mapping[AbilityId, Cost]] = MappingProxyType(
    {
        # Nothing makes an interceptor in the tech tree: the table names no ability for it, and the sweep never saw a
        # carrier offered Build Interceptors (tool `sweep_tech_tree`). Each costs 15, charged when the carrier is
        # ordered to build it. Its row gives it no build steps.
        AbilityId.CARRIER_BUILD_INTERCEPTORS: Cost(15, 0),
        # A nuke is not a unit, so nothing is derived for it, nor its steps.
        AbilityId.GHOST_ACADEMY_BUILD_NUKE: Cost(100, 100),
        # One order makes two zerglings; the row prices one, with its half supply.
        AbilityId.LARVA_MORPH_ZERGLING: Cost(50, 0, 1, time=GameTime(steps=384)),
    }
)
"""The cost of ordering an ability, where its product does not say. Every other cost is that of the step that makes
the product, `UNIT_TYPE_COST_OVERRIDES` included, or an upgrade's cost. A nuke has no row to derive from, an interceptor
no ability that makes it, and a larva's order makes two zerglings. These are the costs the game has always had
(stated), with the steps the tables give what each makes: a zergling's 384. `tests/test_gamedata.py` checks that every
entry differs from what the tables derive, so an entry goes as soon as a patch makes it unnecessary."""

UNIT_TYPE_COST_OVERRIDES: Final[Mapping[UnitTypeId, Cost]] = MappingProxyType(
    {
        # Two high templar merged into one for nothing (tool `sweep_orders`); the row is one high and one dark
        # templar's, 175/275.
        UnitTypeId.ARCHON: Cost(0, 0),
        # A turret is paid for with the raven's energy (#23); the row keeps 100, which the game prices a repair from
        # (tool `sweep_repair`).
        UnitTypeId.AUTO_TURRET: Cost(0, 0),
        # A drone's 50 is folded into the row of what it becomes (an extractor's row reads 75), so a hatchery's
        # should read 350. It reads 325, and taking off the drone leaves 275 where the morph costs 300.
        UnitTypeId.HATCHERY: Cost(300, 0, -1),
        # Called down for 50 energy and not a mineral (tool `sweep_orders`); the row's 50 is what the game prices a
        # repair from (tool `sweep_repair`).
        UnitTypeId.MULE: Cost(0, 0),
        # The transport's row reads 100, the same as an overlord's, so the derived difference is nothing, though the
        # morph costs 25/25.
        UnitTypeId.OVERLORD_TRANSPORT: Cost(25, 25),
        # The nova's row carries the disruptor's 3 supply; a nova takes none.
        UnitTypeId.PURIFICATION_NOVA: Cost(0, 0),
    }
)
"""The cost of the step that makes each of these unit types, its minerals, vespene and supply, where the tables get it
wrong. Every other step costs the type's row less its source type's, which is right for every other morph: an orbital
command 150 of its row's 550, with its cancel giving back 113 (tool `sweep_orders`), an extractor 25 of 75, a baneling
25/25. The time is the type's build time. `tests/test_gamedata.py` checks that every entry differs from what the tables
derive."""

COCOON_PRODUCTS: Final[Mapping[UnitTypeId, UnitTypeId]] = MappingProxyType(
    {
        UnitTypeId.BANELING_COCOON: UnitTypeId.BANELING,
        UnitTypeId.BROOD_LORD_COCOON: UnitTypeId.BROOD_LORD,
        UnitTypeId.LURKER_EGG: UnitTypeId.LURKER,
        UnitTypeId.OVERLORD_COCOON: UnitTypeId.OVERSEER,
        UnitTypeId.OVERLORD_TRANSPORT_COCOON: UnitTypeId.OVERLORD_TRANSPORT,
        UnitTypeId.RAVAGER_COCOON: UnitTypeId.RAVAGER,
    }
)
"""The type each cocoon becomes: a zergling a baneling through a `BANELING_COCOON`, and an overlord an overseer through
an `OVERLORD_COCOON` (#37, in game); the rest are named for what they become."""

# The abilities besides a build that put a structure or a unit at the point they are aimed at, where only one of several
# units given it can, as `AbilityData.places_at_target` reads them: a landing, a rooting and a warp-in (tool
# `sweep_orders`, `one-command-each`). The game's tables mark none of them as placing anything.
ABILITIES_PLACING_AT_THE_TARGET: Final[frozenset[AbilityId]] = frozenset(
    {
        AbilityId.LAND,
        AbilityId.ROOT,
        AbilityId.WARP_GATE_WARP_IN_ADEPT,
        AbilityId.WARP_GATE_WARP_IN_DARK_TEMPLAR,
        AbilityId.WARP_GATE_WARP_IN_HIGH_TEMPLAR,
        AbilityId.WARP_GATE_WARP_IN_SENTRY,
        AbilityId.WARP_GATE_WARP_IN_STALKER,
        AbilityId.WARP_GATE_WARP_IN_ZEALOT,
    }
)

# The abilities every unit of a command naming several carries out, as `AbilityData.multi_unit` reads them (tool
# `sweep_orders`, `group-abilities-terran`, `-protoss` and `-zerg`), with the other half of each toggle, a return,
# a locust's swoop and an archon's merge, which needs two. None of them costs minerals or vespene.
MULTI_UNIT_ABILITIES: Final[frozenset[AbilityId]] = frozenset(
    {
        AbilityId.ADEPT_SHADE,
        AbilityId.ATTACK,
        AbilityId.BANELING_ATTACK_STRUCTURES_OFF,
        AbilityId.BANELING_ATTACK_STRUCTURES_ON,
        AbilityId.BANELING_EXPLODE,
        AbilityId.BATTLECRUISER_TACTICAL_JUMP,
        AbilityId.BLINK,
        AbilityId.BURROW,
        AbilityId.CANCEL,
        AbilityId.CLOAK_OFF,
        AbilityId.CLOAK_ON,
        AbilityId.GATHER,
        AbilityId.HELLBAT_MORPH_HELLION,
        AbilityId.HELLION_MORPH_HELLBAT,
        AbilityId.HOLD_FIRE_OFF,
        AbilityId.HOLD_FIRE_ON,
        AbilityId.HOLD_POSITION,
        AbilityId.HYDRALISK_LUNGE,
        AbilityId.LIFT,
        AbilityId.LOAD_ALL,
        AbilityId.LOCUST_SWOOP,
        AbilityId.MEDIVAC_BOOST,
        AbilityId.MORPH_ARCHON,
        AbilityId.MOTHERSHIP_CLOAK_FIELD,
        AbilityId.MOVE,
        AbilityId.ORACLE_PULSAR_BEAM_OFF,
        AbilityId.ORACLE_PULSAR_BEAM_ON,
        AbilityId.OVERLORD_CREEP_OFF,
        AbilityId.OVERLORD_CREEP_ON,
        AbilityId.PATROL,
        AbilityId.RALLY_UNITS,
        AbilityId.RALLY_WORKERS,
        AbilityId.REPAIR,
        AbilityId.RETURN,
        AbilityId.SIEGE,
        AbilityId.SMART,
        AbilityId.STIM,
        AbilityId.STOP,
        AbilityId.SUPPLY_DEPOT_LOWER,
        AbilityId.SUPPLY_DEPOT_RAISE,
        AbilityId.SWARM_HOST_SPAWN_LOCUST,
        AbilityId.THOR_EXPLOSIVE_MODE,
        AbilityId.THOR_HIGH_IMPACT_MODE,
        AbilityId.UNBURROW,
        AbilityId.UNLOAD,
        AbilityId.UNLOAD_AT,
        AbilityId.UNSIEGE,
        AbilityId.UPROOT,
        AbilityId.VIKING_LAND,
        AbilityId.VIKING_LIFT,
        AbilityId.VOID_RAY_PRISMATIC_ALIGNMENT,
        AbilityId.ZEALOT_CHARGE,
    }
)
