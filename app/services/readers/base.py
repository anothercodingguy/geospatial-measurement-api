from dataclasses import dataclass, field
from typing import Any

from pyproj import CRS
from shapely.geometry.base import BaseGeometry


class InvalidGeospatialFile(Exception):
    """The upload could not be read as a geospatial file. The message is safe to show to clients."""


@dataclass
class RawFeature:
    index: int
    source_id: str | None
    geometry: BaseGeometry | None
    properties: dict[str, Any]
    # Set when the source geometry type has no shapely equivalent (e.g. KML <Model>).
    unsupported_type: str | None = None


@dataclass
class ParsedDataset:
    crs: CRS | None
    features: list[RawFeature]
    warnings: list[str] = field(default_factory=list)
