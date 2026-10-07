"""Ability identifiers.

Hand-maintained: filtered to what multiplayer needs, named for readability. Each member is defined by a raw
catalog member, never a literal id, except a custom id: one the game has no ability for, which goes out as each unit
type's own game ability (`AbilityData.sent_as`): SIEGE as a tank's siege mode and a liberator's defender mode.
A custom id is assigned `auto()`, which numbers it above every game id; what the game reports for it reads as it.
Unknown ids raise.

Each names the unit that performs it and then what it does -- SCV_BUILD_BARRACKS, BARRACKS_TRAIN_MARINE,
LARVA_MORPH_ZERGLING -- and by what it does alone where several units can. An action several units perform has one
id, whichever unit performs it: LIFT for a barracks and a starport, GATHER for every worker, CANCEL for whatever a
unit is offered a cancel for. It is the id to order, the id a unit is offered and the id it reports; the
game's own id for each unit reads as it (`_REMAPPED_IDS`). A leveled research has an id per level and none for the
next level, as the upgrade it makes does.

Where a unit reports running another id than the one it was ordered, and the game links neither to the other, that id
reads as the one ordered too: a liberator ordered SIEGE reports LiberatorMorphtoAG_LiberatorAGMode, which
reads as SIEGE.
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
    # A unit that can fire runs it as an attack, one that cannot as a scan move (in game).
    ATTACK = RawAbilityId.Attack
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
    BLINK = RawAbilityId.Effect_Blink
    BUILD_CREEP_TUMOR = RawAbilityId.Build_CreepTumor
    BUILD_REACTOR = RawAbilityId.Build_Reactor
    BUILD_TECH_LAB = RawAbilityId.Build_TechLab
    BURROW = RawAbilityId.BurrowDown
    # Cancels whatever a unit is offered a cancel for, beyond a queue: a morph, an add-on, a structure going up, a
    # cocoon or egg, a channel, a shade, a nuke. A unit is offered one at a time (in game).
    CANCEL = RawAbilityId.Cancel
    # Takes the last item off a structure's queue, of units or of research. A structure training answers
    # CANCEL with `Error`; a morph and an add-on answer this one so (in game).
    CANCEL_LAST = RawAbilityId.Cancel_Last
    CARRIER_BUILD_INTERCEPTORS = RawAbilityId.Build_Interceptors
    CLOAK_OFF = RawAbilityId.Behavior_CloakOff
    CLOAK_ON = RawAbilityId.Behavior_CloakOn
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
    GATHER = RawAbilityId.Harvest_Gather
    GHOST_ACADEMY_BUILD_NUKE = RawAbilityId.Build_Nuke
    GHOST_ACADEMY_RESEARCH_GHOST_CLOAK = RawAbilityId.Research_PersonalCloaking
    GHOST_EMP = RawAbilityId.EMP_EMP
    GHOST_SNIPE = RawAbilityId.Effect_GhostSnipe
    GHOST_TACTICAL_NUKE = RawAbilityId.TacNukeStrike_NukeCalldown
    HALT = RawAbilityId.Halt
    HATCHERY_MORPH_LAIR = RawAbilityId.UpgradeToLair_Lair
    HATCHERY_TRAIN_QUEEN = RawAbilityId.TrainQueen_Queen
    HELLBAT_MORPH_HELLION = RawAbilityId.Morph_Hellion
    HELLION_MORPH_HELLBAT = RawAbilityId.Morph_Hellbat
    HIGH_TEMPLAR_FEEDBACK = RawAbilityId.Feedback_Feedback
    HIGH_TEMPLAR_STORM = RawAbilityId.PsiStorm_PsiStorm
    HOLD_FIRE_OFF = RawAbilityId.Behavior_HoldFireOff
    HOLD_FIRE_ON = RawAbilityId.Behavior_HoldFireOn
    HOLD_POSITION = RawAbilityId.HoldPosition
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
    LAND = RawAbilityId.Land
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
    LIFT = RawAbilityId.Lift
    LOAD = RawAbilityId.Load
    LOAD_ALL = RawAbilityId.LoadAll
    LOCUST_SWOOP = RawAbilityId.Effect_LocustSwoop
    LURKER_DEN_RESEARCH_LURKER_BURROW_SPEED = RawAbilityId.Research_AdaptiveTalons
    LURKER_DEN_RESEARCH_LURKER_RANGE = RawAbilityId.LurkerDenResearch_ResearchLurkerRange
    MEDIVAC_BOOST = RawAbilityId.Effect_MedivacIgniteAfterburners
    MEDIVAC_HEAL = RawAbilityId.MedivacHeal_Heal
    # The order to give two templar, high or dark, selected together: they walk to each other and merge into an
    # archon. Given to one alone it is refused. Each reports MORPH_ARCHON running, aimed at the other (in game).
    MORPH_ARCHON = RawAbilityId.Morph_Archon
    MOTHERSHIP_CLOAK_FIELD = RawAbilityId.MothershipCloak_OracleCloakField
    MOTHERSHIP_TIME_WARP = RawAbilityId.Effect_TimeWarp
    MOVE = RawAbilityId.Move
    NEXUS_CHRONO_BOOST = RawAbilityId.Effect_ChronoBoostEnergyCost
    NEXUS_ENERGY_RECHARGE = RawAbilityId.EnergyRecharge_EnergyRecharge
    NEXUS_TRAIN_MOTHERSHIP = RawAbilityId.NexusTrainMothership_Mothership
    NEXUS_TRAIN_PROBE = RawAbilityId.NexusTrain_Probe
    # Id zero, no ability at all, so unlike the rest it names no performer.
    NULL = RawAbilityId.Null_Null
    NYDUS_NETWORK_BUILD_NYDUS_WORM = RawAbilityId.Build_NydusWorm
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
    OVERSEER_SPAWN_CHANGELING = RawAbilityId.SpawnChangeling_SpawnChangeling
    PATROL = RawAbilityId.Patrol
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
    RALLY_UNITS = RawAbilityId.Rally_Units
    RALLY_WORKERS = RawAbilityId.Rally_Workers
    RAVAGER_CORROSIVE_BILE = RawAbilityId.Effect_CorrosiveBile
    RAVEN_ANTI_ARMOR_MISSILE = RawAbilityId.Effect_AntiArmorMissile
    RAVEN_INTERFERENCE_MATRIX = RawAbilityId.Effect_InterferenceMatrix
    RAVEN_SPAWN_AUTO_TURRET = RawAbilityId.BuildAutoTurret_AutoTurret
    REAPER_GRENADE = RawAbilityId.KD8Charge_KD8Charge
    RECALL = RawAbilityId.Effect_MassRecall
    REPAIR = RawAbilityId.Effect_Repair
    RETURN = RawAbilityId.Harvest_Return
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
    ROOT = RawAbilityId.Morph_Root
    SALVAGE = RawAbilityId.SalvageEffect_Salvage
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
    # A tank's siege mode, a liberator's defender mode aimed at its zone, an observer's surveillance mode and an
    # overseer's oversight mode, and leaving each. The game has no id for either across the four.
    SIEGE = auto()
    SMART = RawAbilityId.Smart  # the right-click order
    SPAWNING_POOL_RESEARCH_ADRENAL_GLANDS = RawAbilityId.Research_ZerglingAdrenalGlands
    SPAWNING_POOL_RESEARCH_ZERGLING_SPEED = RawAbilityId.Research_ZerglingMetabolicBoost
    SPIRE_MORPH_GREATER_SPIRE = RawAbilityId.UpgradeToGreaterSpire_GreaterSpire
    SPIRE_RESEARCH_AIR_ARMOR_1 = RawAbilityId.Research_ZergFlyerArmorLevel1
    SPIRE_RESEARCH_AIR_ARMOR_2 = RawAbilityId.Research_ZergFlyerArmorLevel2
    SPIRE_RESEARCH_AIR_ARMOR_3 = RawAbilityId.Research_ZergFlyerArmorLevel3
    SPIRE_RESEARCH_AIR_WEAPONS_1 = RawAbilityId.Research_ZergFlyerAttackLevel1
    SPIRE_RESEARCH_AIR_WEAPONS_2 = RawAbilityId.Research_ZergFlyerAttackLevel2
    SPIRE_RESEARCH_AIR_WEAPONS_3 = RawAbilityId.Research_ZergFlyerAttackLevel3
    SPRAY = RawAbilityId.Effect_Spray
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
    STIM = RawAbilityId.Effect_Stim
    STOP = RawAbilityId.Stop
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
    UNBURROW = RawAbilityId.BurrowUp
    # Puts every passenger down where the transport is: UnloadAll for a bunker, command center, planetary fortress or
    # nydus, and the unload at a point aimed at the transport itself for a medivac, warp prism or transport overlord,
    # which answer UnloadAll `Error` (in game).
    UNLOAD = RawAbilityId.UnloadAll
    UNLOAD_AT = RawAbilityId.UnloadAllAt
    UNSIEGE = auto()
    UPROOT = RawAbilityId.Morph_Uproot
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
    # and reports. The game takes the action's id as an order and runs each unit's own (in game). Then each type's own
    # id of an action the game has no id for across types, with the custom id that goes out as it. Then the id a unit
    # reports running for an order given by another, where the game links neither to the other, with the id ordered.
    # Ordering such a reported id does nothing, except that the archon's, given to both templar and aimed at one of
    # them, merges them too (in game).
    _REMAPPED_IDS = nonmember(
        MappingProxyType(
            {
                RawAbilityId.Attack_Battlecruiser: ATTACK,
                RawAbilityId.Attack_Redirect: ATTACK,
                RawAbilityId.Scan_Move: ATTACK,
                RawAbilityId.attack_Attack: ATTACK,
                RawAbilityId.Effect_Blink_Stalker: BLINK,
                RawAbilityId.Effect_ShadowStride: BLINK,
                RawAbilityId.Build_CreepTumor_Queen: BUILD_CREEP_TUMOR,
                RawAbilityId.Build_CreepTumor_Tumor: BUILD_CREEP_TUMOR,
                RawAbilityId.Build_Reactor_Barracks: BUILD_REACTOR,
                RawAbilityId.Build_Reactor_Factory: BUILD_REACTOR,
                RawAbilityId.Build_Reactor_Starport: BUILD_REACTOR,
                RawAbilityId.Build_TechLab_Barracks: BUILD_TECH_LAB,
                RawAbilityId.Build_TechLab_Factory: BUILD_TECH_LAB,
                RawAbilityId.Build_TechLab_Starport: BUILD_TECH_LAB,
                RawAbilityId.BurrowDown_Baneling: BURROW,
                RawAbilityId.BurrowDown_Drone: BURROW,
                RawAbilityId.BurrowDown_Hydralisk: BURROW,
                RawAbilityId.BurrowDown_Infestor: BURROW,
                RawAbilityId.BurrowDown_Lurker: BURROW,
                RawAbilityId.BurrowDown_Queen: BURROW,
                RawAbilityId.BurrowDown_Ravager: BURROW,
                RawAbilityId.BurrowDown_Roach: BURROW,
                RawAbilityId.BurrowDown_SwarmHost: BURROW,
                RawAbilityId.BurrowDown_Ultralisk: BURROW,
                RawAbilityId.BurrowDown_WidowMine: BURROW,
                RawAbilityId.BurrowDown_Zergling: BURROW,
                RawAbilityId.Cancel_AdeptPhaseShift: CANCEL,
                RawAbilityId.Cancel_AdeptShadePhaseShift: CANCEL,
                RawAbilityId.Cancel_BarracksAddOn: CANCEL,
                RawAbilityId.Cancel_BuildInProgress: CANCEL,
                RawAbilityId.Cancel_FactoryAddOn: CANCEL,
                RawAbilityId.Cancel_GravitonBeam: CANCEL,
                RawAbilityId.Cancel_MorphBroodlord: CANCEL,
                RawAbilityId.Cancel_MorphGreaterSpire: CANCEL,
                RawAbilityId.Cancel_MorphHive: CANCEL,
                RawAbilityId.Cancel_MorphLair: CANCEL,
                RawAbilityId.Cancel_MorphLurker: CANCEL,
                RawAbilityId.Cancel_MorphOrbital: CANCEL,
                RawAbilityId.Cancel_MorphOverlordTransport: CANCEL,
                RawAbilityId.Cancel_MorphOverseer: CANCEL,
                RawAbilityId.Cancel_MorphPlanetaryFortress: CANCEL,
                RawAbilityId.Cancel_MorphRavager: CANCEL,
                RawAbilityId.Cancel_NeuralParasite: CANCEL,
                RawAbilityId.Cancel_Nuke: CANCEL,
                RawAbilityId.Cancel_StarportAddOn: CANCEL,
                RawAbilityId.Cancel_VoidRayPrismaticAlignment: CANCEL,
                RawAbilityId.ChannelSnipe_Cancel: CANCEL,
                RawAbilityId.MorphToBaneling_Cancel: CANCEL,
                RawAbilityId.Cancel_HangarQueue5: CANCEL_LAST,
                RawAbilityId.Cancel_Queue1: CANCEL_LAST,
                RawAbilityId.Cancel_Queue5: CANCEL_LAST,
                RawAbilityId.Cancel_QueueAddOn: CANCEL_LAST,
                RawAbilityId.Cancel_QueueCancelToSelection: CANCEL_LAST,
                RawAbilityId.Cancel_QueuePasive: CANCEL_LAST,
                RawAbilityId.Cancel_QueuePassiveCancelToSelection: CANCEL_LAST,
                RawAbilityId.Behavior_CloakOff_Banshee: CLOAK_OFF,
                RawAbilityId.Behavior_CloakOff_Ghost: CLOAK_OFF,
                RawAbilityId.Behavior_CloakOn_Banshee: CLOAK_ON,
                RawAbilityId.Behavior_CloakOn_Ghost: CLOAK_ON,
                RawAbilityId.Harvest_Gather_Drone: GATHER,
                RawAbilityId.Harvest_Gather_Mule: GATHER,
                RawAbilityId.Harvest_Gather_Probe: GATHER,
                RawAbilityId.Harvest_Gather_SCV: GATHER,
                RawAbilityId.Halt_Building: HALT,
                RawAbilityId.Halt_TerranBuild: HALT,
                RawAbilityId.Behavior_HoldFireOff_Ghost: HOLD_FIRE_OFF,
                RawAbilityId.Behavior_HoldFireOff_Lurker: HOLD_FIRE_OFF,
                RawAbilityId.Behavior_HoldFireOn_Ghost: HOLD_FIRE_ON,
                RawAbilityId.Behavior_HoldFireOn_Lurker: HOLD_FIRE_ON,
                RawAbilityId.HoldPosition_Battlecruiser: HOLD_POSITION,
                RawAbilityId.HoldPosition_Hold: HOLD_POSITION,
                RawAbilityId.Land_Barracks: LAND,
                RawAbilityId.Land_CommandCenter: LAND,
                RawAbilityId.Land_Factory: LAND,
                RawAbilityId.Land_OrbitalCommand: LAND,
                RawAbilityId.Land_Starport: LAND,
                RawAbilityId.Lift_Barracks: LIFT,
                RawAbilityId.Lift_CommandCenter: LIFT,
                RawAbilityId.Lift_Factory: LIFT,
                RawAbilityId.Lift_OrbitalCommand: LIFT,
                RawAbilityId.Lift_Starport: LIFT,
                RawAbilityId.Load_Bunker: LOAD,
                RawAbilityId.Load_Medivac: LOAD,
                RawAbilityId.Load_NydusNetwork: LOAD,
                RawAbilityId.Load_NydusWorm: LOAD,
                RawAbilityId.Load_Overlord: LOAD,
                RawAbilityId.Load_WarpPrism: LOAD,
                RawAbilityId.LoadAll_CommandCenter: LOAD_ALL,
                RawAbilityId.Move_Battlecruiser: MOVE,
                RawAbilityId.Move_Move: MOVE,
                RawAbilityId.Patrol_Battlecruiser: PATROL,
                RawAbilityId.Patrol_Patrol: PATROL,
                RawAbilityId.Rally_Building: RALLY_UNITS,
                RawAbilityId.Rally_Hatchery_Units: RALLY_UNITS,
                RawAbilityId.Rally_CommandCenter: RALLY_WORKERS,
                RawAbilityId.Rally_Hatchery_Workers: RALLY_WORKERS,
                RawAbilityId.Rally_Nexus: RALLY_WORKERS,
                RawAbilityId.Effect_MassRecall_Nexus: RECALL,
                RawAbilityId.Effect_MassRecall_StrategicRecall: RECALL,
                RawAbilityId.Effect_Repair_Mule: REPAIR,
                RawAbilityId.Effect_Repair_SCV: REPAIR,
                RawAbilityId.Harvest_Return_Drone: RETURN,
                RawAbilityId.Harvest_Return_Mule: RETURN,
                RawAbilityId.Harvest_Return_Probe: RETURN,
                RawAbilityId.Harvest_Return_SCV: RETURN,
                RawAbilityId.SpineCrawlerRoot_SpineCrawlerRoot: ROOT,
                RawAbilityId.SporeCrawlerRoot_SporeCrawlerRoot: ROOT,
                RawAbilityId.Effect_Spray_Protoss: SPRAY,
                RawAbilityId.Effect_Spray_Terran: SPRAY,
                RawAbilityId.Effect_Spray_Zerg: SPRAY,
                RawAbilityId.Effect_Stim_Marauder: STIM,
                RawAbilityId.Effect_Stim_Marine: STIM,
                RawAbilityId.Stop_Battlecruiser: STOP,
                RawAbilityId.Stop_Redirect: STOP,
                RawAbilityId.stop_Stop: STOP,
                RawAbilityId.BurrowUp_Baneling: UNBURROW,
                RawAbilityId.BurrowUp_Drone: UNBURROW,
                RawAbilityId.BurrowUp_Hydralisk: UNBURROW,
                RawAbilityId.BurrowUp_Infestor: UNBURROW,
                RawAbilityId.BurrowUp_Lurker: UNBURROW,
                RawAbilityId.BurrowUp_Queen: UNBURROW,
                RawAbilityId.BurrowUp_Ravager: UNBURROW,
                RawAbilityId.BurrowUp_Roach: UNBURROW,
                RawAbilityId.BurrowUp_SwarmHost: UNBURROW,
                RawAbilityId.BurrowUp_Ultralisk: UNBURROW,
                RawAbilityId.BurrowUp_WidowMine: UNBURROW,
                RawAbilityId.BurrowUp_Zergling: UNBURROW,
                RawAbilityId.OverlordTransport: UNLOAD,
                RawAbilityId.UnloadAll_Bunker: UNLOAD,
                RawAbilityId.UnloadAll_CommandCenter: UNLOAD,
                RawAbilityId.UnloadAll_NydasNetwork: UNLOAD,
                RawAbilityId.UnloadAll_NydusWorm: UNLOAD,
                RawAbilityId.UnloadAll_WarpPrism: UNLOAD,
                RawAbilityId.Unload_Medivac: UNLOAD,
                RawAbilityId.BunkerTransport: UNLOAD_AT,
                RawAbilityId.CommandCenterTransport_414: UNLOAD_AT,
                RawAbilityId.NydusCanalTransport: UNLOAD_AT,
                RawAbilityId.NydusWormTransport: UNLOAD_AT,
                RawAbilityId.UnloadAllAt_Medivac: UNLOAD_AT,
                RawAbilityId.UnloadAllAt_Overlord: UNLOAD_AT,
                RawAbilityId.UnloadAllAt_WarpPrism: UNLOAD_AT,
                RawAbilityId.SpineCrawlerUproot_SpineCrawlerUproot: UPROOT,
                RawAbilityId.SporeCrawlerUproot_SporeCrawlerUproot: UPROOT,
                RawAbilityId.Morph_LiberatorAGMode: SIEGE,
                RawAbilityId.Morph_OversightMode: SIEGE,
                RawAbilityId.Morph_SurveillanceMode: SIEGE,
                RawAbilityId.SiegeMode_SiegeMode: SIEGE,
                RawAbilityId.Morph_LiberatorAAMode: UNSIEGE,
                RawAbilityId.Morph_ObserverMode: UNSIEGE,
                RawAbilityId.Morph_OverseerMode: UNSIEGE,
                RawAbilityId.Unsiege_Unsiege: UNSIEGE,
                RawAbilityId.LiberatorMorphtoAG_LiberatorAGMode: SIEGE,
                RawAbilityId.LiberatorMorphtoAA_LiberatorAAMode: UNSIEGE,
                RawAbilityId.Archon_Warp_Target: MORPH_ARCHON,
            }
        )
    )
