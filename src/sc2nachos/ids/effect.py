"""Effect identifiers.

Hand-maintained: filtered to what multiplayer needs, named for readability. Each member is defined by a raw
catalog member, never a literal id. Unknown ids raise.
"""

from sc2nachos._enum import ReadableIntEnum
from sc2nachos.ids.raw import RawEffectId


class EffectId(ReadableIntEnum):
    """Effect ids used in multiplayer games."""

    COLOSSUS_BEAM = RawEffectId.ThermalLancesForward  # "Thermal Lances" in game
    GHOST_NUKE = RawEffectId.NukePersistent
    HIGH_TEMPLAR_STORM = RawEffectId.PsiStormPersistent
    LIBERATOR_ZONE = RawEffectId.LiberatorTargetMorphPersistent
    LIBERATOR_ZONE_PENDING = RawEffectId.LiberatorTargetMorphDelayPersistent  # placed, firing in 2 seconds
    LURKER_SPINES = RawEffectId.LurkerMP
    MOTHERSHIP_TIME_WARP = RawEffectId.TemporalFieldAfterBubbleCreatePersistent
    # Cast, and slowing in 1.8 seconds.
    MOTHERSHIP_TIME_WARP_PENDING = RawEffectId.TemporalFieldGrowingBubbleCreatePersistent
    ORBITAL_COMMAND_SCAN = RawEffectId.ScannerSweep
    RAVAGER_CORROSIVE_BILE = RawEffectId.RavagerCorrosiveBileCP
    SENTRY_GUARDIAN_SHIELD = RawEffectId.GuardianShieldPersistent
    VIPER_BLINDING_CLOUD = RawEffectId.BlindingCloudCP
