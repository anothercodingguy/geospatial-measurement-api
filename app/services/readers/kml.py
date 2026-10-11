"""KML / KMZ reader built on the standard library XML parser (via defusedxml).

KML coordinates are always WGS84 longitude/latitude (EPSG:4326) per the OGC KML 2.2 spec,
so the dataset CRS is fixed. Altitude values are ignored: measurements are 2D.
"""

from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from pyproj import CRS
from shapely.geometry import (
    GeometryCollection,
    LinearRing,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from app.services.readers.archive import data_members, open_zip
from app.services.readers.base import InvalidGeospatialFile, ParsedDataset, RawFeature

KML_CRS = CRS.from_epsg(4326)
GEOMETRY_TAGS = {"Point", "LineString", "LinearRing", "Polygon", "MultiGeometry"}
# Geometry-like elements we recognise but cannot turn into shapely geometries.
UNSUPPORTED_TAGS = {"Model", "Track", "MultiTrack"}


def read_kml(path: Path) -> ParsedDataset:
    return parse_kml_bytes(path.read_bytes())


def read_kmz(path: Path, max_uncompressed_bytes: int) -> ParsedDataset:
    with open_zip(path, max_uncompressed_bytes) as archive:
        kml_members = [m for m in data_members(archive) if m.filename.lower().endswith(".kml")]
        if not kml_members:
            raise InvalidGeospatialFile("KMZ archive does not contain a .kml file.")
        # By convention the main document is doc.kml at the root; otherwise take the first one.
        main = next((m for m in kml_members if m.filename.lower() == "doc.kml"), kml_members[0])
        return parse_kml_bytes(archive.read(main))


def parse_kml_bytes(data: bytes) -> ParsedDataset:
    try:
        root = ElementTree.fromstring(data)
    except (ParseError, DefusedXmlException) as exc:
        raise InvalidGeospatialFile(f"Invalid KML: {exc}") from exc

    _strip_namespaces(root)
    if root.tag != "kml":
        raise InvalidGeospatialFile("Invalid KML: root element is not <kml>.")

    features: list[RawFeature] = []
    warnings: list[str] = []
    for index, placemark in enumerate(root.iter("Placemark")):
        features.append(_read_placemark(index, placemark))

    if not features:
        warnings.append("The KML file contains no Placemarks.")
    return ParsedDataset(crs=KML_CRS, features=features, warnings=warnings)


def _strip_namespaces(root: Element) -> None:
    # KML appears with the 2.2 namespace, older Google namespaces, or none at all.
    # gx: extensions (e.g. gx:Track) keep their local name, which is all we need.
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def _read_placemark(index: int, placemark: Element) -> RawFeature:
    properties: dict[str, Any] = {}
    for key in ("name", "description"):
        text = placemark.findtext(key)
        if text is not None:
            properties[key] = text.strip()
    properties.update(_extended_data(placemark))

    geometry: BaseGeometry | None = None
    unsupported: str | None = None
    for child in placemark:
        if child.tag in GEOMETRY_TAGS:
            try:
                geometry = _parse_geometry(child)
            except (ValueError, TypeError) as exc:
                raise InvalidGeospatialFile(
                    f"Invalid geometry in Placemark #{index}: {exc}"
                ) from exc
            break
        if child.tag in UNSUPPORTED_TAGS:
            unsupported = child.tag
            break

    return RawFeature(
        index=index,
        source_id=placemark.get("id"),
        geometry=geometry,
        properties=properties,
        unsupported_type=unsupported,
    )


def _extended_data(placemark: Element) -> dict[str, Any]:
    data: dict[str, Any] = {}
    extended = placemark.find("ExtendedData")
    if extended is None:
        return data
    for item in extended.iter("Data"):  # <Data name="x"><value>..</value></Data>
        name = item.get("name")
        if name:
            data[name] = (item.findtext("value") or "").strip()
    for item in extended.iter("SimpleData"):  # <SchemaData><SimpleData name="x">..</SimpleData>
        name = item.get("name")
        if name:
            data[name] = (item.text or "").strip()
    return data


def _parse_coordinates(element: Element | None) -> list[tuple[float, float]]:
    if element is None or not (element.text or "").strip():
        raise ValueError("missing <coordinates>")
    coords = []
    for token in element.text.split():
        parts = token.split(",")
        if len(parts) < 2:
            raise ValueError(f"bad coordinate tuple {token!r}")
        coords.append((float(parts[0]), float(parts[1])))  # lon, lat; altitude dropped
    return coords


def _parse_ring(ring_parent: Element | None) -> list[tuple[float, float]]:
    ring = ring_parent.find("LinearRing") if ring_parent is not None else None
    if ring is None:
        raise ValueError("polygon boundary without <LinearRing>")
    return _parse_coordinates(ring.find("coordinates"))


def _parse_geometry(element: Element) -> BaseGeometry:
    tag = element.tag
    if tag == "Point":
        return Point(_parse_coordinates(element.find("coordinates"))[0])
    if tag == "LineString":
        return LineString(_parse_coordinates(element.find("coordinates")))
    if tag == "LinearRing":
        return LinearRing(_parse_coordinates(element.find("coordinates")))
    if tag == "Polygon":
        shell = _parse_ring(element.find("outerBoundaryIs"))
        holes = [_parse_ring(inner) for inner in element.findall("innerBoundaryIs")]
        return Polygon(shell, holes)
    if tag == "MultiGeometry":
        parts = [_parse_geometry(c) for c in element if c.tag in GEOMETRY_TAGS]
        return _combine(parts)
    raise ValueError(f"unsupported geometry <{tag}>")


def _combine(parts: list[BaseGeometry]) -> BaseGeometry:
    """Turn a KML MultiGeometry into the most specific shapely type."""
    flat: list[BaseGeometry] = []
    for part in parts:
        flat.extend(getattr(part, "geoms", [part]))
    types = {g.geom_type for g in flat}
    if types == {"Point"}:
        return MultiPoint(flat)
    if types <= {"LineString", "LinearRing"}:
        return MultiLineString([LineString(g.coords) for g in flat])
    if types == {"Polygon"}:
        return MultiPolygon(flat)
    return GeometryCollection(flat)
