"""The alerts the game raises for this player."""

from s2clientprotocol import sc2api_pb2

from sc2nachos._enum import ReadableIntEnum

_ALERT = sc2api_pb2.Alert


class Alert(ReadableIntEnum):
    """An alert the game raised for this player. An alert names no unit and no position.

    What each docstring says raises the alert was seen in game. The protocol's `AlertError` and `TrainError` have no
    member: the game raised neither, reporting a failed order as an action error instead. Should it raise either,
    it is skipped.
    """

    NUCLEAR_LAUNCH_DETECTED = _ALERT.NuclearLaunchDetected
    """The enemy launched a nuke, whether or not this player sees where it is aimed. One alert per nuke. This player's
    own nukes raise nothing."""
    NYDUS_WORM_DETECTED = _ALERT.NydusWormDetected
    """The enemy summoned a nydus worm, whether or not this player sees where. One alert per worm. This player's own
    worms raise only `BUILDING_COMPLETE`."""
    ADD_ON_COMPLETE = _ALERT.AddOnComplete
    """An add-on of this player's finished. One alert per add-on."""
    BUILDING_COMPLETE = _ALERT.BuildingComplete
    """A structure of this player's finished: built, morphed from a drone, or a nydus worm, but not an add-on. One
    alert per structure."""
    BUILDING_UNDER_ATTACK = _ALERT.BuildingUnderAttack
    """A structure of this player's, off camera, came under attack. See `UNIT_UNDER_ATTACK` for when the game
    raises none."""
    LARVA_HATCHED = _ALERT.LarvaHatched
    """A queen's inject hatched its larva. One alert per inject, a few steps before the larva are seen. Larva a
    hatchery makes by itself raise none."""
    MERGE_COMPLETE = _ALERT.MergeComplete
    """Two templar of this player's merged into an archon. One alert per archon, some 100 steps after the archon is
    first seen."""
    MINERALS_EXHAUSTED = _ALERT.MineralsExhausted
    """A mineral field this player mined ran out. One alert per field, in the observation the field is gone from."""
    MORPH_COMPLETE = _ALERT.MorphComplete
    """A unit or structure of this player's finished morphing: a lair, a baneling, an overseer or a ravager. One alert
    per morph. A hellion becoming a hellbat raises none."""
    MOTHERSHIP_COMPLETE = _ALERT.MothershipComplete
    """A mothership of this player's finished. It raises no `TRAIN_UNIT_COMPLETE`."""
    MULE_EXPIRED = _ALERT.MULEExpired
    """A MULE of this player's expired. One alert per MULE, in the observation that reports it dead."""
    NUKE_COMPLETE = _ALERT.NukeComplete
    """A nuke was armed at a ghost academy of this player's."""
    RESEARCH_COMPLETE = _ALERT.ResearchComplete
    """A research of this player's that is not a level finished: stimpack, combat shield, zergling speed, warp gate or
    charge. One alert per research."""
    TRAIN_UNIT_COMPLETE = _ALERT.TrainUnitComplete
    """A unit of this player's other than a worker was trained or hatched. One alert per unit, so two for a pair of
    zerglings. A mothership, a warp-in and a morph raise alerts of their own."""
    TRAIN_WORKER_COMPLETE = _ALERT.TrainWorkerComplete
    """A worker of this player's was trained or hatched. One alert per worker."""
    TRANSFORMATION_COMPLETE = _ALERT.TransformationComplete
    """A gateway of this player's became a warp gate, or a warp gate a gateway. One alert per transformation,
    including the gateways that warp gate research turns by itself."""
    UNIT_UNDER_ATTACK = _ALERT.UnitUnderAttack
    """A unit of this player's, off camera, came under attack.

    The game raises none for a unit on camera, and none again for a unit until it has gone some 6000 steps without
    being attacked, counted per unit.

    The camera starts on the main base, with its townhall, workers, mineral fields and geysers on screen, and stays
    there until moved, so an attack on the main raises none. `OwnUnitDamagedEvent` reports every unit of this
    player's that loses health or shields, every turn, whatever the camera shows.
    """
    UPGRADE_COMPLETE = _ALERT.UpgradeComplete
    """A level of this player's weapons or armor finished, or a command center became an orbital command or a
    planetary fortress. One alert per upgrade."""
    VESPENE_EXHAUSTED = _ALERT.VespeneExhausted
    """A geyser this player mined ran out. One alert per geyser."""
    WARP_IN_COMPLETE = _ALERT.WarpInComplete
    """A unit of this player's finished warping in. One alert per unit."""
