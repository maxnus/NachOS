"""Find how much vespene workers collect from a refinery in a set time, on plain gathers and returns and with a move at
each trip's end, at one, two and three workers a geyser.

Needs StarCraft II installed. Plays one game on the sandbox's map for each trial, as Terran against the very easy
computer, which keeps the game going, where a game alone ends at once. Every starting SCV is handed to speed mining's
`mine`; once there are the minerals for it, the miner nearest each geyser of the main is released to build a refinery
on it, as a refinery made there by debug command took no collector (`sc2` run 41827726722). Once both refineries are
finished and their builders out of them, `SpeedMining.collect` takes each refinery's builder and the miners nearest it,
as many as the trial says. A trial is a count of collectors a refinery, a seed, and plain or moved:

- Plain, speed mining keeps each collector on plain gathers and returns, as `collect` does.
- Moved, the sweep also gives a collector on its gather at its refinery, or on its return, the move speed mining gives
  its miners: within 0.75 to 2 of where its trip ends, a move there with the gather queued behind, or a right-click on
  the townhall, which the worker shows as a return. The trip to the refinery ends `inset` short of where a worker's
  circle touches the refinery's, toward the townhall's center, as a miner's ends 0.175 short of a field's; the trip
  back ends 3.125 from the townhall's center, toward that point.

After a warm-up from the hand-over, it records over the time measured what each refinery's contents fell by, the
minerals and vespene the score says were collected, the actions the game refused, the most enemy units in sight at
once, which the computer may send at some point, how many of each refinery's collectors were seen gathering at a
mineral field, and in a moved trial the moves given::

    uv run python tools/sweep_gas.py
    uv run python tools/sweep_gas.py --collectors 2 3 --insets 0 0.175 0.35 --seeds 1 2 --out gas.json

On GitHub, the `sc2` workflow runs it, `tools/sweep_gas.py` as the script and the arguments as given. The findings are
written as JSON: the build, the settings, and each trial's.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from _sandbox import MAP
from loguru import logger

from sc2nachos import Api, GameTime, Race, run_local
from sc2nachos.events import GameStartEvent, TurnEvent
from sc2nachos.extensions.speed_mining import SpeedMining
from sc2nachos.gamemap import Expansion
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.match import Computer, Difficulty
from sc2nachos.run import Installation
from sc2nachos.units import OwnUnit, Unit, UnitType

# The distances from where a trip ends between which a collector on its gather or return is given the move, and how far
# from a townhall's center the move of a trip back ends: speed mining's for its miners.
_NEAREST_DISTANCE_FOR_THE_MOVE = 0.75
_FARTHEST_DISTANCE_FOR_THE_MOVE = 2.0
_RETURN_POINT_DISTANCE = 3.125
# An SCV's radius (corpus).
_WORKER_RADIUS = 0.375
# Steps between turns: one, so that a move goes out as soon as a worker comes within reach of it.
_STEPS_PER_TURN = 1
# How long the refineries may take to be finished, before the warm-up: the minerals for both, an SCV's walk to each
# geyser, and a build of 480 steps (in game).
_SETUP_TIME = GameTime(seconds=90)
# The computer, which keeps the game going: on the ladder's maps a game alone ends at once, in a victory (in game).
_OPPONENT = Computer(Race.ZERG, Difficulty.VERY_EASY)


@dataclass(frozen=True, kw_only=True)
class _Trial:
    """What one game measures: `collectors` a refinery, seeded `seed`, plain where `inset` is `None`, else moved with
    the gather move ending `inset` short of touching the refinery."""

    collectors: int
    seed: int
    inset: float | None


@dataclass(kw_only=True)
class _RefineryFindings:
    """What one refinery gave over the time measured."""

    position: tuple[float, float]
    distance_to_the_townhall: float
    radius: float
    collectors: int
    vespene_taken: int
    collectors_seen_mining: int
    """How many of its collectors were seen gathering at a mineral field over the time measured."""
    gather_point: tuple[float, float] | None = None
    """Where the trip to it ends, in a moved trial."""


@dataclass(kw_only=True)
class _TrialFindings:
    """What one trial's game recorded."""

    trial: _Trial
    measured_from_step: int
    measured_to_step: int
    refineries: list[_RefineryFindings] = field(default_factory=list)
    vespene_collected: float = 0.0
    minerals_collected: float = 0.0
    moves_given: int = 0
    actions_refused: int = 0
    most_enemies_in_sight: int = 0


class _TrialGame:
    """One trial's game: the refineries made, the SCVs handed over, the moves given in a moved trial, and what it
    records."""

    def __init__(self, trial: _Trial, *, warm_up: GameTime, measured: GameTime) -> None:
        self.api: Api[SpeedMining] = Api(extensions=SpeedMining)
        self.findings: _TrialFindings | None = None
        self._trial = trial
        self._warm_up = warm_up
        self._measured = measured
        self._main: Expansion | None = None
        self._builder_by_geyser: dict[Unit[UnitType.AnyVespeneGeyser], OwnUnit[UnitType.Worker]] = {}
        self._handed_over_at: GameTime | None = None
        self._measuring_from: GameTime | None = None
        self._measuring_from_step = 0
        self._vespene_left_at_the_start: dict[Unit[UnitType.GasBuilding], int] = {}
        self._collected_at_the_start = (0.0, 0.0)
        self._trip_ends_by_refinery: dict[Unit[UnitType.GasBuilding], tuple[Point, Point]] = {}
        self._seen_mining_by_refinery: dict[Unit[UnitType.GasBuilding], set[int]] = {}
        self._moves_given = 0
        self._actions_refused = 0
        self._most_enemies_in_sight = 0
        self.api.on(GameStartEvent)(self._start_mining)
        self.api.on(TurnEvent)(self._play_the_turn)

    def _start_mining(self, _: GameStartEvent) -> None:
        """Hand every SCV to speed mining to mine at the main."""
        main = self.api.expansions[0]
        self._main = main
        for scv in self.api.units.own.workers:
            self.api.extensions.mine(scv, main.mineral_fields)

    def _build_the_refineries(self, main: Expansion) -> None:
        """Release the miner nearest each geyser of `main` with no refinery of this player's on it, and no builder on
        the way, to build one there, while there are the minerals for it."""
        api = self.api
        minerals = api.resources.minerals
        cost = api.data.units[UnitTypeId.REFINERY].cost.minerals
        standing = {gas_building.position for gas_building in api.units.own.gas_buildings}
        for geyser in main.geysers:
            builder = self._builder_by_geyser.get(geyser)
            if geyser.position in standing or (builder is not None and builder.is_alive and builder.orders):
                continue
            miners = [scv for scv in api.extensions.miners if scv.is_orderable]
            if minerals < cost or not miners:
                return
            builder = min(miners, key=lambda scv: scv.distance_to(geyser))
            api.extensions.release(builder)
            api.orders.issue(builder, AbilityId.SCV_BUILD_REFINERY, target=geyser)
            self._builder_by_geyser[geyser] = builder
            minerals -= cost

    def _finished_refineries(self, main: Expansion) -> list[Unit[UnitType.GasBuilding]] | None:
        """This player's refineries on the geysers of `main`, in their order, once every one is finished and its builder
        is out of it and no longer reads as constructing, else `None`."""
        by_position = {gas_building.position: gas_building for gas_building in self.api.units.own.gas_buildings}
        refineries = []
        for geyser in main.geysers:
            refinery = by_position.get(geyser.position)
            builder = self._builder_by_geyser.get(geyser)
            if (
                refinery is None
                or not refinery.is_complete
                or builder is None
                or not builder.is_orderable
                or builder.is_constructing
            ):
                return None
            refineries.append(refinery)
        return refineries

    def _play_the_turn(self, _: TurnEvent) -> None:
        """Hand the SCVs over once the refineries stand, give the moves in a moved trial, and record the time
        measured."""
        api = self.api
        main = self._main
        if main is None:
            return
        if self._handed_over_at is None:
            if (refineries := self._finished_refineries(main)) is None:
                self._build_the_refineries(main)
            else:
                self._hand_over(main, refineries)
            return
        if self._trial.inset is not None:
            self._give_the_moves(main)
        self._actions_refused += len(api.action_failures)
        self._most_enemies_in_sight = max(self._most_enemies_in_sight, len(api.units.enemy.visible))
        if self._measuring_from is None and api.time >= self._handed_over_at + self._warm_up:
            self._measuring_from = api.time
            self._measuring_from_step = api.step
            self._vespene_left_at_the_start = {
                refinery: refinery.vespene_contents for refinery in self._trip_ends_by_refinery
            }
            collected = api.score.collected
            self._collected_at_the_start = (collected.minerals, collected.vespene)
            self._moves_given = 0
            self._actions_refused = 0
            self._most_enemies_in_sight = 0
        elif self._measuring_from is not None and self.findings is None:
            self._note_the_collectors_mining()
            if api.time >= self._measuring_from + self._measured:
                self.findings = self._read_the_findings(main)

    def _note_the_collectors_mining(self) -> None:
        """Note each collector on a gather at a mineral field, by its refinery."""
        for scv, refinery in self.api.extensions.collectors.items():
            current_order = scv.current_order if scv.is_orderable else None
            if current_order is None or current_order.ability is not AbilityId.GATHER:
                continue
            target = current_order.known_target
            if isinstance(target, Unit) and UnitType.AnyMineralField.includes(target):
                self._seen_mining_by_refinery.setdefault(refinery, set()).add(scv.id)

    def _hand_over(self, main: Expansion, refineries: Sequence[Unit[UnitType.GasBuilding]]) -> None:
        """Hand each of `refineries`' builder and the miners nearest it to speed mining to collect, as many as the trial
        says, and work out where each refinery's trips end. Raises `RuntimeError` for a worker `collect` refuses, as
        the time measured would then measure nothing."""
        speed_mining = self.api.extensions
        for geyser, refinery in zip(main.geysers, refineries, strict=True):
            builder = self._builder_by_geyser[geyser]
            miners = sorted(
                (scv for scv in speed_mining.miners if scv.is_orderable), key=lambda scv: scv.distance_to(refinery)
            )
            for scv in [builder, *miners[: self._trial.collectors - 1]]:
                if speed_mining.collect(scv, refinery) is None:
                    raise RuntimeError(f"collect refused {scv!r} at {refinery!r}")
            self._trip_ends_by_refinery[refinery] = _trip_ends(main, refinery, self._trial.inset or 0.0)
        self._handed_over_at = self.api.time

    def _give_the_moves(self, main: Expansion) -> None:
        """Give each collector on its gather at its refinery, or on its return, near where its trip ends, the move there
        with the gather or a right-click on the townhall queued behind."""
        townhall = main.townhall
        if townhall is None:
            return
        order_book = self.api.orders
        for scv, refinery in self.api.extensions.collectors.items():
            if not scv.is_orderable or order_book.issued_to(scv, include_sent=True):
                continue
            current_order = scv.current_order
            if current_order is None:
                continue
            gather_point, return_point = self._trip_ends_by_refinery[refinery]
            then_ability: AbilityId
            then_target: Unit[UnitType.AnyResource] | Unit[UnitType.Townhall]
            if current_order.ability is AbilityId.GATHER and current_order.known_target is refinery:
                trip_end, then_ability, then_target = gather_point, AbilityId.GATHER, refinery
            elif current_order.ability is AbilityId.RETURN:
                trip_end, then_ability, then_target = return_point, AbilityId.SMART, townhall
            else:
                continue
            if _NEAREST_DISTANCE_FOR_THE_MOVE < scv.distance_to(trip_end) < _FARTHEST_DISTANCE_FOR_THE_MOVE:
                order_book.issue(scv, AbilityId.MOVE, target=trip_end, force=True)
                order_book.issue(scv, then_ability, target=then_target, queued=True)
                self._moves_given += 1

    def _read_the_findings(self, main: Expansion) -> _TrialFindings:
        """What the time measured gave, read at its end."""
        api = self.api
        collected = api.score.collected
        start_minerals, start_vespene = self._collected_at_the_start
        findings = _TrialFindings(
            trial=self._trial,
            measured_from_step=self._measuring_from_step,
            measured_to_step=api.step,
            vespene_collected=collected.vespene - start_vespene,
            minerals_collected=collected.minerals - start_minerals,
            moves_given=self._moves_given,
            actions_refused=self._actions_refused,
            most_enemies_in_sight=self._most_enemies_in_sight,
        )
        for refinery, (gather_point, _return_point) in self._trip_ends_by_refinery.items():
            findings.refineries.append(
                _RefineryFindings(
                    position=(refinery.position.x, refinery.position.y),
                    distance_to_the_townhall=refinery.distance_to(main.center),
                    radius=refinery.radius,
                    collectors=len(api.extensions.workers_at(refinery)),
                    vespene_taken=self._vespene_left_at_the_start[refinery] - refinery.vespene_contents,
                    collectors_seen_mining=len(self._seen_mining_by_refinery.get(refinery, ())),
                    gather_point=None if self._trial.inset is None else (gather_point.x, gather_point.y),
                )
            )
        return findings


def _trip_ends(main: Expansion, refinery: Unit[UnitType.GasBuilding], inset: float) -> tuple[Point, Point]:
    """Where the moves of the trips between `refinery` and the townhall at `main`'s center end: `inset` short of where a
    worker's circle touches the refinery's, toward the center, and 3.125 from the center, toward that point."""
    center = main.center
    gather_point = refinery.position.towards(center, refinery.radius + _WORKER_RADIUS - inset)
    return gather_point, center.towards(gather_point, _RETURN_POINT_DISTANCE)


def _play(trial: _Trial, *, warm_up: GameTime, measured: GameTime) -> _TrialFindings:
    """Play `trial`'s game and return what it recorded."""
    game = _TrialGame(trial, warm_up=warm_up, measured=measured)
    run_local(
        MAP,
        game.api,
        _OPPONENT,
        race=Race.TERRAN,
        steps_per_turn=_STEPS_PER_TURN,
        time_limit=_SETUP_TIME + warm_up + measured,
        random_seed=trial.seed,
    )
    if game.findings is None:
        raise RuntimeError(f"{trial} ended before the time measured did: the refineries were not finished in time")
    return game.findings


def _summary(findings: _TrialFindings, measured: GameTime) -> str:
    """One line on `findings`: the trial, and the vespene a refinery gave a minute."""
    trial = findings.trial
    mode = "plain" if trial.inset is None else f"moved, inset {trial.inset}"
    per_minute = 60 / measured.seconds
    refineries = ", ".join(
        f"{refinery.vespene_taken * per_minute:.0f} at {refinery.distance_to_the_townhall:.2f} "
        f"({refinery.collectors_seen_mining} seen mining)"
        for refinery in findings.refineries
    )
    return (
        f"{trial.collectors} a geyser, seed {trial.seed}, {mode}: vespene a minute {refineries}; "
        f"minerals {findings.minerals_collected:.0f}; moves {findings.moves_given}, "
        f"refused {findings.actions_refused}; enemies in sight {findings.most_enemies_in_sight}"
    )


def main(argv: Sequence[str]) -> None:
    """Play every trial the arguments make, plain and moved, and write the findings."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--collectors", type=int, nargs="+", default=[1, 2, 3], help="collectors a refinery")
    parser.add_argument(
        "--insets", type=float, nargs="+", default=[0.175], help="how far short of touching a gather move ends"
    )
    parser.add_argument("--seeds", type=int, nargs="+", default=[1], help="the games' random seeds")
    parser.add_argument("--warm-up", type=float, default=20.0, help="game seconds from the hand-over to measuring")
    parser.add_argument("--measured", type=float, default=180.0, help="game seconds measured")
    parser.add_argument("--out", type=Path, default=Path("gas.json"), help="where to write the findings")
    arguments = parser.parse_args(argv)
    if any(collectors not in (1, 2, 3) for collectors in arguments.collectors):
        raise SystemExit("a refinery takes one to three collectors")
    warm_up = GameTime(seconds=arguments.warm_up)
    measured = GameTime(seconds=arguments.measured)
    trials = [
        _Trial(collectors=collectors, seed=seed, inset=inset)
        for seed in arguments.seeds
        for collectors in arguments.collectors
        for inset in (None, *arguments.insets)
    ]
    all_findings = []
    for trial in trials:
        findings = _play(trial, warm_up=warm_up, measured=measured)
        logger.info(_summary(findings, measured))
        all_findings.append(findings)
    report = {
        "build": Installation.find().build_to_launch(),
        "map": MAP,
        "warm_up_seconds": arguments.warm_up,
        "measured_seconds": arguments.measured,
        "trials": [asdict(findings) for findings in all_findings],
    }
    arguments.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for findings in all_findings:
        print(_summary(findings, measured))
    logger.info("Wrote the findings to {}", arguments.out)


if __name__ == "__main__":
    main(sys.argv[1:])
