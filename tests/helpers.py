"""Builders for test fixtures: KML documents and zipped shapefiles created in memory."""

import io
import zipfile

import shapefile
import shapely
from pyproj import CRS, Geod
from pyproj.enums import WktVersion
from shapely.geometry.base import BaseGeometry

GEOD = Geod(ellps="WGS84")
WGS84 = CRS.from_epsg(4326)
KML_NS = "http://www.opengis.net/kml/2.2"


def geodesic_area(geom: BaseGeometry) -> float:
    """Reference ellipsoidal area in m2, used to check projected results.

    The geometry is densified like the API does, so both treat edges as straight lines in
    lon/lat (for small features this makes no difference).
    """
    # pyproj sums signed ring areas, so rings must be oriented (shell CCW, holes CW).
    area, _ = GEOD.geometry_area_perimeter(shapely.orient_polygons(shapely.segmentize(geom, 0.05)))
    return abs(area)


def geodesic_length(geom: BaseGeometry) -> float:
    return GEOD.geometry_length(shapely.segmentize(geom, 0.05))


def coords_text(coords) -> str:
    return " ".join(f"{x},{y},0" for x, y in coords)


def kml_document(*placemarks: str, namespace: str | None = KML_NS) -> bytes:
    ns = f' xmlns="{namespace}"' if namespace else ""
    body = "\n".join(placemarks)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><kml{ns}><Document>{body}</Document></kml>'.encode()
    )


def kml_polygon(name: str, shell, holes=(), extra: str = "") -> str:
    inner = "".join(
        f"<innerBoundaryIs><LinearRing><coordinates>{coords_text(h)}</coordinates>"
        "</LinearRing></innerBoundaryIs>"
        for h in holes
    )
    return (
        f"<Placemark><name>{name}</name>{extra}<Polygon><outerBoundaryIs><LinearRing>"
        f"<coordinates>{coords_text(shell)}</coordinates></LinearRing></outerBoundaryIs>"
        f"{inner}</Polygon></Placemark>"
    )


def kml_line(name: str, coords) -> str:
    return (
        f"<Placemark><name>{name}</name><LineString><coordinates>{coords_text(coords)}"
        "</coordinates></LineString></Placemark>"
    )


def kml_point(name: str, x: float, y: float) -> str:
    point = f"<Point><coordinates>{x},{y}</coordinates></Point>"
    return f"<Placemark><name>{name}</name>{point}</Placemark>"


def shapefile_zip(
    shape_type: int,
    shapes: list,
    crs: CRS | None = WGS84,
    fields=(("name", "C", 50),),
    records: list | None = None,
    stem: str = "layer",
    include: tuple[str, ...] = (".shp", ".shx", ".dbf", ".prj"),
) -> bytes:
    """Write a shapefile with pyshp into a zip. `shapes`: coordinate lists, None = null shape."""
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    with shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shape_type) as writer:
        for field in fields:
            writer.field(*field)
        for i, coords in enumerate(shapes):
            if coords is None:
                writer.null()
            elif shape_type == shapefile.POLYGON:
                writer.poly(coords)
            elif shape_type == shapefile.POLYLINE:
                writer.line(coords)
            elif shape_type == shapefile.POINT:
                writer.point(*coords)
            writer.record(*(records[i] if records else [f"feature-{i}"]))

    files = {".shp": shp.getvalue(), ".shx": shx.getvalue(), ".dbf": dbf.getvalue()}
    if crs is not None:
        files[".prj"] = crs.to_wkt(WktVersion.WKT1_ESRI).encode()

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for ext, data in files.items():
            if ext in include:
                zf.writestr(f"{stem}{ext}", data)
    return out.getvalue()
