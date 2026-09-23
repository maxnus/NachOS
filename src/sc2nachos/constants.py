"""Constants of the game itself."""

# Steps in one second at the game's Normal speed. The game gives some times, such as a weapon's cooldown, in Normal
# seconds.
STEPS_PER_NORMAL_SECOND = 16
# Ladder and multiplayer games run at the "Faster" speed, 1.4x Normal: 22.4 steps per second of real time.
STEPS_PER_SECOND = 22.4
# Multiplying by this lands on every whole second exactly. Dividing by 22.4 overshoots the whole seconds in the
# top quarter of a power of two: 15 seconds comes out as 15.000000000000002.
SECONDS_PER_STEP = 1 / STEPS_PER_SECOND
# Converts a speed the game gives per Normal second to per Faster second, the speed games are played at.
FASTER_PER_NORMAL_SPEED = STEPS_PER_SECOND / STEPS_PER_NORMAL_SECOND


# The game truncates a point it is given to this fraction of a tile: a move to x = 157.123456 is carried out and
# reported at 157.123291 (in game).
POINT_PRECISION = 1 / 4096


def steps_to_seconds(steps: float) -> float:
    """Game steps as seconds of real time."""
    return steps * SECONDS_PER_STEP


def seconds_to_steps(seconds: float) -> float:
    """Seconds of real time as game steps, unrounded."""
    return seconds * STEPS_PER_SECOND
