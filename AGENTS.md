# AGENTS.md

## Project Overview

**NachOS** (package `sc2nachos`) — an event-driven StarCraft II bot API for Python, built directly on
`s2clientprotocol`. It replaces `python-sc2` as the client interface for the bot
[AvocaDOS](https://github.com/maxnus/AvocaDOS), and is intended to be published for other bot authors.

The migration plan lives in the AvocaDOS repo at `docs/plans/nachOS-plan.md`, with its rationale in
`docs/plans/nachOS-initial-prompt.md`. How to decide which game ids are real, and to refresh them after a patch,
is in `docs/curating-ids.md`, what the game has been seen to do in `docs/game-behavior.md`, and every event a game
hands out in `docs/events.md`.

## Keep this file small

Every agent reads all of it before every task. A line belongs here only if you can name the task whose outcome
it changes; everything else goes where it is used, as **Where a finding goes** below says. Delete a line the
code has overtaken.

## Python

Use this repository's own environment, `.venv`, which `uv sync --extra dev` creates, and run every tool
through `uv run`, as CI does. Never system Python, and never assume anything about what else is checked out
next to this repository.

**`Path.read_text` and `Path.write_text` default to the locale encoding here, which is cp1252.** Pass
`encoding="utf-8"` to both, or an em dash written back to a source file silently becomes invalid UTF-8
and ruff refuses to read it.

**A `Final` dataclass field can only be set by the generated `__init__`**, and pyright rejects any later
assignment, in `__post_init__` too. A field that is fetched rather than passed in comes from a classmethod that
calls the constructor, as `_Game.start` does.

## Core design rules

These are the non-negotiables. They exist because this library is published for others, not just used by AvocaDOS.

- **No module-level mutable state, anywhere.** NachOS never creates or exposes a singleton. It exposes an
  instance-based `Api`; the consuming bot decides whether to make one global. Two `Api` instances must be
  able to coexist in one process.
- **The API is instantiated, never subclassed.** Do not document, encourage or design for bot authors inheriting
  from `Api`, and never add a mixin or extension hook for them to hang helpers on. Their helpers belong in
  their own modules as ordinary functions. A felt need to subclass is a signal that NachOS is missing an API —
  treat it as a bug report against this library, not as a pattern to support.
- **`Api.__init__` must be cheap and must not require a live connection.** Consumers construct it at import
  time so that `@api.event.on(...)` decorators can run as their modules load. Connecting happens in
  `run_local` / `run_ladder`, which take an already-built instance.
- **No AvocaDOS-shaped quirks.** Nothing may exist in NachOS solely to make something work in AvocaDOS. If in
  doubt, the awkwardness stays in AvocaDOS. Better still, fix it so neither side needs it.
- **NachOS owes python-sc2 nothing.** This is a new package, not a fork and not a compatible replacement — no API
  compatibility, no naming, no behavior. python-sc2 is a reference for *what the game requires*, never for *how to
  express it*. When porting, the question is never "what does python-sc2 do here" but "what should this do", and
  where it is wrong or awkward NachOS must be right, even if that means the consuming bot has to change. The
  failure mode is silent: matching the reference feels like diligence, which is how its bugs and its internal
  development names get copied in. Already caught: `Point2.rounded` there is `math.floor`; `Rect` subclasses
  `Point2` and so has a `distance_to`; `PUNISHERGRENADES` is what a player calls concussive shells. Wherever
  NachOS behaves differently in a way a bot moving over could trip on, add it to
  `docs/migrating-from-python-sc2.md`, concisely.
- **Scope:** anything useful to any bot maker, if it is (or can be made) acceptable quality — protocol, state, data
  model, units, orders, events, geometry, pathfinding, map analysis, generic utilities. Not: strategy, build
  orders, combat micro, roles, economy management.
- **The protocol transport is injectable.** Everything above it must be testable with no game client running,
  against recorded observation fixtures.
- **Performance matters.** This code sits in the bot's hot loop. Do not add abstraction layers that cost step time.

## Conventions

Carried over from AvocaDOS, so the two codebases read alike:

- **Imports**: separate stdlib, third-party and internal imports with blank lines.
- **Keyword-only args**: use `*` in signatures liberally.
- **One class per file** (except small data classes). File named after the class, snake-cased.
- **Name a class for what it is responsible for**, so the name says what it does.
- **Type hints** on all parameters and return types.
- **Docstrings** on all public functions and classes, but without parameter/return sections.
- **`__all__`** only where it earns its place — package `__init__.py` files that curate a public surface.
- **`TYPE_CHECKING` guard** for imports that would otherwise be circular.
- **loguru**, not stdlib `logging`.
- **Errors**: a failure of the library's own raises a subclass of `NachOSError`, which also subclasses the
  built-in it is a case of, where one fits: `ConnectionClosedError` is a `ConnectionError`. Misuse, such as a bad
  argument, raises the built-in (`ValueError`, `TypeError`, `IndexError`). A dependency's exception is translated
  where it enters, with `raise ... from`, and never reaches the caller.
- **US spelling** everywhere in code, comments, docstrings and docs — `behavior`, `initialize`, `summarize`,
  `color`, `center`. The exception is generated identifiers: `ids/raw/` mirrors Blizzard's own names verbatim
  (`BuildinProgressNonCancellable`), and those are data, never to be "corrected".
- Line length 120. `ruff check` and `ruff format --check` must pass.
- **One game loop is a step.** Above the protocol layer time is counted in steps -- `Api.step`,
  `steps_per_turn`, `steps_to_seconds` -- and the bot's own cycle is a turn, which nothing counts. The protocol
  layer keeps Blizzard's `game_loop`, because the messages it hands back carry that field, and `Api.play` is
  the one place the two meet. Never write "frame" for either.
- **Where a finding goes**: a rule that shapes code not yet written goes here, in a line or two. A fact about one
  piece of code goes beside that code, in its docstring or a comment. What the game was seen to do goes in
  `docs/game-behavior.md`, with how it was seen, and the code states only the part it relies on, tagged
  `(in game)` or `(corpus)`. The steps for one kind of task go in `docs/`, and measurements in the commit message or
  PR.

## Review checklist

What reviews keep finding, each item from a real bug, is in `docs/code-review.md`. Read it before writing or
reviewing code.

## Talking to the game

- **The authoritative protocol documentation is the comments in `sc2api.proto`**, and the `s2clientprotocol`
  package on PyPI ships only generated code, which carries none of them. Read the source:
  `https://raw.githubusercontent.com/Blizzard/s2client-proto/master/s2clientprotocol/sc2api.proto`
- **One connection can play game after game**, so anything held because it does not change during a game is held
  per game, never per connection.
- **`ResponseGameInfo` never changes during a game.** Ask once, at the start; python-sc2 asks on every step.
- **`ResponseData` changes with the asking player's upgrades, and only with them**, though a unit type has one
  entry and no player. Asked at the start, before any upgrade, it holds the base values both sides share. Each
  unit reports its own upgrade levels, visible enemies' included.
- **Debug cheats change more than their names say**, `god` the weapons in `ResponseData` among them. Read
  `docs/cheats.md` before turning one on.
- **`race_actual` in `ResponseGameInfo` is filled only for your own player.**
- **The game leaves out what a player could not know, and the field then reads zero**: a remembered unit's health,
  an enemy's orders. Answer from the last report that had it, or raise `NotReportedError`; never answer the zero.
- **What an order does can show up an observation late.** On the ladder, and in any realtime game, an add-on or a
  stim buff may appear two observations after the order rather than in the next. Never write code, or a test, that
  needs an order's effect in the very next observation: wait for it.
- **A tag is not a unit's identity.** A structure is remembered under a new tag every time it goes out of sight.
  Name a unit by its NachOS `id`, and translate every tag the game hands over.

## Testing

`pytest`. Tests must not require StarCraft II to be installed or running, with the single exception of tests
marked `@pytest.mark.integration`, which a plain `pytest` run deselects. Everything else runs against recorded
protobuf fixtures via the fixture transport.

**The corpus** in `tests/corpus` is seven whole games, a bare api losing to the computer, one on each map of the
current ladder pool, recorded by `tools/record_corpus.py`, which says what each one is. Replaying one asks the
same questions in the same order, so a change to what the library asks a game fails `test_corpus.py` until the
corpus is recorded again. The bare api gives no orders, so the only orders in it are those the game gives on its
own, nearly all of them workers mining, and since nothing leaves its base it shows the computer's army but none
of its buildings.

| Task | Command |
|---|---|
| Set up | `uv sync --extra dev` |
| Run tests | `uv run pytest` |
| Run the tests that start a game | `uv run pytest -m integration` |
| Lint | `uv run ruff check .` and `uv run ruff format --check .` |
| Type check | `uv run pyright` |
| Regenerate raw ids | `uv run python tools/generate_ids.py`, after refreshing `data/stableid.json` as `docs/curating-ids.md` says |
| Regenerate `UnitType` | `uv run python tools/generate_unit_types.py`, after changing `UnitTypeId` |
| Record the corpus again | `uv run python tools/record_corpus.py`, which starts the game |
| Look at the ramps on a map | `uv run python tools/show_ramps.py`, which starts the game |
| Find the buffs a game puts on units | `uv run python tools/sweep_buffs.py`, which starts the game |
| Find which alerts a game raises, and when | `uv run python tools/sweep_alerts.py`, which starts the game |
