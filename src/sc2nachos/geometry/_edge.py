"""How a source field's circle falls from its value to 0 across its edge."""

from enum import Enum


class Edge(Enum):
    """How a `SourceField`'s circle falls from its value to 0 at its edge: at once, or across a band of `edge_width`
    centered on the edge, by a point's depth `t` in the band, how far it lies from the band's outer end in band widths:
    1 where the band begins, half the edge's width inside the circle, ½ on the circle's edge, and 0 half the width
    beyond it. A band wider than the circle leaves no point holding the whole value, and the center holds what its depth
    gives."""

    SHARP = "sharp"
    """At once: the circle holds its whole value out to its edge, and none beyond, so it takes no width."""
    LINEAR = "linear"
    """Evenly, by `t`: a constant slope across the band, which turns at once where the band begins and ends."""
    SMOOTH = "smooth"
    """By the smoothstep `t²(3 − 2t)`: its slope eases to 0 where the band begins and ends, so that the field has no
    kink there and its gradient no jump."""
