"""The state of an order the bot gave."""

from enum import Enum


class OrderState(Enum):
    """What became of an order. Each state is something the game was seen to do (tool `sweep_orders`)."""

    GIVEN = "given"
    """Given this turn, not yet sent."""
    SENT = "sent"
    """Sent and answered `SUCCESS`, with no observation since to say what came of it."""
    RUNNING = "running"
    """The game reported a unit carrying it out, or the unit was already doing it and it was not sent again."""
    DONE = "done"
    """No unit it was given to is carrying it out any more."""
    LOST = "lost"
    """Every unit seen carrying it out died before it was done, so nothing came of it. Where none was ever seen
    carrying it out, every unit it was sent to died. The game reports only that a producer died, and a dead unit
    carries nothing out, so this is what tells a barracks killed halfway through a marine from one that finished it.
    Of three barracks given one train, the one that took it counts. An ability whose effect is its unit's death, such
    as a baneling exploding, reads `LOST` too. So does an order whose unit finished it and died before the next
    observation, which cannot be told from a unit killed at it. A larva's order is `DONE` instead: its egg is reported
    dead when what it makes hatches, and an egg killed first reads the same (in game)."""
    REFUSED = "refused"
    """Sent and answered something other than `SUCCESS`. `action_result` says what."""
    DROPPED = "dropped"
    """Answered `SUCCESS` and never carried out. The game does this silently when an order no longer fits by the
    time it steps: a sixth marine, or an order there are no minerals left for."""
    FAILED = "failed"
    """Carried out and then given up on. An `ActionFailure` says why: a builder's site taken meanwhile, for one."""
    OVERRIDDEN = "overridden"
    """A later order took every unit this one was given to: either one from the same turn, given before this was
    sent, or one from a later turn, which the game carried out in its place. `action_result` tells them apart: it is
    `None` for an order never sent."""
    WITHDRAWN = "withdrawn"
    """Taken back by the bot. An order already sent is only forgotten: the unit carries on with it."""

    @property
    def is_final(self) -> bool:
        """Whether nothing more can become of the order, so NachOS has stopped following it."""
        return self in _FINAL


_FINAL = frozenset(
    {
        OrderState.DONE,
        OrderState.LOST,
        OrderState.REFUSED,
        OrderState.DROPPED,
        OrderState.FAILED,
        OrderState.OVERRIDDEN,
        OrderState.WITHDRAWN,
    }
)
