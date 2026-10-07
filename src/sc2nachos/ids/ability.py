"""Ability identifiers.

Hand-maintained: filtered to what multiplayer needs, named for readability. Each member is defined by a raw
catalog member, never a literal id, except a custom id: one the game has no ability for, which goes out as a game
ability aimed at the unit itself (`AbilityData.sent_as`). A custom id is assigned `auto()`, which numbers it above
every game id, and the game never reports one. Unknown ids raise.

Each names the unit that performs it and then what it does -- SCV_BUILD_BARRACKS, BARRACKS_TRAIN_MARINE,
LARVA_MORPH_ZERGLING -- and GENERAL where several units can. An action several units perform has one id, whichever
unit performs it: GENERAL_LIFT for a barracks and a starport, GENERAL_GATHER for every worker, GENERAL_CANCEL for
whatever a unit is offered a cancel for. It is the id to order, the id a unit is offered and the id it reports; the
game's own id for each unit reads as it (`_REMAPPED_IDS`). A leveled research has an id per level and none for the
next level, as the upgrade it makes does.

Where a unit reports running another id than the one it was ordered, and the game links neither to the other, that id
reads as the one ordered too: a liberator ordered LIBERATOR_SIEGE reports LiberatorMorphtoAG_LiberatorAGMode, which
reads as LIBERATOR_SIEGE.
"""

from enum import auto, nonmember
from types import MappingProxyType

from sc2nachos.ids._id_enum import IdEnum
from sc2nachos.ids.raw import RawAbilityId


class AbilityId(IdEnum):
    """Ability ids used in multiplayer games."""

    ADEPT_SHADE = RawAbilityId.AdeptPhaseShift_AdeptPhaseShift
    ARMORY_RESEARCH_SHIP_WEAPONS_1 = RawAbilityId.ArmoryResearch_TerranShipWeaponsLevel1
    ARMORY_RESEARCH_SHIP_WEAPONS_2 = RawAbilityId.ArmoryResearch_TerranShipWeaponsLevel2
    ARMORY_RESEARCH_SHIP_WEAPONS_3 = RawAbilityId.ArmoryResearch_TerranShipWeaponsLevel3
    # The upgrade table names the ArmoryResearchSwarm ids for these, which an armory is never offered and which do
    # nothing when ordered.
    ARMORY_RESEARCH_VEHICLE_AND_SHIP_ARMOR_1 = RawAbilityId.ArmoryResearch_TerranVehicleAndShipPlatingLevel1
    ARMORY_RESEARCH_VEHICLE_AND_SHIP_ARMOR_2 = RawAbilityId.ArmoryResearch_TerranVehicleAndShipPlatingLevel2
    ARMORY_RESEARCH_VEHICLE_AND_SHIP_ARMOR_3 = RawAbilityId.ArmoryResearch_TerranVehicleAndShipPlatingLevel3
    ARMORY_RESEARCH_VEHICLE_WEAPONS_1 = RawAbilityId.ArmoryResearch_TerranVehicleWeaponsLevel1
    ARMORY_RESEARCH_VEHICLE_WEAPONS_2 = RawAbilityId.ArmoryResearch_TerranVehicleWeaponsLevel2
    ARMORY_RESEARCH_VEHICLE_WEAPONS_3 = RawAbilityId.ArmoryResearch_TerranVehicleWeaponsLevel3
    BANELING_ATTACK_STRUCTURES_OFF = RawAbilityId.Behavior_BuildingAttackOff
    BANELING_ATTACK_STRUCTURES_ON = RawAbilityId.Behavior_BuildingAttackOn
    BANELING_EXPLODE = RawAbilityId.Explode_Explode
    BANELING_NEST_RESEARCH_BANELING_SPEED = RawAbilityId.Research_CentrifugalHooks
    BARRACKS_TECH_LAB_RESEARCH_COMBAT_SHIELD = RawAbilityId.Research_CombatShield
    BARRACKS_TECH_LAB_RESEARCH_CONCUSSIVE_SHELLS = RawAbilityId.Research_ConcussiveShells
    BARRACKS_TECH_LAB_RESEARCH_STIMPACK = RawAbilityId.BarracksTechLabResearch_Stimpack
    BARRACKS_TRAIN_GHOST = RawAbilityId.BarracksTrain_Ghost
    BARRACKS_TRAIN_MARAUDER = RawAbilityId.BarracksTrain_Marauder
    BARRACKS_TRAIN_MARINE = RawAbilityId.BarracksTrain_Marine
    BARRACKS_TRAIN_REAPER = RawAbilityId.BarracksTrain_Reaper
    BATTLECRUISER_TACTICAL_JUMP = RawAbilityId.Effect_TacticalJump
    BATTLECRUISER_YAMATO = RawAbilityId.Yamato_YamatoGun
    CARRIER_BUILD_INTERCEPTORS = RawAbilityId.Build_Interceptors
    COMMAND_CENTER_MORPH_ORBITAL_COMMAND = RawAbilityId.UpgradeToOrbital_OrbitalCommand
    COMMAND_CENTER_MORPH_PLANETARY_FORTRESS = RawAbilityId.UpgradeToPlanetaryFortress_PlanetaryFortress
    COMMAND_CENTER_TRAIN_SCV = RawAbilityId.CommandCenterTrain_SCV
    CORRUPTOR_CAUSTIC_SPRAY = RawAbilityId.CausticSpray_CausticSpray
    CORRUPTOR_MORPH_BROOD_LORD = RawAbilityId.MorphToBroodLord_BroodLord
    CYBERNETICS_CORE_RESEARCH_AIR_ARMOR_1 = RawAbilityId.CyberneticsCoreResearch_ProtossAirArmorLevel1
    CYBERNETICS_CORE_RESEARCH_AIR_ARMOR_2 = RawAbilityId.CyberneticsCoreResearch_ProtossAirArmorLevel2
    CYBERNETICS_CORE_RESEARCH_AIR_ARMOR_3 = RawAbilityId.CyberneticsCoreResearch_ProtossAirArmorLevel3
    CYBERNETICS_CORE_RESEARCH_AIR_WEAPONS_1 = RawAbilityId.CyberneticsCoreResearch_ProtossAirWeaponsLevel1
    CYBERNETICS_CORE_RESEARCH_AIR_WEAPONS_2 = RawAbilityId.CyberneticsCoreResearch_ProtossAirWeaponsLevel2
    CYBERNETICS_CORE_RESEARCH_AIR_WEAPONS_3 = RawAbilityId.CyberneticsCoreResearch_ProtossAirWeaponsLevel3
    CYBERNETICS_CORE_RESEARCH_WARP_GATE = RawAbilityId.Research_WarpGate
    CYCLONE_LOCK_ON = RawAbilityId.LockOn_LockOn
    DARK_SHRINE_RESEARCH_DARK_TEMPLAR_BLINK = RawAbilityId.Research_ShadowStrike
    DISRUPTOR_PURIFICATION_NOVA = RawAbilityId.Effect_PurificationNova
    DRONE_MORPH_BANELING_NEST = RawAbilityId.ZergBuild_BanelingNest
    DRONE_MORPH_EVOLUTION_CHAMBER = RawAbilityId.ZergBuild_EvolutionChamber
    DRONE_MORPH_EXTRACTOR = RawAbilityId.ZergBuild_Extractor
    DRONE_MORPH_HATCHERY = RawAbilityId.ZergBuild_Hatchery
    DRONE_MORPH_HYDRALISK_DEN = RawAbilityId.ZergBuild_HydraliskDen
    DRONE_MORPH_INFESTATION_PIT = RawAbilityId.ZergBuild_InfestationPit
    DRONE_MORPH_LURKER_DEN = RawAbilityId.Build_LurkerDen
    DRONE_MORPH_NYDUS_NETWORK = RawAbilityId.ZergBuild_NydusNetwork
    DRONE_MORPH_ROACH_WARREN = RawAbilityId.ZergBuild_RoachWarren
    DRONE_MORPH_SPAWNING_POOL = RawAbilityId.ZergBuild_SpawningPool
    DRONE_MORPH_SPINE_CRAWLER = RawAbilityId.ZergBuild_SpineCrawler
    DRONE_MORPH_SPIRE = RawAbilityId.ZergBuild_Spire
    DRONE_MORPH_SPORE_CRAWLER = RawAbilityId.ZergBuild_SporeCrawler
    DRONE_MORPH_ULTRALISK_CAVERN = RawAbilityId.ZergBuild_UltraliskCavern
    ENGINEERING_BAY_RESEARCH_BUILDING_ARMOR = RawAbilityId.Research_TerranStructureArmorUpgrade
    ENGINEERING_BAY_RESEARCH_HISEC_AUTO_TRACKING = RawAbilityId.Research_HiSecAutoTracking
    ENGINEERING_BAY_RESEARCH_INFANTRY_ARMOR_1 = RawAbilityId.EngineeringBayResearch_TerranInfantryArmorLevel1
    ENGINEERING_BAY_RESEARCH_INFANTRY_ARMOR_2 = RawAbilityId.EngineeringBayResearch_TerranInfantryArmorLevel2
    ENGINEERING_BAY_RESEARCH_INFANTRY_ARMOR_3 = RawAbilityId.EngineeringBayResearch_TerranInfantryArmorLevel3
    ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_1 = RawAbilityId.EngineeringBayResearch_TerranInfantryWeaponsLevel1
    ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_2 = RawAbilityId.EngineeringBayResearch_TerranInfantryWeaponsLevel2
    ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_3 = RawAbilityId.EngineeringBayResearch_TerranInfantryWeaponsLevel3
    EVOLUTION_CHAMBER_RESEARCH_GROUND_ARMOR_1 = RawAbilityId.Research_ZergGroundArmorLevel1
    EVOLUTION_CHAMBER_RESEARCH_GROUND_ARMOR_2 = RawAbilityId.Research_ZergGroundArmorLevel2
    EVOLUTION_CHAMBER_RESEARCH_GROUND_ARMOR_3 = RawAbilityId.Research_ZergGroundArmorLevel3
    EVOLUTION_CHAMBER_RESEARCH_MELEE_WEAPONS_1 = RawAbilityId.Research_ZergMeleeWeaponsLevel1
    EVOLUTION_CHAMBER_RESEARCH_MELEE_WEAPONS_2 = RawAbilityId.Research_ZergMeleeWeaponsLevel2
    EVOLUTION_CHAMBER_RESEARCH_MELEE_WEAPONS_3 = RawAbilityId.Research_ZergMeleeWeaponsLevel3
    EVOLUTION_CHAMBER_RESEARCH_RANGE_WEAPONS_1 = RawAbilityId.Research_ZergMissileWeaponsLevel1
    EVOLUTION_CHAMBER_RESEARCH_RANGE_WEAPONS_2 = RawAbilityId.Research_ZergMissileWeaponsLevel2
    EVOLUTION_CHAMBER_RESEARCH_RANGE_WEAPONS_3 = RawAbilityId.Research_ZergMissileWeaponsLevel3
    FACTORY_TECH_LAB_RESEARCH_BLUE_FLAME = RawAbilityId.Research_InfernalPreigniter
    FACTORY_TECH_LAB_RESEARCH_CYCLONE_LOCK_ON_DAMAGE = RawAbilityId.Research_CycloneLockOnDamage
    FACTORY_TECH_LAB_RESEARCH_DRILLING_CLAWS = RawAbilityId.Research_DrillingClaws
    FACTORY_TECH_LAB_RESEARCH_SMART_SERVOS = RawAbilityId.Research_SmartServos
    FACTORY_TRAIN_CYCLONE = RawAbilityId.Train_Cyclone
    FACTORY_TRAIN_HELLBAT = RawAbilityId.Train_Hellbat
    FACTORY_TRAIN_HELLION = RawAbilityId.FactoryTrain_Hellion
    FACTORY_TRAIN_SIEGE_TANK = RawAbilityId.FactoryTrain_SiegeTank
    FACTORY_TRAIN_THOR = RawAbilityId.FactoryTrain_Thor
    FACTORY_TRAIN_WIDOW_MINE = RawAbilityId.FactoryTrain_WidowMine
    FLEET_BEACON_RESEARCH_PHOENIX_RANGE = RawAbilityId.Research_PhoenixAnionPulseCrystals
    FLEET_BEACON_RESEARCH_TEMPEST_BUILDING_DAMAGE = RawAbilityId.FleetBeaconResearch_TempestResearchGroundAttackUpgrade
    FLEET_BEACON_RESEARCH_VOID_RAY_SPEED = RawAbilityId.FleetBeaconResearch_ResearchVoidRaySpeedUpgrade
    FORGE_RESEARCH_GROUND_ARMOR_1 = RawAbilityId.ForgeResearch_ProtossGroundArmorLevel1
    FORGE_RESEARCH_GROUND_ARMOR_2 = RawAbilityId.ForgeResearch_ProtossGroundArmorLevel2
    FORGE_RESEARCH_GROUND_ARMOR_3 = RawAbilityId.ForgeResearch_ProtossGroundArmorLevel3
    FORGE_RESEARCH_GROUND_WEAPONS_1 = RawAbilityId.ForgeResearch_ProtossGroundWeaponsLevel1
    FORGE_RESEARCH_GROUND_WEAPONS_2 = RawAbilityId.ForgeResearch_ProtossGroundWeaponsLevel2
    FORGE_RESEARCH_GROUND_WEAPONS_3 = RawAbilityId.ForgeResearch_ProtossGroundWeaponsLevel3
    FORGE_RESEARCH_SHIELDS_1 = RawAbilityId.ForgeResearch_ProtossShieldsLevel1
    FORGE_RESEARCH_SHIELDS_2 = RawAbilityId.ForgeResearch_ProtossShieldsLevel2
    FORGE_RESEARCH_SHIELDS_3 = RawAbilityId.ForgeResearch_ProtossShieldsLevel3
    FUSION_CORE_RESEARCH_LIBERATOR_RANGE = RawAbilityId.FusionCoreResearch_ResearchBallisticRange
    FUSION_CORE_RESEARCH_MEDIVAC_ENERGY_REGENERATION = RawAbilityId.FusionCoreResearch_ResearchMedivacEnergyUpgrade
    FUSION_CORE_RESEARCH_YAMATO_CANNON = RawAbilityId.Research_BattlecruiserWeaponRefit
    GATEWAY_MORPH_WARP_GATE = RawAbilityId.Morph_WarpGate
    GATEWAY_TRAIN_ADEPT = RawAbilityId.Train_Adept
    GATEWAY_TRAIN_DARK_TEMPLAR = RawAbilityId.GatewayTrain_DarkTemplar
    GATEWAY_TRAIN_HIGH_TEMPLAR = RawAbilityId.GatewayTrain_HighTemplar
    GATEWAY_TRAIN_SENTRY = RawAbilityId.GatewayTrain_Sentry
    GATEWAY_TRAIN_STALKER = RawAbilityId.GatewayTrain_Stalker
    GATEWAY_TRAIN_ZEALOT = RawAbilityId.GatewayTrain_Zealot
    # A unit that can fire runs it as an attack, one that cannot as a scan move (in game).
    GENERAL_ATTACK = RawAbilityId.Attack
    GENERAL_BLINK = RawAbilityId.Effect_Blink
    GENERAL_BUILD_CREEP_TUMOR = RawAbilityId.Build_CreepTumor
    GENERAL_BUILD_REACTOR = RawAbilityId.Build_Reactor
    GENERAL_BUILD_TECH_LAB = RawAbilityId.Build_TechLab
    GENERAL_BURROW = RawAbilityId.BurrowDown
    # Cancels whatever a unit is offered a cancel for, beyond a queue: a morph, an add-on, a structure going up, a
    # cocoon or egg, a channel, a shade, a nuke. A unit is offered one at a time (in game).
    GENERAL_CANCEL = RawAbilityId.Cancel
    # Takes the last item off a structure's queue, of units or of research. A structure training answers
    # GENERAL_CANCEL with `Error`; a morph and an add-on answer this one so (in game).
    GENERAL_CANCEL_LAST = RawAbilityId.Cancel_Last
    GENERAL_CLOAK_OFF = RawAbilityId.Behavior_CloakOff
    GENERAL_CLOAK_ON = RawAbilityId.Behavior_CloakOn
    GENERAL_GATHER = RawAbilityId.Harvest_Gather
    GENERAL_HALT = RawAbilityId.Halt
    GENERAL_HOLD_FIRE_OFF = RawAbilityId.Behavior_HoldFireOff
    GENERAL_HOLD_FIRE_ON = RawAbilityId.Behavior_HoldFireOn
    GENERAL_HOLD_POSITION = RawAbilityId.HoldPosition
    GENERAL_LAND = RawAbilityId.Land
    GENERAL_LIFT = RawAbilityId.Lift
    GENERAL_LOAD = RawAbilityId.Load
    GENERAL_LOAD_ALL = RawAbilityId.LoadAll
    # The order to give two templar, high or dark, selected together: they walk to each other and merge into an
    # archon. Given to one alone it is refused. Each reports MORPH_ARCHON running, aimed at the other (in game).
    GENERAL_MORPH_ARCHON = RawAbilityId.Morph_Archon
    GENERAL_MOVE = RawAbilityId.Move
    GENERAL_PATROL = RawAbilityId.Patrol
    GENERAL_RALLY_UNITS = RawAbilityId.Rally_Units
    GENERAL_RALLY_WORKERS = RawAbilityId.Rally_Workers
    GENERAL_RECALL = RawAbilityId.Effect_MassRecall
    GENERAL_REPAIR = RawAbilityId.Effect_Repair
    GENERAL_RETURN = RawAbilityId.Harvest_Return
    GENERAL_ROOT = RawAbilityId.Morph_Root
    GENERAL_SALVAGE = RawAbilityId.SalvageEffect_Salvage
    GENERAL_SMART = RawAbilityId.Smart  # the right-click order
    GENERAL_SPRAY = RawAbilityId.Effect_Spray
    GENERAL_STIM = RawAbilityId.Effect_Stim
    GENERAL_STOP = RawAbilityId.Stop
    GENERAL_UNBURROW = RawAbilityId.BurrowUp
    GENERAL_UNLOAD = RawAbilityId.UnloadAll
    GENERAL_UNLOAD_AT = RawAbilityId.UnloadAllAt
    GENERAL_UNLOAD_IN_PLACE = auto()
    GENERAL_UPROOT = RawAbilityId.Morph_Uproot
    GHOST_ACADEMY_BUILD_NUKE = RawAbilityId.Build_Nuke
    GHOST_ACADEMY_RESEARCH_GHOST_CLOAK = RawAbilityId.Research_PersonalCloaking
    GHOST_EMP = RawAbilityId.EMP_EMP
    GHOST_SNIPE = RawAbilityId.Effect_GhostSnipe
    GHOST_TACTICAL_NUKE = RawAbilityId.TacNukeStrike_NukeCalldown
    HATCHERY_MORPH_LAIR = RawAbilityId.UpgradeToLair_Lair
    HATCHERY_TRAIN_QUEEN = RawAbilityId.TrainQueen_Queen
    HELLBAT_MORPH_HELLION = RawAbilityId.Morph_Hellion
    HELLION_MORPH_HELLBAT = RawAbilityId.Morph_Hellbat
    HIGH_TEMPLAR_FEEDBACK = RawAbilityId.Feedback_Feedback
    HIGH_TEMPLAR_STORM = RawAbilityId.PsiStorm_PsiStorm
    HYDRALISK_DEN_RESEARCH_HYDRALISK_LUNGE = RawAbilityId.HydraliskDenResearch_ResearchFrenzy
    HYDRALISK_DEN_RESEARCH_HYDRALISK_RANGE = RawAbilityId.Research_GroovedSpines
    HYDRALISK_DEN_RESEARCH_HYDRALISK_SPEED = RawAbilityId.Research_MuscularAugments
    HYDRALISK_LUNGE = RawAbilityId.HydraliskFrenzy
    # The catalog also holds LurkerAspectMPFromHydraliskBurrowed, which no lurker den offers and which does
    # nothing when ordered, burrowed or not.
    HYDRALISK_MORPH_LURKER = RawAbilityId.Morph_Lurker
    INFESTATION_PIT_RESEARCH_NEURAL_PARASITE = RawAbilityId.Research_NeuralParasite
    INFESTOR_FUNGAL_GROWTH = RawAbilityId.FungalGrowth_FungalGrowth
    INFESTOR_MICROBIAL_SHROUD = RawAbilityId.AmorphousArmorcloud_AmorphousArmorcloud
    INFESTOR_NEURAL_PARASITE = RawAbilityId.NeuralParasite_NeuralParasite
    LAIR_MORPH_HIVE = RawAbilityId.UpgradeToHive_Hive
    LAIR_RESEARCH_BURROW = RawAbilityId.Research_Burrow
    LAIR_RESEARCH_OVERLORD_SPEED = RawAbilityId.Research_PneumatizedCarapace
    LARVA_MORPH_CORRUPTOR = RawAbilityId.LarvaTrain_Corruptor
    LARVA_MORPH_DRONE = RawAbilityId.LarvaTrain_Drone
    LARVA_MORPH_HYDRALISK = RawAbilityId.LarvaTrain_Hydralisk
    LARVA_MORPH_INFESTOR = RawAbilityId.LarvaTrain_Infestor
    LARVA_MORPH_MUTALISK = RawAbilityId.LarvaTrain_Mutalisk
    LARVA_MORPH_OVERLORD = RawAbilityId.LarvaTrain_Overlord
    LARVA_MORPH_ROACH = RawAbilityId.LarvaTrain_Roach
    LARVA_MORPH_SWARM_HOST = RawAbilityId.Train_SwarmHost
    LARVA_MORPH_ULTRALISK = RawAbilityId.LarvaTrain_Ultralisk
    LARVA_MORPH_VIPER = RawAbilityId.LarvaTrain_Viper
    LARVA_MORPH_ZERGLING = RawAbilityId.LarvaTrain_Zergling
    LIBERATOR_SIEGE = RawAbilityId.Morph_LiberatorAGMode
    LIBERATOR_UNSIEGE = RawAbilityId.Morph_LiberatorAAMode
    LOCUST_SWOOP = RawAbilityId.Effect_LocustSwoop
    LURKER_DEN_RESEARCH_LURKER_BURROW_SPEED = RawAbilityId.Research_AdaptiveTalons
    LURKER_DEN_RESEARCH_LURKER_RANGE = RawAbilityId.LurkerDenResearch_ResearchLurkerRange
    MEDIVAC_BOOST = RawAbilityId.Effect_MedivacIgniteAfterburners
    MEDIVAC_HEAL = RawAbilityId.MedivacHeal_Heal
    MOTHERSHIP_CLOAK_FIELD = RawAbilityId.MothershipCloak_OracleCloakField
    MOTHERSHIP_TIME_WARP = RawAbilityId.Effect_TimeWarp
    NEXUS_CHRONO_BOOST = RawAbilityId.Effect_ChronoBoostEnergyCost
    NEXUS_ENERGY_RECHARGE = RawAbilityId.EnergyRecharge_EnergyRecharge
    NEXUS_TRAIN_MOTHERSHIP = RawAbilityId.NexusTrainMothership_Mothership
    NEXUS_TRAIN_PROBE = RawAbilityId.NexusTrain_Probe
    # Id zero, no ability at all, so unlike the rest it names no performer.
    NULL = RawAbilityId.Null_Null
    NYDUS_NETWORK_BUILD_NYDUS_WORM = RawAbilityId.Build_NydusWorm
    OBSERVER_SIEGE = RawAbilityId.Morph_SurveillanceMode  # "Surveillance Mode" in game
    OBSERVER_UNSIEGE = RawAbilityId.Morph_ObserverMode
    ORACLE_BUILD_STASIS_WARD = RawAbilityId.Build_StasisTrap
    ORACLE_PULSAR_BEAM_OFF = RawAbilityId.Behavior_PulsarBeamOff
    ORACLE_PULSAR_BEAM_ON = RawAbilityId.Behavior_PulsarBeamOn
    ORACLE_REVELATION = RawAbilityId.OracleRevelation_OracleRevelation
    ORBITAL_COMMAND_CALLDOWN_MULE = RawAbilityId.CalldownMULE_CalldownMULE
    ORBITAL_COMMAND_SCAN = RawAbilityId.ScannerSweep_Scan
    ORBITAL_COMMAND_SUPPLY_DROP = RawAbilityId.SupplyDrop_SupplyDrop
    OVERLORD_CREEP_OFF = RawAbilityId.Behavior_GenerateCreepOff
    OVERLORD_CREEP_ON = RawAbilityId.Behavior_GenerateCreepOn
    OVERLORD_MORPH_OVERLORD_TRANSPORT = RawAbilityId.Morph_OverlordTransport
    OVERLORD_MORPH_OVERSEER = RawAbilityId.Morph_Overseer
    OVERSEER_CONTAMINATE = RawAbilityId.Contaminate_Contaminate
    OVERSEER_SIEGE = RawAbilityId.Morph_OversightMode  # "Oversight Mode" in game
    OVERSEER_SPAWN_CHANGELING = RawAbilityId.SpawnChangeling_SpawnChangeling
    OVERSEER_UNSIEGE = RawAbilityId.Morph_OverseerMode
    PHOENIX_GRAVITON_BEAM = RawAbilityId.GravitonBeam_GravitonBeam
    PROBE_BUILD_ASSIMILATOR = RawAbilityId.ProtossBuild_Assimilator
    PROBE_BUILD_CYBERNETICS_CORE = RawAbilityId.ProtossBuild_CyberneticsCore
    PROBE_BUILD_DARK_SHRINE = RawAbilityId.ProtossBuild_DarkShrine
    PROBE_BUILD_FLEET_BEACON = RawAbilityId.ProtossBuild_FleetBeacon
    PROBE_BUILD_FORGE = RawAbilityId.ProtossBuild_Forge
    PROBE_BUILD_GATEWAY = RawAbilityId.ProtossBuild_Gateway
    PROBE_BUILD_NEXUS = RawAbilityId.ProtossBuild_Nexus
    PROBE_BUILD_PHOTON_CANNON = RawAbilityId.ProtossBuild_PhotonCannon
    PROBE_BUILD_PYLON = RawAbilityId.ProtossBuild_Pylon
    PROBE_BUILD_ROBOTICS_BAY = RawAbilityId.ProtossBuild_RoboticsBay
    PROBE_BUILD_ROBOTICS_FACILITY = RawAbilityId.ProtossBuild_RoboticsFacility
    PROBE_BUILD_SHIELD_BATTERY = RawAbilityId.Build_ShieldBattery
    PROBE_BUILD_STARGATE = RawAbilityId.ProtossBuild_Stargate
    PROBE_BUILD_TEMPLAR_ARCHIVE = RawAbilityId.ProtossBuild_TemplarArchive
    PROBE_BUILD_TWILIGHT_COUNCIL = RawAbilityId.ProtossBuild_TwilightCouncil
    QUEEN_INJECT = RawAbilityId.Effect_InjectLarva
    QUEEN_TRANSFUSE = RawAbilityId.Transfusion_Transfusion
    RAVAGER_CORROSIVE_BILE = RawAbilityId.Effect_CorrosiveBile
    RAVEN_ANTI_ARMOR_MISSILE = RawAbilityId.Effect_AntiArmorMissile
    RAVEN_INTERFERENCE_MATRIX = RawAbilityId.Effect_InterferenceMatrix
    RAVEN_SPAWN_AUTO_TURRET = RawAbilityId.BuildAutoTurret_AutoTurret
    REAPER_GRENADE = RawAbilityId.KD8Charge_KD8Charge
    ROACH_MORPH_RAVAGER = RawAbilityId.MorphToRavager_Ravager
    ROACH_WARREN_RESEARCH_ROACH_SPEED = RawAbilityId.Research_GlialRegeneration
    ROACH_WARREN_RESEARCH_TUNNELING_CLAWS = RawAbilityId.Research_TunnelingClaws
    ROBOTICS_BAY_RESEARCH_COLOSSUS_RANGE = RawAbilityId.Research_ExtendedThermalLance
    ROBOTICS_BAY_RESEARCH_OBSERVER_SPEED = RawAbilityId.Research_GraviticBooster
    ROBOTICS_BAY_RESEARCH_WARP_PRISM_SPEED = RawAbilityId.Research_GraviticDrive
    ROBOTICS_FACILITY_TRAIN_COLOSSUS = RawAbilityId.RoboticsFacilityTrain_Colossus
    ROBOTICS_FACILITY_TRAIN_DISRUPTOR = RawAbilityId.Train_Disruptor
    ROBOTICS_FACILITY_TRAIN_IMMORTAL = RawAbilityId.RoboticsFacilityTrain_Immortal
    ROBOTICS_FACILITY_TRAIN_OBSERVER = RawAbilityId.RoboticsFacilityTrain_Observer
    ROBOTICS_FACILITY_TRAIN_WARP_PRISM = RawAbilityId.RoboticsFacilityTrain_WarpPrism
    SCV_BUILD_ARMORY = RawAbilityId.TerranBuild_Armory
    SCV_BUILD_BARRACKS = RawAbilityId.TerranBuild_Barracks
    SCV_BUILD_BUNKER = RawAbilityId.TerranBuild_Bunker
    SCV_BUILD_COMMAND_CENTER = RawAbilityId.TerranBuild_CommandCenter
    SCV_BUILD_ENGINEERING_BAY = RawAbilityId.TerranBuild_EngineeringBay
    SCV_BUILD_FACTORY = RawAbilityId.TerranBuild_Factory
    SCV_BUILD_FUSION_CORE = RawAbilityId.TerranBuild_FusionCore
    SCV_BUILD_GHOST_ACADEMY = RawAbilityId.TerranBuild_GhostAcademy
    SCV_BUILD_MISSILE_TURRET = RawAbilityId.TerranBuild_MissileTurret
    SCV_BUILD_REFINERY = RawAbilityId.TerranBuild_Refinery
    SCV_BUILD_SENSOR_TOWER = RawAbilityId.TerranBuild_SensorTower
    SCV_BUILD_STARPORT = RawAbilityId.TerranBuild_Starport
    SCV_BUILD_SUPPLY_DEPOT = RawAbilityId.TerranBuild_SupplyDepot
    SENTRY_FORCE_FIELD = RawAbilityId.ForceField_ForceField
    SENTRY_GUARDIAN_SHIELD = RawAbilityId.GuardianShield_GuardianShield
    SENTRY_HALLUCINATE_ADEPT = RawAbilityId.Hallucination_Adept
    SENTRY_HALLUCINATE_ARCHON = RawAbilityId.Hallucination_Archon
    SENTRY_HALLUCINATE_COLOSSUS = RawAbilityId.Hallucination_Colossus
    SENTRY_HALLUCINATE_DISRUPTOR = RawAbilityId.Hallucination_Disruptor
    SENTRY_HALLUCINATE_HIGH_TEMPLAR = RawAbilityId.Hallucination_HighTemplar
    SENTRY_HALLUCINATE_IMMORTAL = RawAbilityId.Hallucination_Immortal
    SENTRY_HALLUCINATE_ORACLE = RawAbilityId.Hallucination_Oracle
    SENTRY_HALLUCINATE_PHOENIX = RawAbilityId.Hallucination_Phoenix
    SENTRY_HALLUCINATE_PROBE = RawAbilityId.Hallucination_Probe
    SENTRY_HALLUCINATE_STALKER = RawAbilityId.Hallucination_Stalker
    SENTRY_HALLUCINATE_VOID_RAY = RawAbilityId.Hallucination_VoidRay
    SENTRY_HALLUCINATE_WARP_PRISM = RawAbilityId.Hallucination_WarpPrism
    SENTRY_HALLUCINATE_ZEALOT = RawAbilityId.Hallucination_Zealot
    SHIELD_BATTERY_RECHARGE = RawAbilityId.ShieldBatteryRechargeEx5_ShieldBatteryRecharge
    SIEGE_TANK_SIEGE = RawAbilityId.SiegeMode_SiegeMode
    SIEGE_TANK_UNSIEGE = RawAbilityId.Unsiege_Unsiege
    SPAWNING_POOL_RESEARCH_ADRENAL_GLANDS = RawAbilityId.Research_ZerglingAdrenalGlands
    SPAWNING_POOL_RESEARCH_ZERGLING_SPEED = RawAbilityId.Research_ZerglingMetabolicBoost
    SPIRE_MORPH_GREATER_SPIRE = RawAbilityId.UpgradeToGreaterSpire_GreaterSpire
    SPIRE_RESEARCH_AIR_ARMOR_1 = RawAbilityId.Research_ZergFlyerArmorLevel1
    SPIRE_RESEARCH_AIR_ARMOR_2 = RawAbilityId.Research_ZergFlyerArmorLevel2
    SPIRE_RESEARCH_AIR_ARMOR_3 = RawAbilityId.Research_ZergFlyerArmorLevel3
    SPIRE_RESEARCH_AIR_WEAPONS_1 = RawAbilityId.Research_ZergFlyerAttackLevel1
    SPIRE_RESEARCH_AIR_WEAPONS_2 = RawAbilityId.Research_ZergFlyerAttackLevel2
    SPIRE_RESEARCH_AIR_WEAPONS_3 = RawAbilityId.Research_ZergFlyerAttackLevel3
    STARGATE_TRAIN_CARRIER = RawAbilityId.StargateTrain_Carrier
    STARGATE_TRAIN_ORACLE = RawAbilityId.StargateTrain_Oracle
    STARGATE_TRAIN_PHOENIX = RawAbilityId.StargateTrain_Phoenix
    STARGATE_TRAIN_TEMPEST = RawAbilityId.StargateTrain_Tempest
    STARGATE_TRAIN_VOID_RAY = RawAbilityId.StargateTrain_VoidRay
    STARPORT_TECH_LAB_RESEARCH_BANSHEE_CLOAK = RawAbilityId.Research_BansheeCloakingField
    STARPORT_TECH_LAB_RESEARCH_BANSHEE_SPEED = RawAbilityId.Research_BansheeHyperflightRotors
    STARPORT_TECH_LAB_RESEARCH_INTERFERENCE_MATRIX = RawAbilityId.StarportTechLabResearch_ResearchRavenInterferenceMatrix  # noqa: E501 # fmt: skip
    STARPORT_TRAIN_BANSHEE = RawAbilityId.StarportTrain_Banshee
    STARPORT_TRAIN_BATTLECRUISER = RawAbilityId.StarportTrain_Battlecruiser
    STARPORT_TRAIN_LIBERATOR = RawAbilityId.StarportTrain_Liberator
    STARPORT_TRAIN_MEDIVAC = RawAbilityId.StarportTrain_Medivac
    STARPORT_TRAIN_RAVEN = RawAbilityId.StarportTrain_Raven
    STARPORT_TRAIN_VIKING = RawAbilityId.StarportTrain_VikingFighter
    SUPPLY_DEPOT_LOWER = RawAbilityId.Morph_SupplyDepot_Lower
    SUPPLY_DEPOT_RAISE = RawAbilityId.Morph_SupplyDepot_Raise
    SWARM_HOST_SPAWN_LOCUST = RawAbilityId.Effect_SpawnLocusts
    TEMPLAR_ARCHIVE_RESEARCH_STORM = RawAbilityId.Research_PsiStorm
    THOR_EXPLOSIVE_MODE = RawAbilityId.Morph_ThorExplosiveMode
    THOR_HIGH_IMPACT_MODE = RawAbilityId.Morph_ThorHighImpactMode
    TWILIGHT_COUNCIL_RESEARCH_BLINK = RawAbilityId.Research_Blink
    TWILIGHT_COUNCIL_RESEARCH_CHARGE = RawAbilityId.Research_Charge
    TWILIGHT_COUNCIL_RESEARCH_GLAIVES = RawAbilityId.Research_AdeptResonatingGlaives
    ULTRALISK_CAVERN_RESEARCH_ULTRALISK_ARMOR = RawAbilityId.Research_ChitinousPlating
    ULTRALISK_CAVERN_RESEARCH_ULTRALISK_SPEED = RawAbilityId.Research_AnabolicSynthesis
    VIKING_LAND = RawAbilityId.Morph_VikingAssaultMode  # "Assault Mode" in game
    VIKING_LIFT = RawAbilityId.Morph_VikingFighterMode
    VIPER_ABDUCT = RawAbilityId.Effect_Abduct
    VIPER_BLINDING_CLOUD = RawAbilityId.BlindingCloud_BlindingCloud
    VIPER_CONSUME = RawAbilityId.ViperConsumeStructure_ViperConsume
    VIPER_PARASITIC_BOMB = RawAbilityId.ParasiticBomb_ParasiticBomb
    VOID_RAY_PRISMATIC_ALIGNMENT = RawAbilityId.Effect_VoidRayPrismaticAlignment
    WARP_GATE_MORPH_GATEWAY = RawAbilityId.Morph_Gateway
    WARP_GATE_WARP_IN_ADEPT = RawAbilityId.TrainWarp_Adept
    WARP_GATE_WARP_IN_DARK_TEMPLAR = RawAbilityId.WarpGateTrain_DarkTemplar
    WARP_GATE_WARP_IN_HIGH_TEMPLAR = RawAbilityId.WarpGateTrain_HighTemplar
    WARP_GATE_WARP_IN_SENTRY = RawAbilityId.WarpGateTrain_Sentry
    WARP_GATE_WARP_IN_STALKER = RawAbilityId.WarpGateTrain_Stalker
    WARP_GATE_WARP_IN_ZEALOT = RawAbilityId.WarpGateTrain_Zealot
    WARP_PRISM_PHASING_MODE = RawAbilityId.Morph_WarpPrismPhasingMode
    WARP_PRISM_TRANSPORT_MODE = RawAbilityId.Morph_WarpPrismTransportMode
    WIDOW_MINE_ATTACK = RawAbilityId.WidowMineAttack_WidowMineAttack
    ZEALOT_CHARGE = RawAbilityId.Effect_Charge
    # The catalog also holds MorphZerglingToBaneling, which a zergling is never offered and which does nothing.
    ZERGLING_MORPH_BANELING = RawAbilityId.MorphToBaneling_Baneling

    # The game's own id for each unit of an action several units perform, with the action's: the id a unit is offered
    # and reports. The game takes the action's id as an order and runs each unit's own (in game). Then the id a unit
    # reports running for an order given by another, where the game links neither to the other, with the id ordered.
    # Ordering such a reported id does nothing, except that the archon's, given to both templar and aimed at one of
    # them, merges them too (in game).
    _REMAPPED_IDS = nonmember(
        MappingProxyType(
            {
                RawAbilityId.Attack_Battlecruiser: GENERAL_ATTACK,
                RawAbilityId.Attack_Redirect: GENERAL_ATTACK,
                RawAbilityId.Scan_Move: GENERAL_ATTACK,
                RawAbilityId.attack_Attack: GENERAL_ATTACK,
                RawAbilityId.Effect_Blink_Stalker: GENERAL_BLINK,
                RawAbilityId.Effect_ShadowStride: GENERAL_BLINK,
                RawAbilityId.Build_CreepTumor_Queen: GENERAL_BUILD_CREEP_TUMOR,
                RawAbilityId.Build_CreepTumor_Tumor: GENERAL_BUILD_CREEP_TUMOR,
                RawAbilityId.Build_Reactor_Barracks: GENERAL_BUILD_REACTOR,
                RawAbilityId.Build_Reactor_Factory: GENERAL_BUILD_REACTOR,
                RawAbilityId.Build_Reactor_Starport: GENERAL_BUILD_REACTOR,
                RawAbilityId.Build_TechLab_Barracks: GENERAL_BUILD_TECH_LAB,
                RawAbilityId.Build_TechLab_Factory: GENERAL_BUILD_TECH_LAB,
                RawAbilityId.Build_TechLab_Starport: GENERAL_BUILD_TECH_LAB,
                RawAbilityId.BurrowDown_Baneling: GENERAL_BURROW,
                RawAbilityId.BurrowDown_Drone: GENERAL_BURROW,
                RawAbilityId.BurrowDown_Hydralisk: GENERAL_BURROW,
                RawAbilityId.BurrowDown_Infestor: GENERAL_BURROW,
                RawAbilityId.BurrowDown_Lurker: GENERAL_BURROW,
                RawAbilityId.BurrowDown_Queen: GENERAL_BURROW,
                RawAbilityId.BurrowDown_Ravager: GENERAL_BURROW,
                RawAbilityId.BurrowDown_Roach: GENERAL_BURROW,
                RawAbilityId.BurrowDown_SwarmHost: GENERAL_BURROW,
                RawAbilityId.BurrowDown_Ultralisk: GENERAL_BURROW,
                RawAbilityId.BurrowDown_WidowMine: GENERAL_BURROW,
                RawAbilityId.BurrowDown_Zergling: GENERAL_BURROW,
                RawAbilityId.Cancel_AdeptPhaseShift: GENERAL_CANCEL,
                RawAbilityId.Cancel_AdeptShadePhaseShift: GENERAL_CANCEL,
                RawAbilityId.Cancel_BarracksAddOn: GENERAL_CANCEL,
                RawAbilityId.Cancel_BuildInProgress: GENERAL_CANCEL,
                RawAbilityId.Cancel_FactoryAddOn: GENERAL_CANCEL,
                RawAbilityId.Cancel_GravitonBeam: GENERAL_CANCEL,
                RawAbilityId.Cancel_MorphBroodlord: GENERAL_CANCEL,
                RawAbilityId.Cancel_MorphGreaterSpire: GENERAL_CANCEL,
                RawAbilityId.Cancel_MorphHive: GENERAL_CANCEL,
                RawAbilityId.Cancel_MorphLair: GENERAL_CANCEL,
                RawAbilityId.Cancel_MorphLurker: GENERAL_CANCEL,
                RawAbilityId.Cancel_MorphOrbital: GENERAL_CANCEL,
                RawAbilityId.Cancel_MorphOverlordTransport: GENERAL_CANCEL,
                RawAbilityId.Cancel_MorphOverseer: GENERAL_CANCEL,
                RawAbilityId.Cancel_MorphPlanetaryFortress: GENERAL_CANCEL,
                RawAbilityId.Cancel_MorphRavager: GENERAL_CANCEL,
                RawAbilityId.Cancel_NeuralParasite: GENERAL_CANCEL,
                RawAbilityId.Cancel_Nuke: GENERAL_CANCEL,
                RawAbilityId.Cancel_StarportAddOn: GENERAL_CANCEL,
                RawAbilityId.Cancel_VoidRayPrismaticAlignment: GENERAL_CANCEL,
                RawAbilityId.ChannelSnipe_Cancel: GENERAL_CANCEL,
                RawAbilityId.MorphToBaneling_Cancel: GENERAL_CANCEL,
                RawAbilityId.Cancel_HangarQueue5: GENERAL_CANCEL_LAST,
                RawAbilityId.Cancel_Queue1: GENERAL_CANCEL_LAST,
                RawAbilityId.Cancel_Queue5: GENERAL_CANCEL_LAST,
                RawAbilityId.Cancel_QueueAddOn: GENERAL_CANCEL_LAST,
                RawAbilityId.Cancel_QueueCancelToSelection: GENERAL_CANCEL_LAST,
                RawAbilityId.Cancel_QueuePasive: GENERAL_CANCEL_LAST,
                RawAbilityId.Cancel_QueuePassiveCancelToSelection: GENERAL_CANCEL_LAST,
                RawAbilityId.Behavior_CloakOff_Banshee: GENERAL_CLOAK_OFF,
                RawAbilityId.Behavior_CloakOff_Ghost: GENERAL_CLOAK_OFF,
                RawAbilityId.Behavior_CloakOn_Banshee: GENERAL_CLOAK_ON,
                RawAbilityId.Behavior_CloakOn_Ghost: GENERAL_CLOAK_ON,
                RawAbilityId.Harvest_Gather_Drone: GENERAL_GATHER,
                RawAbilityId.Harvest_Gather_Mule: GENERAL_GATHER,
                RawAbilityId.Harvest_Gather_Probe: GENERAL_GATHER,
                RawAbilityId.Harvest_Gather_SCV: GENERAL_GATHER,
                RawAbilityId.Halt_Building: GENERAL_HALT,
                RawAbilityId.Halt_TerranBuild: GENERAL_HALT,
                RawAbilityId.Behavior_HoldFireOff_Ghost: GENERAL_HOLD_FIRE_OFF,
                RawAbilityId.Behavior_HoldFireOff_Lurker: GENERAL_HOLD_FIRE_OFF,
                RawAbilityId.Behavior_HoldFireOn_Ghost: GENERAL_HOLD_FIRE_ON,
                RawAbilityId.Behavior_HoldFireOn_Lurker: GENERAL_HOLD_FIRE_ON,
                RawAbilityId.HoldPosition_Battlecruiser: GENERAL_HOLD_POSITION,
                RawAbilityId.HoldPosition_Hold: GENERAL_HOLD_POSITION,
                RawAbilityId.Land_Barracks: GENERAL_LAND,
                RawAbilityId.Land_CommandCenter: GENERAL_LAND,
                RawAbilityId.Land_Factory: GENERAL_LAND,
                RawAbilityId.Land_OrbitalCommand: GENERAL_LAND,
                RawAbilityId.Land_Starport: GENERAL_LAND,
                RawAbilityId.Lift_Barracks: GENERAL_LIFT,
                RawAbilityId.Lift_CommandCenter: GENERAL_LIFT,
                RawAbilityId.Lift_Factory: GENERAL_LIFT,
                RawAbilityId.Lift_OrbitalCommand: GENERAL_LIFT,
                RawAbilityId.Lift_Starport: GENERAL_LIFT,
                RawAbilityId.Load_Bunker: GENERAL_LOAD,
                RawAbilityId.Load_Medivac: GENERAL_LOAD,
                RawAbilityId.Load_NydusNetwork: GENERAL_LOAD,
                RawAbilityId.Load_NydusWorm: GENERAL_LOAD,
                RawAbilityId.Load_Overlord: GENERAL_LOAD,
                RawAbilityId.Load_WarpPrism: GENERAL_LOAD,
                RawAbilityId.LoadAll_CommandCenter: GENERAL_LOAD_ALL,
                RawAbilityId.Move_Battlecruiser: GENERAL_MOVE,
                RawAbilityId.Move_Move: GENERAL_MOVE,
                RawAbilityId.Patrol_Battlecruiser: GENERAL_PATROL,
                RawAbilityId.Patrol_Patrol: GENERAL_PATROL,
                RawAbilityId.Rally_Building: GENERAL_RALLY_UNITS,
                RawAbilityId.Rally_Hatchery_Units: GENERAL_RALLY_UNITS,
                RawAbilityId.Rally_CommandCenter: GENERAL_RALLY_WORKERS,
                RawAbilityId.Rally_Hatchery_Workers: GENERAL_RALLY_WORKERS,
                RawAbilityId.Rally_Nexus: GENERAL_RALLY_WORKERS,
                RawAbilityId.Effect_MassRecall_Nexus: GENERAL_RECALL,
                RawAbilityId.Effect_MassRecall_StrategicRecall: GENERAL_RECALL,
                RawAbilityId.Effect_Repair_Mule: GENERAL_REPAIR,
                RawAbilityId.Effect_Repair_SCV: GENERAL_REPAIR,
                RawAbilityId.Harvest_Return_Drone: GENERAL_RETURN,
                RawAbilityId.Harvest_Return_Mule: GENERAL_RETURN,
                RawAbilityId.Harvest_Return_Probe: GENERAL_RETURN,
                RawAbilityId.Harvest_Return_SCV: GENERAL_RETURN,
                RawAbilityId.SpineCrawlerRoot_SpineCrawlerRoot: GENERAL_ROOT,
                RawAbilityId.SporeCrawlerRoot_SporeCrawlerRoot: GENERAL_ROOT,
                RawAbilityId.Effect_Spray_Protoss: GENERAL_SPRAY,
                RawAbilityId.Effect_Spray_Terran: GENERAL_SPRAY,
                RawAbilityId.Effect_Spray_Zerg: GENERAL_SPRAY,
                RawAbilityId.Effect_Stim_Marauder: GENERAL_STIM,
                RawAbilityId.Effect_Stim_Marine: GENERAL_STIM,
                RawAbilityId.Stop_Battlecruiser: GENERAL_STOP,
                RawAbilityId.Stop_Redirect: GENERAL_STOP,
                RawAbilityId.stop_Stop: GENERAL_STOP,
                RawAbilityId.BurrowUp_Baneling: GENERAL_UNBURROW,
                RawAbilityId.BurrowUp_Drone: GENERAL_UNBURROW,
                RawAbilityId.BurrowUp_Hydralisk: GENERAL_UNBURROW,
                RawAbilityId.BurrowUp_Infestor: GENERAL_UNBURROW,
                RawAbilityId.BurrowUp_Lurker: GENERAL_UNBURROW,
                RawAbilityId.BurrowUp_Queen: GENERAL_UNBURROW,
                RawAbilityId.BurrowUp_Ravager: GENERAL_UNBURROW,
                RawAbilityId.BurrowUp_Roach: GENERAL_UNBURROW,
                RawAbilityId.BurrowUp_SwarmHost: GENERAL_UNBURROW,
                RawAbilityId.BurrowUp_Ultralisk: GENERAL_UNBURROW,
                RawAbilityId.BurrowUp_WidowMine: GENERAL_UNBURROW,
                RawAbilityId.BurrowUp_Zergling: GENERAL_UNBURROW,
                RawAbilityId.OverlordTransport: GENERAL_UNLOAD,
                RawAbilityId.UnloadAll_Bunker: GENERAL_UNLOAD,
                RawAbilityId.UnloadAll_CommandCenter: GENERAL_UNLOAD,
                RawAbilityId.UnloadAll_NydasNetwork: GENERAL_UNLOAD,
                RawAbilityId.UnloadAll_NydusWorm: GENERAL_UNLOAD,
                RawAbilityId.UnloadAll_WarpPrism: GENERAL_UNLOAD,
                RawAbilityId.Unload_Medivac: GENERAL_UNLOAD,
                RawAbilityId.BunkerTransport: GENERAL_UNLOAD_AT,
                RawAbilityId.CommandCenterTransport_414: GENERAL_UNLOAD_AT,
                RawAbilityId.NydusCanalTransport: GENERAL_UNLOAD_AT,
                RawAbilityId.NydusWormTransport: GENERAL_UNLOAD_AT,
                RawAbilityId.UnloadAllAt_Medivac: GENERAL_UNLOAD_AT,
                RawAbilityId.UnloadAllAt_Overlord: GENERAL_UNLOAD_AT,
                RawAbilityId.UnloadAllAt_WarpPrism: GENERAL_UNLOAD_AT,
                RawAbilityId.SpineCrawlerUproot_SpineCrawlerUproot: GENERAL_UPROOT,
                RawAbilityId.SporeCrawlerUproot_SporeCrawlerUproot: GENERAL_UPROOT,
                RawAbilityId.LiberatorMorphtoAG_LiberatorAGMode: LIBERATOR_SIEGE,
                RawAbilityId.LiberatorMorphtoAA_LiberatorAAMode: LIBERATOR_UNSIEGE,
                RawAbilityId.Archon_Warp_Target: GENERAL_MORPH_ARCHON,
            }
        )
    )
