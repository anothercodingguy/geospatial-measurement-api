"""Per-feature measurement: area for polygons, length for lines, nothing for points."""

from dataclasses import dataclass

from pyproj import CRS
from pyproj.exceptions import ProjError
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity, make_valid

from app.models import MeasurementStatus
from app.services.crs import choose_measurement_crs, prepare_geometry, reproject

AREA_TYPES = {"Polygon", "MultiPolygon"}
LENGTH_TYPES = {"LineString", "MultiLineString", "LinearRing"}
POINT_TYPES = {"Point", "MultiPoint"}


@dataclass
class Measurement:
    status: MeasurementStatus
    type: str | None = None  # "area" | "length"
    value: float | None = None
    unit: str | None = None  # "m2" | "m"
    crs: str | None = None  # label of the projected CRS the value was computed in
    note: str | None = None


def measure(geometry: BaseGeometry | None, source_crs: CRS | None) -> Measurement:
    if geometry is None or geometry.is_empty:
        return Measurement(MeasurementStatus.EMPTY_GEOMETRY, note="Feature has no geometry.")

    geom_type = geometry.geom_type
    if geom_type in POINT_TYPES:
        return Measurement(MeasurementStatus.NOT_APPLICABLE, note="Points have no area or length.")
    if geom_type not in AREA_TYPES | LENGTH_TYPES:
        return Measurement(
            MeasurementStatus.UNSUPPORTED_GEOMETRY,
            note=f"Measurement is not supported for {geom_type}.",
        )
    if source_crs is None:
        return Measurement(
            MeasurementStatus.ERROR, note="The file's CRS is unknown, so it cannot be measured."
        )

    kind = "area" if geom_type in AREA_TYPES else "length"
    note = None
    if kind == "area" and not geometry.is_valid:
        # e.g. a self-intersecting "bow-tie" polygon. Its lobes would cancel out in the area
        # sum, and later steps (densifying) can silently mangle it, so repair it first.
        note = f"Invalid geometry ({explain_validity(geometry)}) was repaired before measuring."
        geometry = make_valid(geometry)

    try:
        target = choose_measurement_crs(geometry, source_crs, kind)
        projected = reproject(prepare_geometry(geometry, source_crs), source_crs, target.crs)
    except (ValueError, ProjError) as exc:
        return Measurement(MeasurementStatus.ERROR, note=f"Reprojection failed: {exc}")

    if kind == "length":
        value = projected.length * target.to_metres
        return Measurement(MeasurementStatus.OK, "length", round(value, 3), "m", target.label)

    # make_valid may return a GeometryCollection; its .area sums only the polygonal parts.
    value = projected.area * target.to_metres**2
    return Measurement(MeasurementStatus.OK, "area", round(value, 3), "m2", target.label, note)
