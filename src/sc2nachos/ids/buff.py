"""Buff identifiers.

Hand-maintained: filtered to what multiplayer needs, named for readability. Each member is defined by a raw
catalog member, never a literal id, or is a custom id, assigned `auto()`, which the game's ids read as. Unknown ids
raise.
"""

from enum import auto, nonmember
from types import MappingProxyType

from sc2nachos.ids._id_enum import IdEnum
from sc2nachos.ids.raw import RawBuffId


class BuffId(IdEnum):
    """Buff ids used in multiplayer games."""

    ACCELERATION_ZONE = RawBuffId.AccelerationZoneTemporalField
    ACCELERATION_ZONE_FLYING = RawBuffId.AccelerationZoneFlyingTemporalField
    BANSHEE_CLOAK = RawBuffId.BansheeCloak
    # From a plain geyser, under a game id for each race's worker; none comes from a rich one.
    CARRYING_GAS = auto()
    CARRYING_MINERALS = RawBuffId.CarryMineralFieldMinerals
    CARRYING_MINERALS_RICH = RawBuffId.CarryHighYieldMineralFieldMinerals
    CYCLONE_LOCKED_ON = RawBuffId.LockOn
    GHOST_CLOAK = RawBuffId.GhostCloak
    GHOST_EMP_DECLOAK = RawBuffId.EMPDecloak
    GHOST_HOLD_FIRE = RawBuffId.GhostHoldFireB
    GHOST_SNIPE = RawBuffId.ChannelSnipeCombat  # on the ghost, while it channels
    HIGH_TEMPLAR_STORM = RawBuffId.PsiStorm
    HYDRALISK_LUNGE = RawBuffId.HydraliskFrenzy
    IMMORTAL_BARRIER = RawBuffId.TakenDamage
    INFESTOR_FUNGAL_GROWTH = RawBuffId.FungalGrowth
    INFESTOR_MICROBIAL_SHROUD = RawBuffId.AmorphousArmorcloud
    INFESTOR_NEURAL_PARASITE = RawBuffId.NeuralParasite
    INHIBITOR_ZONE = RawBuffId.InhibitorZoneTemporalField
    INHIBITOR_ZONE_FLYING = RawBuffId.InhibitorZoneFlyingTemporalField
    LURKER_HOLD_FIRE = RawBuffId.LurkerHoldFireB
    MARAUDER_CONCUSSIVE_SHELLS_SLOW = RawBuffId.Slow
    MARAUDER_STIMMED = RawBuffId.StimpackMarauder
    MARINE_STIMMED = RawBuffId.Stimpack
    MEDIVAC_BOOST = RawBuffId.MedivacSpeedBoost
    MOTHERSHIP_CLOAK_FIELD = RawBuffId.CloakField  # on the mothership
    MOTHERSHIP_CLOAK_FIELD_CLOAKED = RawBuffId.CloakFieldEffect  # on each unit the field cloaks
    MOTHERSHIP_TIME_WARP = RawBuffId.TemporalField
    NEXUS_CHRONO_BOOST = RawBuffId.ChronoBoostEnergyCost
    ORACLE_PULSAR_BEAM = RawBuffId.OracleWeapon
    ORACLE_REVELATION = RawBuffId.OracleRevelation
    ORBITAL_COMMAND_SUPPLY_DROP = RawBuffId.SupplyDrop
    OVERSEER_CONTAMINATED = RawBuffId.Contaminated
    PHOENIX_GRAVITON_BEAM = RawBuffId.GravitonBeam
    QUEEN_INJECTED = RawBuffId.QueenSpawnLarvaTimer  # counts down to the larvae, then again for a queued inject
    QUEEN_TRANSFUSED = RawBuffId.Transfusion
    RAVEN_ANTI_ARMOR_MISSILE = RawBuffId.RavenShredderMissileArmorReductionUISubtruct
    RAVEN_ANTI_ARMOR_MISSILE_INCOMING = RawBuffId.RavenShredderMissileTint  # on its target, until the missile lands
    RAVEN_INTERFERENCE_MATRIX = RawBuffId.RavenScramblerMissile
    SENTRY_GUARDIAN_SHIELD = RawBuffId.GuardianShield
    VIPER_BLINDING_CLOUD = RawBuffId.BlindingCloud
    VIPER_PARASITIC_BOMB = RawBuffId.ParasiticBomb
    VOID_RAY_PRISMATIC_ALIGNMENT = RawBuffId.VoidRaySwarmDamageBoost
    WARP_GATE_WARPING_IN = RawBuffId.PowerUserWarpable  # on a unit warping in, until the step after it finishes
    ZEALOT_CHARGING = RawBuffId.Charging  # on the zealot

    # Each race's worker carries gas under a game id of its own (in game).
    _REMAPPED_IDS = nonmember(
        MappingProxyType(
            {
                RawBuffId.CarryHarvestableVespeneGeyserGas: CARRYING_GAS,
                RawBuffId.CarryHarvestableVespeneGeyserGasProtoss: CARRYING_GAS,
                RawBuffId.CarryHarvestableVespeneGeyserGasZerg: CARRYING_GAS,
            }
        )
    )
