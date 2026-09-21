"""Geometry primitives in the game's coordinate space."""

from sc2nachos.geometry._area import Area
from sc2nachos.geometry._circle import Circle
from sc2nachos.geometry._grid import Grid
from sc2nachos.geometry._point import Point, Point3D, PointLike
from sc2nachos.geometry._shapes import Rectangle, Tile, TileSet
from sc2nachos.geometry._tile_path import TilePath

__all__ = [
    "Area",
    "Circle",
    "Grid",
    "Point",
    "Point3D",
    "PointLike",
    "Rectangle",
    "Tile",
    "TilePath",
    "TileSet",
]
