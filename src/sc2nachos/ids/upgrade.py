"""Upgrade identifiers.

Hand-maintained: filtered to what multiplayer needs, named for readability. Each member is defined by a raw
catalog member, never a literal id. Unknown ids raise.
"""

from sc2nachos._enum import ReadableIntEnum
from sc2nachos.ids.raw import RawUpgradeId


class UpgradeId(ReadableIntEnum):
    """Upgrade ids used in multiplayer games."""

    ADRENAL_GLANDS = RawUpgradeId.zerglingattackspeed  # "Adrenal Glands" in game
    BANELING_SPEED = RawUpgradeId.CentrificalHooks  # "Centrifugal Hooks" in game
    BANSHEE_CLOAK = RawUpgradeId.BansheeCloak  # "Cloaking Field" in game
    BANSHEE_SPEED = RawUpgradeId.BansheeSpeed  # "Hyperflight Rotors" in game
    BLINK = RawUpgradeId.BlinkTech
    BLUE_FLAME = RawUpgradeId.HighCapacityBarrels  # "Infernal Pre-Igniter" in game
    BUILDING_ARMOR = RawUpgradeId.TerranBuildingArmor
    BURROW = RawUpgradeId.Burrow
    CHARGE = RawUpgradeId.Charge
    COLOSSUS_RANGE = RawUpgradeId.ExtendedThermalLance  # "Extended Thermal Lance" in game
    COMBAT_SHIELD = RawUpgradeId.ShieldWall
    CONCUSSIVE_SHELLS = RawUpgradeId.PunisherGrenades
    CYCLONE_LOCK_ON_DAMAGE = RawUpgradeId.CycloneLockOnDamageUpgrade  # "Mag-Field Accelerator" in game
    DARK_TEMPLAR_BLINK = RawUpgradeId.DarkTemplarBlinkUpgrade  # "Shadow Stride" in game
    DRILLING_CLAWS = RawUpgradeId.DrillClaws
    GHOST_CLOAK = RawUpgradeId.PersonalCloaking
    GLAIVES = RawUpgradeId.AdeptPiercingAttack  # "Resonating Glaives" in game
    HISEC_AUTO_TRACKING = RawUpgradeId.HiSecAutoTracking
    HYDRALISK_LUNGE = RawUpgradeId.Frenzy  # "Nanomuscular Swell" in game
    HYDRALISK_RANGE = RawUpgradeId.EvolveGroovedSpines  # "Grooved Spines" in game
    HYDRALISK_SPEED = RawUpgradeId.EvolveMuscularAugments  # "Muscular Augments" in game
    INTERFERENCE_MATRIX = RawUpgradeId.InterferenceMatrix
    LIBERATOR_RANGE = RawUpgradeId.LiberatorAGRangeUpgrade  # "Advanced Ballistics" in game
    LURKER_BURROW_SPEED = RawUpgradeId.DiggingClaws  # "Adaptive Talons" in game
    LURKER_RANGE = RawUpgradeId.LurkerRange  # "Seismic Spines" in game
    MEDIVAC_ENERGY_REGENERATION = RawUpgradeId.MedivacCaduceusReactor  # "Caduceus Reactor" in game
    NEURAL_PARASITE = RawUpgradeId.NeuralParasite
    OBSERVER_SPEED = RawUpgradeId.ObserverGraviticBooster  # "Gravitic Boosters" in game
    OVERLORD_SPEED = RawUpgradeId.overlordspeed  # "Pneumatized Carapace" in game
    PHOENIX_RANGE = RawUpgradeId.PhoenixRangeUpgrade  # "Anion Pulse-Crystals" in game
    PROTOSS_AIR_ARMOR_1 = RawUpgradeId.ProtossAirArmorsLevel1
    PROTOSS_AIR_ARMOR_2 = RawUpgradeId.ProtossAirArmorsLevel2
    PROTOSS_AIR_ARMOR_3 = RawUpgradeId.ProtossAirArmorsLevel3
    PROTOSS_AIR_WEAPONS_1 = RawUpgradeId.ProtossAirWeaponsLevel1
    PROTOSS_AIR_WEAPONS_2 = RawUpgradeId.ProtossAirWeaponsLevel2
    PROTOSS_AIR_WEAPONS_3 = RawUpgradeId.ProtossAirWeaponsLevel3
    PROTOSS_GROUND_ARMOR_1 = RawUpgradeId.ProtossGroundArmorsLevel1
    PROTOSS_GROUND_ARMOR_2 = RawUpgradeId.ProtossGroundArmorsLevel2
    PROTOSS_GROUND_ARMOR_3 = RawUpgradeId.ProtossGroundArmorsLevel3
    PROTOSS_GROUND_WEAPONS_1 = RawUpgradeId.ProtossGroundWeaponsLevel1
    PROTOSS_GROUND_WEAPONS_2 = RawUpgradeId.ProtossGroundWeaponsLevel2
    PROTOSS_GROUND_WEAPONS_3 = RawUpgradeId.ProtossGroundWeaponsLevel3
    PROTOSS_SHIELDS_1 = RawUpgradeId.ProtossShieldsLevel1
    PROTOSS_SHIELDS_2 = RawUpgradeId.ProtossShieldsLevel2
    PROTOSS_SHIELDS_3 = RawUpgradeId.ProtossShieldsLevel3
    ROACH_SPEED = RawUpgradeId.GlialReconstitution
    SMART_SERVOS = RawUpgradeId.SmartServos
    STIMPACK = RawUpgradeId.Stimpack
    STORM = RawUpgradeId.PsiStormTech
    TEMPEST_BUILDING_DAMAGE = RawUpgradeId.TempestGroundAttackUpgrade  # "Tectonic Destabilizers" in game
    TERRAN_INFANTRY_ARMOR_1 = RawUpgradeId.TerranInfantryArmorsLevel1
    TERRAN_INFANTRY_ARMOR_2 = RawUpgradeId.TerranInfantryArmorsLevel2
    TERRAN_INFANTRY_ARMOR_3 = RawUpgradeId.TerranInfantryArmorsLevel3
    TERRAN_INFANTRY_WEAPONS_1 = RawUpgradeId.TerranInfantryWeaponsLevel1
    TERRAN_INFANTRY_WEAPONS_2 = RawUpgradeId.TerranInfantryWeaponsLevel2
    TERRAN_INFANTRY_WEAPONS_3 = RawUpgradeId.TerranInfantryWeaponsLevel3
    TERRAN_SHIP_WEAPONS_1 = RawUpgradeId.TerranShipWeaponsLevel1
    TERRAN_SHIP_WEAPONS_2 = RawUpgradeId.TerranShipWeaponsLevel2
    TERRAN_SHIP_WEAPONS_3 = RawUpgradeId.TerranShipWeaponsLevel3
    TERRAN_VEHICLE_AND_SHIP_ARMOR_1 = RawUpgradeId.TerranVehicleAndShipArmorsLevel1  # "Plating" in game
    TERRAN_VEHICLE_AND_SHIP_ARMOR_2 = RawUpgradeId.TerranVehicleAndShipArmorsLevel2
    TERRAN_VEHICLE_AND_SHIP_ARMOR_3 = RawUpgradeId.TerranVehicleAndShipArmorsLevel3
    TERRAN_VEHICLE_WEAPONS_1 = RawUpgradeId.TerranVehicleWeaponsLevel1
    TERRAN_VEHICLE_WEAPONS_2 = RawUpgradeId.TerranVehicleWeaponsLevel2
    TERRAN_VEHICLE_WEAPONS_3 = RawUpgradeId.TerranVehicleWeaponsLevel3
    TUNNELING_CLAWS = RawUpgradeId.TunnelingClaws
    ULTRALISK_ARMOR = RawUpgradeId.ChitinousPlating
    ULTRALISK_SPEED = RawUpgradeId.AnabolicSynthesis
    VOID_RAY_SPEED = RawUpgradeId.VoidRaySpeedUpgrade  # "Flux Vanes" in game
    WARP_GATE = RawUpgradeId.WarpGateResearch
    WARP_PRISM_SPEED = RawUpgradeId.GraviticDrive  # "Gravitic Drive" in game
    YAMATO_CANNON = RawUpgradeId.BattlecruiserEnableSpecializations  # "Weapon Refit" in game
    ZERGLING_SPEED = RawUpgradeId.zerglingmovementspeed  # "Metabolic Boost" in game
    ZERG_AIR_ARMOR_1 = RawUpgradeId.ZergFlyerArmorsLevel1
    ZERG_AIR_ARMOR_2 = RawUpgradeId.ZergFlyerArmorsLevel2
    ZERG_AIR_ARMOR_3 = RawUpgradeId.ZergFlyerArmorsLevel3
    ZERG_AIR_WEAPONS_1 = RawUpgradeId.ZergFlyerWeaponsLevel1
    ZERG_AIR_WEAPONS_2 = RawUpgradeId.ZergFlyerWeaponsLevel2
    ZERG_AIR_WEAPONS_3 = RawUpgradeId.ZergFlyerWeaponsLevel3
    ZERG_GROUND_ARMOR_1 = RawUpgradeId.ZergGroundArmorsLevel1
    ZERG_GROUND_ARMOR_2 = RawUpgradeId.ZergGroundArmorsLevel2
    ZERG_GROUND_ARMOR_3 = RawUpgradeId.ZergGroundArmorsLevel3
    ZERG_MELEE_WEAPONS_1 = RawUpgradeId.ZergMeleeWeaponsLevel1
    ZERG_MELEE_WEAPONS_2 = RawUpgradeId.ZergMeleeWeaponsLevel2
    ZERG_MELEE_WEAPONS_3 = RawUpgradeId.ZergMeleeWeaponsLevel3
    ZERG_RANGE_WEAPONS_1 = RawUpgradeId.ZergMissileWeaponsLevel1
    ZERG_RANGE_WEAPONS_2 = RawUpgradeId.ZergMissileWeaponsLevel2
    ZERG_RANGE_WEAPONS_3 = RawUpgradeId.ZergMissileWeaponsLevel3
