"""The state of an order the bot gave."""

from enum import Enum


class OrderState(Enum):
    """What became of an order in its turn, and the game's answer to it. What a unit then did with an order shows in
    its own orders, in events and in `api.action_failures`."""

    PENDING = "pending"
    """Issued this turn, not yet sent."""
    SENT = "sent"
    """Sent and answered `SUCCESS`. The game can still drop it silently, or give up on it later (in game)."""
    REFUSED = "refused"
    """Sent and answered something other than `SUCCESS`. `action_result` says what."""
    OVERRIDDEN = "overridden"
    """Never sent: a later order of the same turn took every unit this one was given to."""
    WITHDRAWN = "withdrawn"
    """Never sent: taken back by the bot."""
    REDUNDANT = "redundant"
    """Never sent: every unit it held was already carrying out the same order, as its first."""
