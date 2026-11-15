"""How a source field's circle falls from its value to 0 across its edge."""

from enum import Enum


class Edge(Enum):
    """How a `SourceField`'s circle falls from its value to 0 across its edge, a band of `edge_width` inside the circle:
    by the share `t` of the band between a point and the circle's edge, 1 where the band begins and 0 at the edge."""

    LINEAR = "linear"
    """Evenly, by `t`: a constant slope across the band, which turns at once where the band begins and ends."""
    SMOOTH = "smooth"
    """By the smoothstep `t²(3 − 2t)`: its slope eases to 0 where the band begins and ends, so that the field has no
    kink there and its gradient no jump."""
