"""CRS helpers: labelling a CRS and choosing a projected CRS to measure in.

Area and length must never be computed on raw longitude/latitude degrees: a degree of
longitude shrinks from ~111 km at the equator to 0 at the poles. Geographic geometries are
projected to a metric CRS built for each feature, centred on its centroid:

* Area   -> Lambert Azimuthal Equal-Area (LAEA). Equal-area by construction, so the area is
            exact on the WGS84 ellipsoid regardless of where the feature is or how big it is.
* Length -> Transverse Mercator with the central meridian through the centroid (scale 1).
            Scale error grows with distance from the centre: ~0.003% at 50 km, ~0.3% at 500 km.

A single UTM zone was the first approach, but UTM's 0.9996 scale factor and growth towards
zone edges gave a 0.12% area error on a 1 km^2 block in Bengaluru; see README.

Geometries already in a projected CRS are measured in that CRS, converting its linear unit
(metres, US survey feet, ...) to metres.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

import shapely
from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry

WGS84 = CRS.from_epsg(4326)
# Densify geographic edges to at most this many degrees, so a long edge that is straight in
# lon/lat (as drawn on a map) keeps that shape after projection instead of becoming a chord.
MAX_SEGMENT_DEGREES = 0.05

MeasureKind = Literal["area", "length"]


@dataclass(frozen=True)
class MeasurementCRS:
    crs: CRS
    label: str
    to_metres: float  # factor converting the CRS's linear unit to metres


def crs_label(crs: CRS) -> str:
    """'EPSG:4326'-style code when one can be identified, else the CRS name."""
    authority = crs.to_authority(min_confidence=70)
    return f"{authority[0]}:{authority[1]}" if authority else crs.name


def looks_like_lon_lat(geometries: list[BaseGeometry | None]) -> bool:
    bounds = [g.bounds for g in geometries if g is not None and not g.is_empty]
    if not bounds:
        return False
    return all(
        minx >= -180 and maxx <= 180 and miny >= -90 and maxy <= 90
        for minx, miny, maxx, maxy in bounds
    )


@lru_cache(maxsize=256)
def _transformer(source: CRS, target: CRS) -> Transformer:
    # always_xy keeps (lon, lat) / (x, y) order regardless of the CRS's official axis order.
    return Transformer.from_crs(source, target, always_xy=True)


def reproject(geometry: BaseGeometry, source: CRS, target: CRS) -> BaseGeometry:
    if source == target:
        return geometry
    return shapely.transform(geometry, _transformer(source, target).transform, interleaved=False)


def prepare_geometry(geometry: BaseGeometry, source: CRS) -> BaseGeometry:
    """Densify long edges of geographic geometries before projecting them."""
    if source.is_geographic:
        return shapely.segmentize(geometry, MAX_SEGMENT_DEGREES)
    return geometry


def choose_measurement_crs(
    geometry: BaseGeometry, source: CRS, kind: MeasureKind
) -> MeasurementCRS:
    if source.is_projected:
        # Projected axes are linear; the factor converts e.g. US survey feet to metres.
        factor = source.axis_info[0].unit_conversion_factor
        if not factor:
            raise ValueError(f"Projected CRS '{source.name}' has no linear unit.")
        return MeasurementCRS(source, crs_label(source), factor)

    if not source.is_geographic:
        raise ValueError(
            f"Cannot measure in CRS '{source.name}' (neither geographic nor projected)."
        )

    centroid = (reproject(geometry, source, WGS84) if source != WGS84 else geometry).centroid
    lon, lat = round(centroid.x, 6), round(centroid.y, 6)
    if kind == "area":
        proj = f"+proj=laea +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs"
    else:
        proj = f"+proj=tmerc +lat_0={lat} +lon_0={lon} +k=1 +datum=WGS84 +units=m +no_defs"
    return MeasurementCRS(_local_crs(proj), proj, 1.0)


@lru_cache(maxsize=1024)
def _local_crs(proj: str) -> CRS:
    return CRS.from_proj4(proj)
