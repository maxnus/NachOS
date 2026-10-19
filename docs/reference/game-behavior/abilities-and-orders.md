# Abilities and orders

Part of [how the game behaves](README.md), which says how each entry was seen.

## Ids: what a unit is offered, ordered and runs

- A unit is offered and reports its type's own id of an action several types perform. The action's id is never
  offered but is accepted as an order, and the game runs each unit's own: Move (3794) shows as `Move_Move` (16),
  Attack (3674) runs as `attack_Attack` (23), `BurrowDown` burrows any unit, and a general research id researches the
  next level (#23, #28; tested). NachOS reads each unit's own id as the action's (`AbilityId._REMAPPED_IDS`).
- A liberator ordered to siege (2558) reports 2554 running, and unsieging works the same way, though `remaps_to`
  links neither pair (#23). Ordered at a point, it is a sieged liberator by the next observation, and reports 2554
  aimed at itself, not at the point, while its zone forms: 64 steps, after which it reports no order. Ordered the
  siege again meanwhile, it is refused `NotSupported`. NachOS reads 2554 as `LIBERATOR_SIEGE_AT` and the archon's
  1767 as `MORPH_ARCHON` (#103; tested).
- The archon: `Morph_Archon` (1766) given to two templar in one command, high or dark, even 6 apart, walks them to
  each other and merges them, each reporting `Archon_Warp_Target` (1767) targeting the other; given to one alone it
  is refused. 1767 given to both, targeting one of them, merges them too; given to one it does nothing, though it
  answers `Success` (#37; tested).
- `ATTACK` at a point runs `attack_Attack` (23) for a high templar, a lurker, an oracle and an adept's
  shade, which are offered `Scan_Move` (19) too, as it does for a marine. At an enemy drone it is refused
  `NotSupported` by the lurker, the oracle without its beam, and the shade, and taken by the templar and the marine.
  `Scan_Move` ordered by its own id shows as itself for the lurker, the oracle and the shade (tool
  `sweep_orders`, `attack-or-scan`; run 37610767491). A burrowed infestor or roach runs `ATTACK` as
  `Scan_Move` (below). NachOS reads both as `ATTACK`.
- A unit offered `Scan_Move` and not `attack_Attack` (24 types, among them the medivac, raven, observer, overlords,
  warp prism, MULE, infestor, viper and widow mine) runs `ATTACK` as the scan move. A medivac, a raven, an
  observer, a MULE, an infestor and a widow mine given it at a point or at an enemy drone all showed `Scan_Move`,
  and each refused `attack_Attack` `NotSupported`. The widow mine showed its own
  `WIDOW_MINE_ATTACK` ahead of the scan move once the drone was near (tool `sweep_orders`, `scan-only`; run
  37611898076).
- An unburrow's autocast is the type's, not the id's. With an enemy drone beside it, a burrowed roach whose
  `BurrowUp_Roach` or `BurrowUp_Drone` was switched to autocast came up within 50 steps; one whose
  `UNBURROW` was switched stayed down, and a burrowed drone stayed down with either switched. Every switch
  was answered `Success`. The tables allow autocast on the roach's unburrow and not on the drone's (tool
  `sweep_orders`, `unburrow-autocast`; run 37610767491).
- A spell ordered at a target out of reach (storm, neural parasite, the raven's and viper's) shows in the caster's
  orders under the id ordered. Hallucinations, Guardian Shield, Chrono Boost, Supply Drop, warp-ins, bunker orders
  and cancels finish at once and show nothing (#33).
- Creation abilities the tables name that are never offered and do nothing: the lurker's 2104 (2332 works), the rich
  refinery's 325 (a plain refinery on a rich geyser works), the baneling's 80 (4121), the auto-turret's 349 (1764,
  for 50 energy), the locust's 2018 (2704) and purification nova's 2546 (2346). Of the refinery rows 320 and 325,
  only 320 works. The rich assimilator's and rich extractor's rows name no creation ability; the plain one builds
  on a rich geyser (#23, #29).
- Ability 0 is no ability, and Smart is the right-click order. `Cancel_Queue5` and
  `Cancel_QueueCancelToSelection` both remap to `Cancel_Last` (stated).

## What a unit is offered

- `RequestQueryAvailableAbilities` leaves out what a unit lacks the tech for, counts an add-on only for the structure it
  is attached to (a bare barracks is not offered a marauder while another stands with a tech lab), and offers an
  unpowered structure nothing that needs power: an unpowered gateway trains nothing, though a probe is offered a gateway
  and a forge once a nexus stands, with no pylon (#29). Asked to ignore costs, it ignores energy and cooldowns too
  (#29); counting them, it leaves out a warp gate's warp-ins while the gate waits (below).
- A requirement drops out of the answer within 4 steps of its structure leaving the observation; a lifted barracks
  still counts as a barracks, a lowered depot as a depot and a hive as a lair. A ghost is offered 4 steps after its
  academy appears (a tech lab alone is not enough), and weapons level 2 after level 1 (#29; tested).
- A unit is offered only the half of a toggle that fits its state, and the other half up to 22 steps after it
  switches. Cancels and halts are offered only in the state they undo (training, building, a structure going up, a
  cocoon or lurker egg, a channeling ghost, infestor or phoenix, an adept whose shade is out, a nuke arming); a
  locked-on cyclone gets none. Unload is offered only while a transport carries something, and a ghost its nuke only
  while one is armed (#29, #33).
- Once Burrow is researched, every burrowing zerg unit is offered every zerg burrow ability, the infested terran's
  included, and burrows as itself: a zergling given the infested terran's reports `BurrowDown_Zergling` (#29, #33). A
  baneling rolling at an enemy it has seen is offered none of them, its own included, and an order to burrow is
  answered `NotSupported` until it stops (tool `sweep_tech_tree`).
- A roach is offered the ravager morph once a roach warren stands; a warp gate warps in all six gateway units;
  workers carrying minerals are offered a return fresh ones are not; brood lord and overlord cocoons can move,
  patrol and hold; a battlecruiser has its own attack, move, patrol, hold and stop (#29, #33).
- A building SCV is offered `Halt_TerranBuild` (348, named "Halt TerranBuild"), which clears its orders and leaves
  the structure at its progress. The other slot-30 rows, zerg, protoss, the queen's, the raven's and the nydus's, named
  `ZergBuild_30` and the like, are offered to nobody in any state, idle, building, or the half-built structure a drone
  became, and ordered do nothing (#23).

## Verdicts, and what is carried out

- A player that gives no orders still has its workers mine: the game gives those orders itself (corpus).
- Every action in a `RequestAction` gets a verdict, in the order sent, even 500 in one request, and the game carries
  them out in that order: of two unqueued moves to one unit the second stands, an unqueued move after a queued one
  clears it, and a queued move after an unqueued one goes behind it. A stim and a move to one marine are both
  carried out in either order: it moves, stimmed (tool `sweep_orders`).
- Each action is judged against the game state as the last step left it, not as earlier actions in the same step
  change it. When the game steps, it carries them out in order and drops what no longer fits, with no error and no
  entry among the reported actions. Ten marines sent to a barracks in ten requests within one step are all answered
  `Success`: five are queued, or eight with a reactor, and the rest are dropped. Of seven SCVs in one request to a
  command center, five are queued (tool `sweep_orders`).
- An order to a dead unit's tag, or to a tag never used, is answered `Error`; to an enemy unit,
  `YouCantControlThatUnit`. A target of the wrong kind is answered `Error`: a point for a stop or a stim, none for a
  move, a unit for a supply depot (tool `sweep_orders`).
- An enemy structure out of sight is attacked by the tag of the snapshot the observation lists for it; the tag it
  was seen under is refused `NotSupported` (tool `sweep_orders`).

## Supply and money

- With no supply, an order is refused as given: drones get `NotEnoughFood`. Five SCVs queued with 2 supply left
  train 2, and the observation with the second carries a `NotEnoughFood` action error, as it does when the depot
  they need is killed. A depot ordered onto minerals gets `CantFindPlacementLocation`, and one whose ground a sieged
  tank then took gets a `CantBuildLocationInvalid` action error (tool `sweep_alerts`, #37).
- A second SCV or drone ordered with no minerals left, queued or not, is answered `Success`, never made, raises no
  error, and is left out of the actions the next observation reports (tool `sweep_alerts`, #37; tool
  `sweep_orders`).
- Supply is taken when a unit starts, not when it is queued, and a full cap stalls the queue rather than refusing
  it. A barracks with two supply left given five marines takes all five: `food_used` rises by one as the first
  starts, and the four behind it wait at no progress, taking nothing. One given three with the cap full takes all
  three, answers each `Success`, and starts none: no progress for 380 steps, no action error, and nothing dropped.
  Two SCVs queued at once take one supply between them, and cancelling the one that had not started gives none
  back (tool `sweep_orders`).

## Production

- Training and research queue behind what a structure is already making, queued or not: an unqueued train given to
  a command center, hatchery or nexus that is training, or an unqueued research to an engineering bay, forge or
  evolution chamber that is researching, waits its turn like a queued one. A structure holds 5; a sixth is answered
  `QueueIsFull`. A structure with a reactor holds 8 and makes two at once, barracks, factory and starport alike; one
  with a tech lab or no add-on holds 5 and makes one. Given 10 in one step, each kept its 8 or its 5, and exactly 2
  or 1 of them showed progress (tool `sweep_orders`).
- A morph or an add-on given to a structure that is making something is refused, `NotSupported`, queued or not: an
  orbital command or a planetary fortress to a command center training an SCV, a lair to a hatchery training a queen,
  a warp gate to a gateway training a zealot, a tech lab to a barracks training a marine. An idle command center
  morphs and an idle barracks builds its tech lab. A gateway turning into a warp gate by itself after the research
  waits for its zealot to finish (tool `sweep_orders`).
- A barracks stays busy one step longer than its add-on takes: in the observation where the tech lab or reactor
  first reads finished, the barracks still shows the order that built it, is offered no train, and refuses a marine
  `NotSupported`. A step later it takes one (tool `sweep_orders`).
- Every non-producing ability a producing structure is offered leaves what it is making alone, at its progress.
  Each was given to a structure part way through a train or a research, and the structure carried on: the rally of a
  barracks, factory, starport, robotics facility, stargate, nexus, command center, orbital command, planetary
  fortress, hatchery, lair and hive, for workers and units alike; a command center's and a planetary's load; an
  orbital's MULE, scan and supply drop; a nexus's recall and energy recharge; a ghost academy's nuke; and a
  planetary fortress's own stop and attack, for which a barracks is answered `Error` (tool `sweep_orders`).
- A barracks training marines keeps training when given a rally, or a cancel, which drops the last marine; a lift is
  refused `NotSupported`, and a stop, a move or hold position `Error`. Chrono Boost and an inject leave a structure's
  production as it was, and a carrier keeps building interceptors through a stop (tool `sweep_orders`).
- A research that another of this player's structures is already doing is answered `Success`, and nothing is
  researched or reported. A level queued behind the previous one, still in research, is refused `NotSupported` (tool
  `sweep_orders`).
- A structure mid-research is offered no research at all, its own included, though it takes every other line
  queued: an engineering bay took infantry weapons 1, infantry armor 1, building armor and hi-sec auto tracking in
  one request, a forge took ground weapons 1, ground armor 1 and shields 1, and an evolution chamber melee 1,
  missile 1 and ground armor 1, each refusing `NotSupported` the second level of a line it was already on (tool
  `sweep_orders`).
- A structure training two goes on training through a smart, which sets its rally: at a point for a barracks, factory,
  starport, command center, orbital command, planetary fortress, nexus, gateway, robotics facility, stargate, hatchery,
  lair and hive, and at a mineral field, which it then rallies to by tag, for a command center, nexus and hatchery. A
  command center and a planetary fortress go on training through a load-all that takes in an SCV beside them (tool
  `sweep_orders`, `structure-orders`).
- A planetary fortress training two SCVs, given an attack on a command center in range, shows the attack behind its
  trains, fires, and goes on training. Given a stop, before or after the attack, it goes on training, and shows an
  attack again within a step, its own. A loaded bunker shows no order through an attack or a stop, and fires on
  through both at a command center in range (tool `sweep_orders`, `held-orders`).

## Cancels and refunds

- A structure part way through something is offered exactly one cancel, its own, by what it is making; every producer of
  the three races was read. A command center morphing is offered `Cancel_MorphOrbital` or
  `Cancel_MorphPlanetaryFortress`, according to the morph it is running; a barracks, a factory and a starport building
  either add-on `Cancel_BarracksAddOn`, `Cancel_FactoryAddOn` and `Cancel_StarportAddOn`; a hatchery morphing
  `Cancel_MorphLair`, a lair `Cancel_MorphHive` and a spire `Cancel_MorphGreaterSpire`. Everything training or
  researching is offered the queue cancel of its kind: `Cancel_Queue5` for a barracks, an engineering bay, a gateway, a
  forge, an evolution chamber and the rest, `Cancel_QueueCancelToSelection` for a command center, an orbital command, a
  hatchery, a lair and a hive, `Cancel_QueuePasive` for a nexus, `Cancel_QueuePassiveCancelToSelection` for a planetary
  fortress and `Cancel_QueueAddOn` for a tech lab researching on its host. `Cancel_Last` sent to a tech lab took back
  its research, on a barracks, a factory and a starport alike. A warp gate, which keeps no queue, is offered none. A
  cancel is offered only while the work goes on, so an idle structure is offered none; and under `fast_build` a marine
  or an add-on can be done within the step its order lands in, before the structure is first read (tool
  `sweep_tech_tree`). `Cancel_Last` is answered `Error` by a morph and by an add-on, so send the cancel the game offers,
  not the generic one (tool `sweep_orders`).
- Through `RequestAction`, a structure's production can be cancelled only from its last item. `Cancel_Last` and a
  structure's own cancel remove the last item, and a busy structure is offered no other cancel than its own, above.
  `Cancel_Slot` and the generic `Cancel` are answered `Error`, and another structure's cancel and `CancelSlot_Queue5`
  `NotSupported`. The reported action names the structure's own cancel, whichever was sent. A cancel to an idle
  structure is answered `Error`; three cancels to a barracks training two marines are all answered `Success`, and two
  are carried out (tool `sweep_orders`).
- The generic `Cancel` (3659) does whatever cancel a unit is offered, beyond a queue. Each of these was put into the
  state it is offered a cancel in, and in every one it was offered exactly one: a command center morphing to an
  orbital or a fortress, a barracks, factory and starport building an add-on, a hatchery, lair and spire morphing, a
  supply depot going up, a zergling, roach, hydralisk, overlord (to an overseer and to a transport) and corruptor in
  their cocoon or egg, a ghost sniping and arming a nuke, an infestor's neural parasite, a phoenix's graviton beam, a
  void ray's prismatic alignment, an adept with its shade out and the shade itself. Given `Cancel`, each was answered
  `Success`, and the game reported the action as that unit's own cancel. Each was undone as its own cancel undoes it:
  a structure kept its type and lost its order, the depot was gone, a cocoon or egg became the unit it came from, a
  channel stopped, the void ray lost its buff, and the shade was gone. A ghost's nuke still showed 6 steps after
  `Cancel` and was gone 48 steps after, exactly as after the ghost's own `Cancel_Nuke` (tool `sweep_orders`,
  `cancels-whole` and `nuke-cancels`; runs 37610767491 and 37611317534).
- An item in the middle of a queue can be cancelled, but only the same way: the UI action
  `ActionProductionPanelRemoveFromQueue`, with the structure selected. A barracks holding a marine, a reaper, a
  marine, a reaper and a marine, selected by a rally, and asked for index 2: answered `Success`, the third item
  goes, and the next observation reports it as a raw `CancelSlot_Queue5` command on the barracks. The same action in
  a game joined with the raw interface and `raw_affects_selection` but no feature layer is answered `Error`, and the
  slot ids sent raw to the selected barracks are answered as they are to an unselected one: `Cancel_Slot` `Error`,
  `CancelSlot_Queue5` and `CancelSlot_QueueCancelToSelection` `NotSupported`. So a selection is not what those ids
  were missing, and NachOS, which asks for the raw interface alone, can cancel only a structure's last item (tool
  `sweep_orders`).
- A cancel makes no room for what follows it in the same step. A cancel and then a marine to a barracks holding 5:
  the cancel is carried out and the marine refused `QueueIsFull`; a step later a marine is taken. To one holding 4,
  a cancel and then a reaper are both carried out, and the reaper goes last. Cancels followed by an orbital command
  to a command center training SCVs, or a cancel followed by a tech lab to a barracks training a marine, empty the
  queue and refuse the morph or add-on `NotSupported`; a step later it is taken (tool `sweep_orders`).
- A cancel's refund follows a fixed rule and does not depend on progress: all of a train or a research, three
  quarters of a morph, an add-on or a structure going up, rounded up. Seen at real prices, as the jump in the bank
  over the observation the cancel landed in: an SCV 50 of 50 and a research 100/100 of 100/100; an orbital command
  113 of 150, a planetary fortress 113/113 of 150/150, a tech lab 38/19 of 50/25, and a supply depot 75 of 100. An
  orbital cancelled 400 steps into its morph refunds the same 113 as one cancelled 40 steps in, and a depot
  cancelled half way up the same 75 as one barely started (tool `sweep_orders`).
- A cancelled SCV refunds its 50 minerals by the next observation, whether half made or only queued, and the refund
  pays for nothing sent in the same step, to the same structure or another. With 5 minerals, a cancel and then an
  SCV to a command center: the SCV is refused `NotEnoughMinerals`, and a step later the 55 the cancel leaves pay for
  it. With none, 5 cancels to a command center and an orbital command to another, idle one: the orbital is refused
  `NotEnoughMinerals`, and a step later the refunded 250 pay for it (tool `sweep_orders`).

## What an order does to a unit's orders

- A few abilities act at once and leave a unit's orders as they were, so a move carries on: stim, the banshee's and
  ghost's cloak, the ghost's hold fire, the medivac's boost, Guardian Shield, the mothership's cloak field, the
  oracle's pulsar beam, the void ray's prismatic alignment, the overlord's creep, the adept's shade and the
  hydralisk's lunge. Every other ability a unit is offered replaces its orders when given unqueued: stop and hold
  position, morphs, burrows, sieges and landing, blink, charge, hallucinations, force fields, and every spell aimed
  at a unit or a point, even one within reach. Queued, each goes behind the move, except a cyclone's lock-on, which
  is refused `NotSupported` (tool `sweep_orders`).
- The off half of a toggle keeps a unit's orders too, like the on half. A unit is offered the off half only once the
  on half has taken effect, and each was then given to a moving unit: the banshee's and ghost's cloak, the ghost's
  hold fire, the oracle's pulsar beam, the overlord's creep and the baneling's attack on structures. Every one is
  answered `Success`, leaves the move first in the unit's orders, and the unit keeps closing on its point. A lurker's
  hold fire was the one that could not be tried, since it is offered only burrowed, and a burrowed lurker is offered
  no move (tool `sweep_orders`).
- A sieged or burrowed form shows the ability that made it as its first order for as long as it stays in the form: a
  sieged tank its siege mode, which reads as `SIEGE`, and a burrowed lurker `BurrowDown_Lurker`. A sieged tank and a
  burrowed lurker given an attack on a command center in range show it behind that. Unsiege takes the tank off its
  attack, and unburrow and hold fire, the lurker's own or the general id, the lurker; each shows behind the form's order
  until the unit changes. A sieged observer and overseer, a phasing warp prism and a burrowed widow mine hold no order
  to take them off: each answers attack, move, patrol and hold position `Error`. A sieged liberator answers an attack on
  a unit in its zone `TargetIsOutOfRange` for at least 64 steps after it shows as sieged, while its zone forms, and
  takes it 224 steps after; unsiege then takes it off the attack. A burrowed infestor given an attack on a command
  center out of range shows `Scan_Move` at it and moves toward it, 4.9 in 46 steps, and unburrow takes it off the
  attack. A burrowed roach does the same with Tunneling Claws (5.1 in 46 steps); without it, it answers attack, move,
  patrol and hold position `Error`, as a burrowed zergling and swarm host do. The `tech_tree` cheat grants Tunneling
  Claws (tool `sweep_orders`, `held-orders`, `liberator-orders`, `structure-orders` and `burrowed-movers`).
- A supply depot holds no order, lowering included: one reported idle as it finished was not reported idle again once
  told to lower and lowered (tested; local game, build 75689).
- A missile turret, spore crawler, photon cannon, spine crawler, auto turret and planetary fortress attacking one
  enemy switch to a second when given an attack or a smart at it. Given a stop, each shows the same attack again by
  the next observation and fires on, so whether stop took it off and it took the target up again on its own cannot
  be told from its orders (tool `sweep_orders`, `structure-orders`).
- An unqueued order identical to a unit's first, in the ability it runs and the target to 1/4096, is ignored:
  answered `Success`, left out of the reported actions, and free. Four marines attacking pylons, each standing at
  each of four sites in turn for 896 steps, dealt 1382 to 1402 in all whether their attack was sent once or re-sent
  every step, every 4th or every 16th, each round within two shots of the others; a re-sent move covered the same
  ground, and a gather re-sent while the worker gathered mined as much. It still drops the orders queued behind the
  first. An order that differs is carried out: a gather sent to a worker returning its cargo sends it back to the
  field with the cargo, so one re-sent every 16 steps mined 5 minerals in 1344 steps against 65 (tool
  `sweep_orders`).

## Loading and unloading

- A single passenger cannot be unloaded through `RequestAction`: it takes the UI action `ActionCargoPanelUnload`
  after a raw command with ability 0 selects the transport, with `raw_affects_selection` and a feature layer on. The
  medivac's unload abilities unload every passenger at once (stated).
- A loaded command center or planetary fortress training two SCVs goes on training while it unloads, by
  `UnloadAll_CommandCenter` (413, which both are offered) or `UNLOAD`. A command center's unload at a point is refused,
  `NotSupported` by its own id and `Error` by the general one. A loaded medivac, warp prism or transport overlord is
  offered only its unload at a point (`UnloadAllAt`). Aimed at the transport itself, it unloads where the transport is
  and leaves its move going; aimed at a point, it replaces the move and flies there first. `UNLOAD` is answered `Error`
  by each of the three, and their own `_UNLOAD` ids `NotSupported` (tool `sweep_orders`, `held-orders`). Two medivacs
  given a move and `UNLOAD_AT` aimed at each itself, in one request and in either order, put their marines down where
  they stood and flew on; from the next observation each showed only its move (`tests/test_orders.py`, run on GitHub
  Actions).
- Nothing else swept does something different to a unit's orders by what it is aimed at. A moving medivac, warp
  prism and transport overlord given a load at a passenger 1 or 8 away drop their move for it; a moving raven given
  its anti-armor missile or interference matrix, and a battlecruiser its Yamato, at an enemy siege tank in reach or 16
  away, drop theirs. A planetary fortress's attack aimed at a point is refused `MustTargetUnit` (tool
  `sweep_orders`, `target-dependent`).

## One command to several units

- One command naming two units of a type has one of them carry out what costs and a few abilities more, and both
  the rest. Two of each unit type and structure, in each of its forms, were given each ability they are offered that
  costs no minerals or vespene in one command, those that move sent off first, and read for whose energy changed,
  whose type, what effects and units appeared, whose orders changed while the other kept its move, and their cloak,
  buffs, rally and cargo; a transport was loaded first for its unloads, and a structure set to train, morph or go up
  for its cancels.
  - One of them: every spell but a toggle, 44 (a storm, a feedback, an EMP, a snipe, a MULE, a scan, a supply drop,
    a heal, a chrono boost, a recharge, a battery's recharge, every hallucination, a force field, a guardian shield,
    an inject, a transfuse, a creep tumor, a stasis ward, an auto turret, a changeling, a fungal, an abduct, a
    consume, which gave one viper energy, and the rest); a Yamato, a corrosive bile, a reaper's grenade, a lock-on,
    a caustic spray, a purification nova, a widow mine's attack, a time warp; a landing and a rooting at one point; a
    load of one passenger; a salvage, a halt, a cancel of the last item; a warp gate's turn back into a gateway and a
    warp prism's change of mode either way; a spray. A liberator's siege at a point, and a nexus's recall.
  - Both: a move, an attack, a patrol, a stop, a hold, a smart, a gather, a repair, a rally; every burrow and
    unburrow, lift, lowering and raising, uprooting, a tank's, an observer's and an overseer's siege, their unsiege
    and a liberator's, a viking's, a hellion's and a thor's change of mode; a cloak, a pulsar beam, a cloaking field,
    a stim, a blink, a charge, a lunge, a hold fire, a boost, a prismatic alignment, a tactical jump; an adept's
    shade, a swarm host's locusts, a baneling's explosion; a cancel of a morph or of a structure going up; an unload,
    at a point or not.
  - Not read: a baneling's attack on structures and an overlord's creep leave nothing to see; a load-all took the
    workers of one command center in one run and of both in the next, and of both flying command centers and both
    planetary fortresses; and two nydus hold one cargo between them, so which of them took or gave a passenger does not
    show. Both of two motherships took a recall, but a mothership has no energy, and two never stand. A nexus's recall
    found units to take in two of four runs, one nexus recalling each time.

  Under `tech_tree` a medivac regains energy twice as fast as a ghost, 5 to 2.6 over 72 steps, so a cast is told by the
  energy spent. A medivac heals hurt units by itself, which spends energy too: of two given a load, one spent 3.6 with
  its heal on autocast, and neither with it off, so the sweep turns it off first. (Tool `sweep_orders`,
  `group-abilities-terran`, `-protoss` and `-zerg`; local game, Linux build 75689.)
- One command to several units sends each moving unit to its own point around the one ordered, so they keep their
  spacing. Of what only one of them carries out, above, a storm went to a templar with the energy for it and a pylon to
  one of two probes; three barracks given one train made one marine (50 minerals went, and one of the three carried the
  order), two engineering bays given one research made one (100/100), and two barracks given one reactor built one, the
  one with room beside it where the other's spot was blocked. Units that cannot take the order are left out, and the
  verdict is still `Success`: a supply depot among marines given a move, a dead unit's tag among live ones. The same tag
  twice counts once (tool `sweep_orders`). So is a unit that could take it but not now: one unload at a point naming a
  loaded and an empty medivac, in either order, is answered `Success` and the loaded one unloads. Naming only empty
  medivacs, one or two, it is answered `Error`, as it is for one alone (tool `sweep_orders`, `group-verdicts`).
- Units each given the same order in a command of their own, all in one request, each carry it out where there is
  room for each: three barracks given a marine trained three, charged 150; two barracks given a reactor built two,
  100/100; two command centers given the orbital both morphed, 300; two SCVs given a depot at a site each both built,
  200; and two orbital commands given a MULE at one field both spent 50 energy, and two MULEs landed within 68 steps.
  Where there is room for one, every command is answered `Success` and the rest come to nothing. Of two engineering
  bays given the same research, one researched, charged once, 100/100, and the other showed no order, no action error
  and no reported action. Two SCVs given a depot at one site were both charged at once, 200; one built, and the other's
  order ended in the action error `CouldntReachTarget` by 64 steps on, and its 100 came back. Of two flying barracks
  given a landing at one point, one landed within 96 steps, and the other's order ended in `CantLandLocationInvalid`
  64 steps on, leaving it flying; of two spine crawlers given a rooting at one point, one rooted within 224 steps and
  the other's order ended the same way 48 steps on; neither charged anything. Of two warp gates given a zealot's
  warp-in at one point, one warped it in, charged 100, and the other showed no order, no action error and no reported
  action (tool `sweep_orders`, `one-command-each`, twice; local game, build 75689).

## Larvae and warp gates

- One larva given two drones in one request makes two: the game hands each order to a larva of its choosing, and the
  reported action names the larva it used, which need not be the one ordered (tool `sweep_orders`).
- A larva keeps no queue. Given a drone, an overlord and a drone in one request, all three answered `Success`, it
  becomes an egg carrying only the last of them, and the hatchery's own orders stay empty. Every larva of a hatchery
  given a drone becomes a drone egg, and a second order to a larva already spoken for changes nothing (tool
  `sweep_orders`).
- A warp gate keeps no queue either: given two zealot warp-ins in one request, both answered `Success`, it shows no
  orders at all, then or 16 steps later (tool `sweep_orders`).
- A warp gate waits after each warp-in: 448 steps after a zealot or an adept, 512 after a stalker or a sentry, and 720
  after a high or a dark templar, 20, 22.9 and 32.1 seconds. Given one every step, a gate made by debug command took
  the first at once and the next on the step its wait ended, each time; every one given meanwhile was answered
  `NotEnoughCharges` and charged nothing. Meanwhile the gate showed no order, read inactive and wore no buff, and the
  abilities query offered the warp-in when it ignored costs; counting them, it left the warp-in out from the
  observation after a warp-in to the observation the gate was ready (tool `sweep_orders`, `warp-gate-cooldown`;
  local game, Linux build 75689).

## Points

- A point crosses the protocol as a 32-bit float, so a coordinate the game reports is exactly a float, widened; a
  point sent is rounded to a float on the way out, before the game rounds it down to its own lattice. The two
  roundings differ: x = 157.123288015625 goes out as 157.123291015625, a whole lattice step above where rounding
  down alone would land (tool `sweep_orders`; stated).
- The game keeps a point to 1/4096, rounded down: a move to x = 157.123456 is carried out and reported as 157.123291,
  and one to 157.124456 as 157.124268. The reported action keeps the point as sent (tool `sweep_orders`).

## Builds, and work lost

- A builder whose site one of this player's units holds position on gets a `CantBuildLocationInvalid` action error
  and drops its order. A build order takes its structure's cost when given, not when the builder arrives: a depot
  ordered 35 away took 100 of 120 minerals by the next observation, an SCV ordered with the 20 left was answered
  `Success` and never made, and the depot went up once the builder arrived. So a builder never finds its minerals
  spent. A probe's pylon and a drone's spawning pool ordered 33 away take theirs by the next observation too. A
  depot whose site a depot of this player's took while the builder walked there gets a `CouldntReachTarget` action
  error once it arrives, and the 100 comes back in that observation (tool `sweep_orders`, `queue-cases`). A build
  sent late loses nothing: an SCV's depot and a probe's pylon ordered from 30 away were first seen built 281 steps
  after the worker set out, and ordered once a move toward the site had brought the worker within 8, 4, 2, 1 or 0.5
  of it, after 274 to 277. From the step after a build is ordered until the builder starts it, the observation shows
  a unit of the structure's type at the site with `display_type` `Placeholder` (tool `sweep_orders`,
  `close-enough`). A build queued behind a move takes its cost when given too: with 100 minerals, an SCV ordered to
  move and then a queued depot had 0 left while still on its way to the first point. With 65, the queued depot is
  refused `NotEnoughMinerals`, the move is carried out, and minerals mined meanwhile do not bring the depot back. A
  storm queued behind a move, its energy spent meanwhile, is dropped with no error. An action error names the unit
  and the ability, and comes in the observation in which the game gave up (tool `sweep_orders`).
- A builder killed before the game gives up on its build is reported dead and nothing more. An SCV whose site one of
  this player's marines held position on was given up on 108 steps after the order, with `CantBuildLocationInvalid`;
  one sent to another such site and killed on the way got no error at all, then or twelve steps later (tool
  `sweep_orders`).
- A structure killed while making something is reported dead and nothing more. A barracks training three marines,
  killed: the next observation still lists it with all three orders, the one after does not list it at all, and
  neither carries an action or an action error for it. An SCV killed on its way to build is the same. So what a dead
  unit was making is known only from the unit being gone, never from a report (tool `sweep_orders`).
- A research's order leaves its structure in the step its upgrade is first listed, so no observation shows neither:
  infantry weapons 1, ordered at step 218, was last shown at 2777 and listed from 2778, the 2560 steps its row in the
  game's tables gives as its research time. Cancelled 64 steps in with
  `CANCEL_LAST`, it was gone by the next step and gave back 100/100; with its engineering bay killed by debug command
  64 steps in, the bay was gone within 3 steps and 100/100 came back within 18. Neither listed the upgrade (tool
  `sweep_orders`, `research-ends`, twice; local game, build 75689).

## Actions the game reports

- An action names the ability as it runs: a move as `Move_Move` (16), which NachOS reads as `MOVE`, a
  research ordered by its general id as its level (1186) (#28; tested).
- The next observation reports every order the game carried out, stepped or realtime, with the step it was carried
  out at: one action per command, naming the units that took it, the ability as it runs, the target as sent and
  whether it was queued. A camera move and an autocast toggle are reported too. An order refused, one answered
  `Success` but not carried out, and one ignored as identical to a unit's first are not (tool `sweep_orders`).
- In a local realtime game, an order shows in the unit's orders and among the reported actions in the same
  observation, the next one (tool `sweep_orders`).
