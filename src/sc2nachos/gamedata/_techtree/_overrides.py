"""The abilities that make a unit type where the game's own table names one that does not.

Hand-written, and never generated, so a new sweep of the tech tree keeps every entry. `tools/sweep_tech_tree.py` orders
each ability here in game, and `tools/generate_tech_tree.py` refuses to write the tech tree unless the sweep saw each
make its unit type, so a patch that breaks one stops the regeneration. `tests/test_gamedata.py` fails once the game's
table names a working ability for a type here, and the entry can go.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from sc2nachos.ids import AbilityId, UnitTypeId

CREATION_ABILITY_OVERRIDES: Final[Mapping[UnitTypeId, AbilityId]] = MappingProxyType(
    {
        # The table names MorphZerglingToBaneling (80), which a zergling is never offered and which does nothing.
        UnitTypeId.BANELING: AbilityId.ZERGLING_MORPH_BANELING,
        # The table names LurkerAspectMPFromHydraliskBurrowed (2104), offered to no hydralisk, standing or burrowed.
        UnitTypeId.LURKER: AbilityId.HYDRALISK_MORPH_LURKER,
        # The table names RavenBuild_AutoTurret (349), which no raven is ever offered.
        UnitTypeId.AUTO_TURRET: AbilityId.RAVEN_SPAWN_AUTO_TURRET,
        # The table names SpawnInfestedTerran_LocustMP (2018), which no swarm host is ever offered.
        UnitTypeId.LOCUST: AbilityId.SWARM_HOST_SPAWN_LOCUST,
        # The table names PurificationNovaMorph (2546), which no disruptor is ever offered.
        UnitTypeId.PURIFICATION_NOVA: AbilityId.DISRUPTOR_PURIFICATION_NOVA,
        # A structure on a rich geyser is put up by the same ability as on any other. The terran table names a
        # duplicate row an SCV is never offered, TerranBuild_Refinery_325, and the other two name nothing.
        UnitTypeId.REFINERY_RICH: AbilityId.SCV_BUILD_REFINERY,
        UnitTypeId.ASSIMILATOR_RICH: AbilityId.PROBE_BUILD_ASSIMILATOR,
        UnitTypeId.EXTRACTOR_RICH: AbilityId.DRONE_MORPH_EXTRACTOR,
    }
)
"""For each unit type the table gives no working creation ability, the one that makes it, each seen doing so in game."""
