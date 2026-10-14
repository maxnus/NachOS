"""The ground around resources that a townhall may not cover."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sc2nachos.geometry import Footprint

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sc2nachos.geometry import Grid
    from sc2nachos.units import Unit

# The tiles a resource keeps townhalls off, laid around its center: its footprint, a mineral field's 2x1 and a geyser's
# 3x3, grown by 3 tiles on every side, less one tile at each corner of a mineral field's and three at a geyser's (tool
# `sweep_townhall_placement`).
_MINERAL_FIELD_TOWNHALL_BLOCKING_ZONE = Footprint.rectangle(8, 7, cut_corners=1)
_GEYSER_TOWNHALL_BLOCKING_ZONE = Footprint.square(9, cut_corners=2)


def _keep_townhalls_off_resources(
    grid: Grid[bool], *, mineral_fields: Iterable[Unit[Any]], geysers: Iterable[Unit[Any]]
) -> None:
    """Set false on `grid` the ground around each of `mineral_fields` and `geysers` that a townhall may not cover: a
    command center, a nexus and a hatchery alike (tool `sweep_townhall_placement`)."""
    for unit in mineral_fields:
        grid.fill_footprint(_MINERAL_FIELD_TOWNHALL_BLOCKING_ZONE, at=unit.position, value=False)
    for unit in geysers:
        grid.fill_footprint(_GEYSER_TOWNHALL_BLOCKING_ZONE, at=unit.position, value=False)
