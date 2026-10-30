"""What a tech requirement names."""

from __future__ import annotations

from enum import Enum

from sc2nachos.ids import UnitTypeId


class Tech(Enum):
    """What a tech requirement names, which a player has while a structure of a type granting it stands. A spire and a
    greater spire both grant `SPIRE`, and `UnitTypeData.techs_granted` says what each type grants. Each is valued by
    the type it is named after, its `base_type`, but never equals that `UnitTypeId`."""

    _value_: UnitTypeId

    SUPPLY_DEPOT = UnitTypeId.SUPPLY_DEPOT
    COMMAND_CENTER = UnitTypeId.COMMAND_CENTER
    BARRACKS = UnitTypeId.BARRACKS
    ENGINEERING_BAY = UnitTypeId.ENGINEERING_BAY
    FACTORY = UnitTypeId.FACTORY
    GHOST_ACADEMY = UnitTypeId.GHOST_ACADEMY
    ARMORY = UnitTypeId.ARMORY
    STARPORT = UnitTypeId.STARPORT
    FUSION_CORE = UnitTypeId.FUSION_CORE

    NEXUS = UnitTypeId.NEXUS
    GATEWAY = UnitTypeId.GATEWAY
    FORGE = UnitTypeId.FORGE
    CYBERNETICS_CORE = UnitTypeId.CYBERNETICS_CORE
    TWILIGHT_COUNCIL = UnitTypeId.TWILIGHT_COUNCIL
    ROBOTICS_FACILITY = UnitTypeId.ROBOTICS_FACILITY
    STARGATE = UnitTypeId.STARGATE
    TEMPLAR_ARCHIVE = UnitTypeId.TEMPLAR_ARCHIVE
    DARK_SHRINE = UnitTypeId.DARK_SHRINE
    ROBOTICS_BAY = UnitTypeId.ROBOTICS_BAY
    FLEET_BEACON = UnitTypeId.FLEET_BEACON

    HATCHERY = UnitTypeId.HATCHERY
    SPAWNING_POOL = UnitTypeId.SPAWNING_POOL
    LAIR = UnitTypeId.LAIR
    BANELING_NEST = UnitTypeId.BANELING_NEST
    ROACH_WARREN = UnitTypeId.ROACH_WARREN
    HYDRALISK_DEN = UnitTypeId.HYDRALISK_DEN
    LURKER_DEN = UnitTypeId.LURKER_DEN
    INFESTATION_PIT = UnitTypeId.INFESTATION_PIT
    SPIRE = UnitTypeId.SPIRE
    HIVE = UnitTypeId.HIVE
    GREATER_SPIRE = UnitTypeId.GREATER_SPIRE
    ULTRALISK_CAVERN = UnitTypeId.ULTRALISK_CAVERN

    @property
    def base_type(self) -> UnitTypeId:
        """The type it is named after, which every other type granting it counts as: a spire, for `SPIRE`."""
        return self.value
