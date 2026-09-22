"""The images the game sends its grids as, read into arrays indexed by tile."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy

from sc2nachos.geometry import Rectangle
from sc2nachos.protocol import ProtocolError

if TYPE_CHECKING:
    from numpy import ndarray
    from s2clientprotocol import common_pb2


def image_array(image: common_pb2.ImageData, area: Rectangle) -> ndarray:
    """The whole of `image`, indexed `[x, y]`.

    Raises `ProtocolError` if the image is not as large as it says or does not cover `area`.
    """
    width, height, bits = image.size.x, image.size.y, image.bits_per_pixel
    if bits not in (1, 8) or len(image.data) != math.ceil(width * height * bits / 8):
        raise ProtocolError(f"a {width} by {height} image of {bits} bits a pixel cannot be {len(image.data)} bytes")
    if not Rectangle(0, 0, width, height).encloses(area):
        raise ProtocolError(f"a {width} by {height} image does not cover {area!r}")
    values = numpy.frombuffer(image.data, dtype=numpy.uint8)
    if bits == 1:
        values = numpy.unpackbits(values, count=width * height)
    # Each row is one y, from the bottom of the map up.
    return values.reshape(height, width).T


def image_tiles(image: common_pb2.ImageData, area: Rectangle) -> ndarray:
    """The pixel of `image` for each tile of `area`."""
    xs, ys = area.tile_range()
    return numpy.ascontiguousarray(image_array(image, area)[xs.start : xs.stop, ys.start : ys.stop])
