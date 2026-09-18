"""What an api tells a bot about as a game goes on, and the handlers it tells.

A game hands out `GameStartEvent`, then a turn for each observation but the last, then `GameEndEvent`. A turn hands
out `TurnStartEvent`, then what its observation reports has happened, then `TurnEvent`. What happened comes grouped by
type, in this order:

1. `OwnUnitCreatedEvent`, `EnemyUnitFirstSeenEvent`, `UnitTypeChangedEvent`, `UnitAllianceChangedEvent`;
2. `OwnConstructionStartedEvent`, `OwnConstructionFinishedEvent`, `OwnWarpInFinishedEvent`, `OwnUpgradeFinishedEvent`;
3. `UnitDamagedEvent`, `EnemyUnitEnteredSightEvent`, `EnemyUnitLeftSightEvent`;
4. `UnitDiedEvent`, `UnitFoundDeadEvent`, so that a unit created and dead within one observation reads in order;
5. `OwnActionEvent`, `ChatEvent`, and the alert events, in the order the game raised them.

The first turn reports the units the game starts with as created. An event is made only for a type with a handler
still to run this game, and its step is when NachOS learned of it: a morph in the fog is reported when the unit is
next seen.
"""

from sc2nachos.events._alert_events import (
    AddOnCompleteAlertEvent,
    BuildingCompleteAlertEvent,
    BuildingUnderAttackAlertEvent,
    ErrorAlertEvent,
    LarvaHatchedAlertEvent,
    MergeCompleteAlertEvent,
    MineralsExhaustedAlertEvent,
    MorphCompleteAlertEvent,
    MothershipCompleteAlertEvent,
    MuleExpiredAlertEvent,
    NuclearLaunchDetectedAlertEvent,
    NukeCompleteAlertEvent,
    NydusWormDetectedAlertEvent,
    ResearchCompleteAlertEvent,
    TrainErrorAlertEvent,
    TrainUnitCompleteAlertEvent,
    TrainWorkerCompleteAlertEvent,
    TransformationCompleteAlertEvent,
    UnitUnderAttackAlertEvent,
    UpgradeCompleteAlertEvent,
    VespeneExhaustedAlertEvent,
    WarpInCompleteAlertEvent,
)
from sc2nachos.events._done import Done
from sc2nachos.events._event import (
    ChatEvent,
    EnemyUnitEnteredSightEvent,
    EnemyUnitFirstSeenEvent,
    EnemyUnitLeftSightEvent,
    Event,
    GameEndEvent,
    GameStartEvent,
    OwnActionEvent,
    OwnConstructionFinishedEvent,
    OwnConstructionStartedEvent,
    OwnUnitCreatedEvent,
    OwnUpgradeFinishedEvent,
    OwnWarpInFinishedEvent,
    TurnEvent,
    TurnStartEvent,
    UnitAllianceChangedEvent,
    UnitDamagedEvent,
    UnitDiedEvent,
    UnitFoundDeadEvent,
    UnitTypeChangedEvent,
)
from sc2nachos.events._event_bus import EventBus
from sc2nachos.events._event_priority import EventPriority
from sc2nachos.events._handler_timings import HandlerTimings

__all__ = [
    "AddOnCompleteAlertEvent",
    "BuildingCompleteAlertEvent",
    "BuildingUnderAttackAlertEvent",
    "ChatEvent",
    "Done",
    "EnemyUnitEnteredSightEvent",
    "EnemyUnitFirstSeenEvent",
    "EnemyUnitLeftSightEvent",
    "ErrorAlertEvent",
    "Event",
    "EventBus",
    "EventPriority",
    "GameEndEvent",
    "GameStartEvent",
    "HandlerTimings",
    "LarvaHatchedAlertEvent",
    "MergeCompleteAlertEvent",
    "MineralsExhaustedAlertEvent",
    "MorphCompleteAlertEvent",
    "MothershipCompleteAlertEvent",
    "MuleExpiredAlertEvent",
    "NuclearLaunchDetectedAlertEvent",
    "NukeCompleteAlertEvent",
    "NydusWormDetectedAlertEvent",
    "OwnActionEvent",
    "OwnConstructionFinishedEvent",
    "OwnConstructionStartedEvent",
    "OwnUnitCreatedEvent",
    "OwnUpgradeFinishedEvent",
    "OwnWarpInFinishedEvent",
    "ResearchCompleteAlertEvent",
    "TrainErrorAlertEvent",
    "TrainUnitCompleteAlertEvent",
    "TrainWorkerCompleteAlertEvent",
    "TransformationCompleteAlertEvent",
    "TurnEvent",
    "TurnStartEvent",
    "UnitAllianceChangedEvent",
    "UnitDamagedEvent",
    "UnitDiedEvent",
    "UnitFoundDeadEvent",
    "UnitTypeChangedEvent",
    "UnitUnderAttackAlertEvent",
    "UpgradeCompleteAlertEvent",
    "VespeneExhaustedAlertEvent",
    "WarpInCompleteAlertEvent",
]
