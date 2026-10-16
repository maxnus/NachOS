# Debugging

A bot draws on the game's screen, turns cheats on and makes units through `api.debug`. What it asks for in a turn goes
out after the turn's orders, in one debug request. The game takes it from a client that joined the game as from the one
that created it (in game); what a ladder's own proxy lets through, NachOS cannot see, and a debug request the game
refuses raises `ProtocolError` out of `api.play`, ending the bot's game, so a bot draws only where it is let. The Linux
client renders nothing, so a drawing shows only in the game's window on Windows or macOS (stated).

## Drawing

```python
@api.events.on(TurnEvent)
def label(event: TurnEvent) -> None:
    for marine in api.units.own.of_type(UnitType.Marine):
        api.debug.draw_text(f"{marine.health:.0f}", at=marine)
    api.debug.draw_text_on_screen(f"step {event.step}", at=(0.01, 0.01))


@api.events.on(GameStartEvent)
def mark_the_expansions(event: GameStartEvent) -> None:
    for expansion in api.expansions:
        api.debug.draw_sphere(expansion.center, 2, color=(0, 255, 0), steps=22_400)
```

- **Where.** A point of two coordinates is drawn on the ground, and one of three at its own height. A unit is drawn
  where it stands, and a drawing at a unit follows it while the drawing lasts. Text on the screen is placed from 0 to 1
  across and down, whatever the window's size. A box stands over a `Rectangle` from the ground up 1.
- **How long.** A drawing lasts the `steps` it is given, 1 by default, which is the turn it is made in.
- **How often.** `api.debug.refresh_steps`, 1 unless set, is the fewest steps between two refreshes of the drawing;
  meanwhile the screen keeps the last one sent. A refresh whose drawing is what the screen already shows sends nothing,
  so a drawing that never changes costs a request when it is made and one when it ends. A cheat or a command goes out at
  once, with the drawing as it stands. Set it as the bot's module loads:

  ```python
  api = Api()
  api.debug.refresh_steps = 4
  ```

- **Colors** are red, green and blue, each from 0 to 255, and white unless given.

## Cheats

```python
api.debug.turn_on(Cheat.SHOW_MAP)
if Cheat.GOD not in api.debug.cheats:
    api.debug.turn_on(Cheat.GOD)
api.debug.give_resources()
```

A cheat is on for the whole game. `api.debug.cheats` is what this player has turned on, and turning one on that is on
already, or off that is off, sends nothing. In a game of two the other player can switch a cheat too, which this does
not see. [cheats.md](cheats.md) says what several do to what the game reports; read it before a test turns one on.

Three hand something out each time they are called: `give_minerals()` 5000 minerals, `give_resources()` 5000 minerals
and 5000 vespene, and `give_upgrades()` the next level of every upgrade of every race (in game).

## Commands

```python
api.debug.create_units(UnitTypeId.MARINE, at=natural.center, count=4)
api.debug.create_units(UnitTypeId.ZERGLING, at=natural.center, count=8, owner=Alliance.ENEMY)
api.debug.set_vitals(api.units.own.of_type(UnitType.Marine), life=10)
api.debug.kill(api.units.enemy.of_type(UnitType.Zergling))
api.debug.declare_victory()
```

- **`create_units`** makes units for this player, the one player it plays against, or the map, whose units read owner
  16. The game can make them a few steps late, beside the point, or not at all: no structure on ground it cannot stand
  on, and a larva or a creep tumor only on creep ([game behavior](game-behavior.md#cheats-and-debug-commands)).
- **`set_vitals`** sets life, energy or shields, and raises `ValueError` for 0, which the game sets to the most a unit
  can have instead. `kill` takes a unit's life.
- **`declare_victory()` and `surrender()`** end the game by the next step, its result reported as any ending's.
  `api.leave_game()` concedes too, but nothing is reported after it, and `play` returns `Result.DEFEAT`.
- Hanging or crashing the game client and setting a curriculum map's score stay raw protocol messages, through
  `api.client.debug`.
