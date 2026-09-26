"""The state of an order the bot gave."""

from enum import Enum


class OrderState(Enum):
    """What became of an order in its turn, and the game's answer to it. What a unit then did with an order shows in
    its own orders, in events and in `api.action_failures`."""

    GIVEN = "given"
    """Given this turn, not yet sent."""
    SENT = "sent"
    """Sent and answered `SUCCESS`. The game can still drop it silently, or give up on it later (in game)."""
    REFUSED = "refused"
    """Sent and answered something other than `SUCCESS`. `action_result` says what."""
    OVERRIDDEN = "overridden"
    """Never sent: a later order of the same turn took every unit this one was given to."""
    WITHDRAWN = "withdrawn"
    """Never sent: taken back by the bot."""

    @property
    def is_final(self) -> bool:
        """Whether the order's state will not change again: every state but `GIVEN`."""
        return self is not OrderState.GIVEN
