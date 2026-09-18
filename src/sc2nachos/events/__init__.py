"""What an api tells a bot about as a game goes on, and the handlers it tells.

A game hands out `GameStartEvent`, then a turn for each observation but the last, then `GameEndEvent`. A turn hands
out `TurnStartEvent`, then what its observation reports has happened, then `TurnEvent`. What happened comes grouped by
type, in this order:

1. `OwnUnitCreatedEvent`, `EnemyUnitFirstSeenEvent`, `UnitTypeChangedEvent`, `UnitAllianceChangedEvent`;
2. `OwnConstructionStartedEvent`, `OwnConstructionFinishedEvent`, `OwnWarpInFinishedEvent`, `OwnUpgradeFinishedEvent`;
3. `OwnUnitDamagedEvent`, `EnemyUnitDamagedEvent`, `OwnUnitEnergyLostEvent`, `EnemyUnitEnergyLostEvent`,
   `OwnUnitCloakChangedEvent`, `EnemyUnitCloakChangedEvent`, `OwnUnitGainedBuffEvent`, `EnemyUnitGainedBuffEvent`,
   `OwnUnitLostBuffEvent`, `EnemyUnitLostBuffEvent`, `EnemyUnitEnteredSightEvent`, `EnemyUnitLeftSightEvent`;
4. `UnitDiedEvent`, `UnitFoundDeadEvent`, so that a unit created and dead within one observation reads in order;
5. `OwnActionEvent`, `ChatEvent`, `AlertEvent`, the alerts in the order the game raised them.

The first turn reports the units the game starts with as created. An event is made only for a type with a handler
still to run this game, and its step is when NachOS learned of it: a morph in the fog is reported when the unit is
next seen.
"""

from sc2nachos.events._done import Done
from sc2nachos.events._event import (
    AlertEvent,
    ChatEvent,
    EnemyUnitCloakChangedEvent,
    EnemyUnitDamagedEvent,
    EnemyUnitEnergyLostEvent,
    EnemyUnitEnteredSightEvent,
    EnemyUnitFirstSeenEvent,
    EnemyUnitGainedBuffEvent,
    EnemyUnitLeftSightEvent,
    EnemyUnitLostBuffEvent,
    Event,
    GameEndEvent,
    GameStartEvent,
    OwnActionEvent,
    OwnConstructionFinishedEvent,
    OwnConstructionStartedEvent,
    OwnUnitCloakChangedEvent,
    OwnUnitCreatedEvent,
    OwnUnitDamagedEvent,
    OwnUnitEnergyLostEvent,
    OwnUnitGainedBuffEvent,
    OwnUnitLostBuffEvent,
    OwnUpgradeFinishedEvent,
    OwnWarpInFinishedEvent,
    TurnEvent,
    TurnStartEvent,
    UnitAllianceChangedEvent,
    UnitDiedEvent,
    UnitFoundDeadEvent,
    UnitTypeChangedEvent,
)
from sc2nachos.events._event_bus import EventBus
from sc2nachos.events._event_priority import EventPriority
from sc2nachos.events._handler_timings import HandlerTimings

__all__ = [
    "AlertEvent",
    "ChatEvent",
    "Done",
    "EnemyUnitCloakChangedEvent",
    "EnemyUnitDamagedEvent",
    "EnemyUnitEnergyLostEvent",
    "EnemyUnitEnteredSightEvent",
    "EnemyUnitFirstSeenEvent",
    "EnemyUnitGainedBuffEvent",
    "EnemyUnitLeftSightEvent",
    "EnemyUnitLostBuffEvent",
    "Event",
    "EventBus",
    "EventPriority",
    "GameEndEvent",
    "GameStartEvent",
    "HandlerTimings",
    "OwnActionEvent",
    "OwnConstructionFinishedEvent",
    "OwnConstructionStartedEvent",
    "OwnUnitCloakChangedEvent",
    "OwnUnitCreatedEvent",
    "OwnUnitDamagedEvent",
    "OwnUnitEnergyLostEvent",
    "OwnUnitGainedBuffEvent",
    "OwnUnitLostBuffEvent",
    "OwnUpgradeFinishedEvent",
    "OwnWarpInFinishedEvent",
    "TurnEvent",
    "TurnStartEvent",
    "UnitAllianceChangedEvent",
    "UnitDiedEvent",
    "UnitFoundDeadEvent",
    "UnitTypeChangedEvent",
]
