"""The alerts the game raises for this player."""

from s2clientprotocol import sc2api_pb2

from sc2nachos._enum import ReadableIntEnum

_ALERT = sc2api_pb2.Alert


class Alert(ReadableIntEnum):
    """An alert the game raised for this player, which names no unit and no position.

    What each docstring says raised it was seen in game, most of it by `tools/sweep_alerts.py`, which also counted
    one alert for each thing it made happen. The protocol's `AlertError` and `TrainError` have no member: nothing the
    sweep did raised either. A morph ordered with no supply left is refused as it is given, and training stalled when
    supply runs short or is lost, or a structure whose ground is found blocked, raises an action error instead. Should
    the game raise either, it is passed over.
    """

    NUCLEAR_LAUNCH_DETECTED = _ALERT.NuclearLaunchDetected
    """The enemy has launched a nuke, one alert for each, whether this player sees where it is aimed or not. This
    player's own nuke raises nothing."""
    NYDUS_WORM_DETECTED = _ALERT.NydusWormDetected
    """The enemy has summoned a nydus worm, one alert for each, whether this player sees where or not. This player's
    own worm raises only `BUILDING_COMPLETE`."""
    ADD_ON_COMPLETE = _ALERT.AddOnComplete
    """An add-on of this player's has finished, one alert for each."""
    BUILDING_COMPLETE = _ALERT.BuildingComplete
    """A structure of this player's has finished, one alert for each: built, become by a drone, or a nydus worm, but
    not an add-on."""
    BUILDING_UNDER_ATTACK = _ALERT.BuildingUnderAttack
    """A structure of this player's out of sight of its camera has come under attack. See `UNIT_UNDER_ATTACK` for when
    the game raises none."""
    LARVA_HATCHED = _ALERT.LarvaHatched
    """A queen's inject has hatched its larva, one alert for each inject, a few steps before the larva are seen. Larva
    a hatchery makes by itself raise none."""
    MERGE_COMPLETE = _ALERT.MergeComplete
    """Two templar of this player's have merged into an archon, one alert for each, some 100 steps after the archon is
    first seen."""
    MINERALS_EXHAUSTED = _ALERT.MineralsExhausted
    """A mineral field this player mined has run out, one alert for each, in the observation it is gone from."""
    MORPH_COMPLETE = _ALERT.MorphComplete
    """A unit or structure of this player's has finished morphing: a lair, a baneling, an overseer or a ravager, one
    alert for each. A hellion becoming a hellbat raises none."""
    MOTHERSHIP_COMPLETE = _ALERT.MothershipComplete
    """A mothership of this player's has finished, without `TRAIN_UNIT_COMPLETE`."""
    MULE_EXPIRED = _ALERT.MULEExpired
    """A MULE of this player's has expired, one alert for each, in the observation that reports it dead."""
    NUKE_COMPLETE = _ALERT.NukeComplete
    """A nuke has been armed at a ghost academy of this player's."""
    RESEARCH_COMPLETE = _ALERT.ResearchComplete
    """Research of this player's that is not a level has finished: stimpack, combat shield, zergling speed, warp gate or
    charge, one alert for each."""
    TRAIN_UNIT_COMPLETE = _ALERT.TrainUnitComplete
    """A unit of this player's that is not a worker has been trained or hatched, one alert for each, two for a pair of
    zerglings. A mothership, a warp-in and a morph raise alerts of their own."""
    TRAIN_WORKER_COMPLETE = _ALERT.TrainWorkerComplete
    """A worker of this player's has been trained or hatched, one alert for each."""
    TRANSFORMATION_COMPLETE = _ALERT.TransformationComplete
    """A gateway of this player's has become a warp gate, or a warp gate a gateway, one alert for each, the gateways
    warp gate research turns by themselves included."""
    UNIT_UNDER_ATTACK = _ALERT.UnitUnderAttack
    """A unit of this player's out of sight of its camera has come under attack.

    The game raises none for a unit its camera shows, and none again for a unit until it has gone some 6000 to 6500
    steps without being attacked: one attacked every 3000 steps raised one alert in 24000 steps. Each unit counts for
    itself, so two attacked at once raise two, and a new unit attacked where another just was raises one.

    The camera starts on the main base, its townhall, workers, mineral fields and geysers on screen, and stays there
    until moved, so an attack on the main raises none. `OwnUnitDamagedEvent` reports every unit of this player's that
    loses health or shields, every turn, whatever the camera shows.
    """
    UPGRADE_COMPLETE = _ALERT.UpgradeComplete
    """A level of this player's weapons or armor has finished, or a command center has become an orbital command or a
    planetary fortress, one alert for each."""
    VESPENE_EXHAUSTED = _ALERT.VespeneExhausted
    """A geyser this player mined has run out, one alert for each."""
    WARP_IN_COMPLETE = _ALERT.WarpInComplete
    """A unit of this player's has finished warping in, one alert for each."""
