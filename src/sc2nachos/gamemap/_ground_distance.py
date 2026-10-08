"""How far a ground unit walks from one point to every tile."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy
from scipy.sparse import coo_array
from scipy.sparse.csgraph import dijkstra

from sc2nachos.geometry import Grid

if TYPE_CHECKING:
    from sc2nachos.geometry import PointLike

# A step to each tile beside, and to each tile corner to corner, by how far it goes. The other four directions are
# the same steps taken back, which an undirected graph already holds.
_STEPS = ((1, 0, 1.0), (0, 1, 1.0), (1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)))


def ground_distances(walkable: Grid[bool], start: PointLike) -> Grid[float]:
    """How far a ground unit walks from the tile holding `start` to each tile of `walkable`, center to center: `inf`
    where no walk reaches.

    A walk steps between walkable tiles that share a side or a corner, and across a corner only where both tiles
    sharing a side with the two are walkable too.
    """
    values = numpy.asarray(walkable.values)
    width, height = values.shape
    nodes = numpy.arange(width * height).reshape(width, height)
    sources, targets, lengths = [], [], []
    for dx, dy, length in _STEPS:
        # The tiles a step leaves from and lands on, as slices of the grid in step with each other.
        xs, to_xs = slice(0, width - dx), slice(dx, width)
        ys, to_ys = (
            (slice(0, height - dy), slice(dy, height)) if dy >= 0 else (slice(-dy, height), slice(0, height + dy))
        )
        open_ = values[xs, ys] & values[to_xs, to_ys]
        if dx and dy:
            open_ &= values[to_xs, ys] & values[xs, to_ys]
        sources.append(nodes[xs, ys][open_])
        targets.append(nodes[to_xs, to_ys][open_])
        lengths.append(numpy.full(int(open_.sum()), length))
    graph = coo_array(
        (numpy.concatenate(lengths), (numpy.concatenate(sources), numpy.concatenate(targets))),
        shape=(width * height, width * height),
    ).tocsr()
    x, y = walkable.index_of(start)
    distances = dijkstra(graph, directed=False, indices=int(nodes[x, y]))
    return Grid(distances.reshape(width, height), origin=walkable.origin)
