# Values that depend on the game time

A value that changes as the game goes on, such as a supply recorded each step or a rate of losses a bot expects from
5:00 on, is a `TimeDependent`. There are three kinds, all in `sc2nachos.gametime`, and all read the same way:

- [`TimeSeries`](#a-recorded-series) records a value a step.
- [`TimeCurve`](#a-curve) joins a few values, each given at a game time.
- [`TimeFunction`](#a-function) asks a function of the game time.

## Reading one

A `TimeDependent` is called with a `GameTime` and gives the value at that time. Called with many times, it gives a
numpy array of the values at them, which for a series or a curve is far quicker than a call a time:

```python
import numpy

from sc2nachos.gametime import GameTime, TimeCurve

losses = TimeCurve([(GameTime(seconds=300), 0.0), (GameTime(seconds=360), 0.004)])

losses(GameTime(seconds=330))  # 0.002
losses([GameTime(seconds=300), GameTime(seconds=330)])  # array([0.   , 0.002])
losses(GameTime(seconds=200).steps + numpy.arange(6720))  # every step of the five minutes from 3:20
```

Many times are a sequence of `GameTime`s or a numpy array of steps, and the values come back in the array's shape. A
moment is a whole step, so the values at every step of a range give its sum, its smallest and its largest exactly.

A time a `TimeDependent` does not cover raises `IndexError`. Only a recorded series has such times, those before its
first step and after its last. A call given a `default` reads that there instead:

```python
supply(GameTime(steps=8000))  # IndexError: step 8000 outside the recorded range 1000-7719
supply(GameTime(steps=8000), default=None)  # None
supply(numpy.arange(7700, 7740), default=0)  # 0 at each step from 7720 on
```

For many times the default is a number, since an array of numbers holds no `None`; `math.nan` marks the times not
covered.

## A recorded series

A `TimeSeries` holds a value for each step from its `start` to its `end`, the times of its first and its last value.
A bot appends to it as the game goes on, and a step it skipped is filled in along the line between its neighbors:

```python
supply = TimeSeries.empty(int)


@api.on(TurnEvent)
def record_supply(event: TurnEvent) -> None:
    supply.append(event.time, int(api.supply.used))
```

A time between two steps reads the step before it, whose value holds until the game reaches the next. So
`supply(api.time - GameTime(seconds=1))` reads 23 steps back, a second being 22.4 steps.

A series is also indexed by time: `supply[time]` reads as the call does, `supply[start:end]` gives a new series of the
steps from `start` up to `end` and without it, and either is assigned to. `supply.values` is the array of what it
recorded, and `min`, `max`, `mean`, `derivative_at` and its filters read over all of it.

## A curve

A `TimeCurve` is made of pairs of a game time and a value, in order of time. It reads along the line between two of
them. Before its first time it holds its first value and after its last time its last, so it covers every time:

```python
losses = TimeCurve([(GameTime(seconds=300), 0.0), (GameTime(seconds=360), 0.004)])

losses(GameTime(seconds=200))  # 0.0
losses(GameTime(seconds=330))  # 0.002
losses(GameTime(seconds=900))  # 0.004
```

With `Interpolation.STEP` each value is held from its time until the next, which the curve jumps to:

```python
losses = TimeCurve([(GameTime.ZERO, 0.0), (GameTime(seconds=300), 0.004)], interpolation=Interpolation.STEP)

losses(GameTime(seconds=299))  # 0.0
losses(GameTime(seconds=300))  # 0.004
```

A curve that rises along a line and then jumps is a linear one with two values a step apart. A curve is not changed
once made; iterating over it hands its pairs back, to make another from.

## A function

A `TimeFunction` wraps a function of the game time, for anything a curve does not say. It decorates one too, and the
function is still called with a time as it was:

```python
@TimeFunction
def losses(time: GameTime) -> float:
    return 0.004 if time >= GameTime(seconds=300) else 0.0


losses(GameTime(seconds=330))  # 0.004
losses(GameTime(seconds=200).steps + numpy.arange(6720))  # an array, the function asked for each step
```

## Taking one

A function that reads a value at a time takes a `Callable[[GameTime], float]`. Every `TimeDependent` passes as one,
being called with a time, and so does a plain function or a lambda:

```python
def worth_at(time: GameTime, factor: Callable[[GameTime], float]) -> float:
    return base_worth * factor(time)
```

A function that reads many times at once takes a `TimeDependent[float]`, and a plain function is wrapped in a
`TimeFunction` to pass:

```python
def share_lost(loss_rate: TimeDependent[float], start: GameTime, end: GameTime) -> float:
    rates = loss_rate(numpy.arange(start.steps, end.steps))
    return 1.0 - math.exp(-rates.sum() * SECONDS_PER_STEP)
```
