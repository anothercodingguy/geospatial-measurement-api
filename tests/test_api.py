"""End-to-end tests through the HTTP API."""

import io
import zipfile

import pytest
import shapefile
from pyproj import CRS
from shapely.geometry import LineString, Polygon

from tests.helpers import (
    geodesic_area,
    geodesic_length,
    kml_document,
    kml_line,
    kml_point,
    kml_polygon,
    shapefile_zip,
)

# ~1.1 km x 1.1 km block in Bengaluru (UTM zone 43N).
BLR_SQUARE = [(77.59, 12.97), (77.60, 12.97), (77.60, 12.98), (77.59, 12.98), (77.59, 12.97)]
BLR_LINE = [(77.59, 12.97), (77.61, 12.99), (77.63, 12.99)]


def measurements(client, file_id):
    resp = client.get(f"/api/files/{file_id}/measurements/")
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- upload + file info ---------------------------------------------------------------------


def test_kml_upload_returns_file_info(upload, client):
    kml = kml_document(
        kml_polygon("block", BLR_SQUARE),
        kml_line("road", BLR_LINE),
        kml_point("gate", 77.59, 12.97),
    )
    resp = upload("survey.kml", kml)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["filename"] == "survey.kml"
    assert body["status"] == "COMPLETED"
    assert body["feature_count"] == 3
    assert body["crs"] == "EPSG:4326"
    assert body["geometry_types"] == {"Polygon": 1, "LineString": 1, "Point": 1}

    fetched = client.get(f"/api/files/{body['id']}/")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]


def test_list_files(upload, client):
    upload("a.kml", kml_document(kml_point("p", 1, 1)))
    upload("b.kml", kml_document(kml_point("p", 2, 2)))
    body = client.get("/api/files/").json()
    assert body["count"] == 2
    assert {f["filename"] for f in body["results"]} == {"a.kml", "b.kml"}


def test_features_endpoint_exposes_geometry_crs_and_properties(upload, client):
    extra = (
        '<ExtendedData><Data name="owner"><value>BBMP</value></Data>'
        '<SchemaData><SimpleData name="zone">R2</SimpleData></SchemaData></ExtendedData>'
    )
    file_id = upload("f.kml", kml_document(kml_polygon("block", BLR_SQUARE, extra=extra))).json()[
        "id"
    ]
    feature = client.get(f"/api/files/{file_id}/features/").json()["results"][0]
    assert feature["feature_id"] == 0
    assert feature["geometry_type"] == "Polygon"
    assert feature["crs"] == "EPSG:4326"
    assert feature["geometry"]["type"] == "Polygon"
    assert feature["properties"] == {"name": "block", "owner": "BBMP", "zone": "R2"}


# --- measurements: KML (geographic coordinates) ---------------------------------------------


def test_polygon_area_is_projected_and_matches_geodesic(upload, client):
    file_id = upload("a.kml", kml_document(kml_polygon("block", BLR_SQUARE))).json()["id"]
    result = measurements(client, file_id)["results"][0]
    assert result["status"] == "OK"
    assert result["measurement"] == "area"
    assert result["unit"] == "m2"
    assert result["measured_in_crs"].startswith("+proj=laea")  # metres, not degrees
    expected = geodesic_area(Polygon(BLR_SQUARE))
    assert result["value"] == pytest.approx(expected, rel=1e-5)


def test_line_length_matches_geodesic(upload, client):
    file_id = upload("l.kml", kml_document(kml_line("road", BLR_LINE))).json()["id"]
    result = measurements(client, file_id)["results"][0]
    assert result["measurement"] == "length"
    assert result["unit"] == "m"
    assert result["measured_in_crs"].startswith("+proj=tmerc")
    assert result["value"] == pytest.approx(geodesic_length(LineString(BLR_LINE)), rel=1e-5)


def test_point_needs_no_measurement(upload, client):
    file_id = upload("p.kml", kml_document(kml_point("gate", 77.59, 12.97))).json()["id"]
    result = measurements(client, file_id)["results"][0]
    assert result["status"] == "NOT_APPLICABLE"
    assert result["value"] is None


def test_polygon_hole_is_subtracted(upload, client):
    hole = [
        (77.592, 12.972),
        (77.594, 12.972),
        (77.594, 12.974),
        (77.592, 12.974),
        (77.592, 12.972),
    ]
    file_id = upload("h.kml", kml_document(kml_polygon("ring", BLR_SQUARE, [hole]))).json()["id"]
    value = measurements(client, file_id)["results"][0]["value"]
    assert value == pytest.approx(geodesic_area(Polygon(BLR_SQUARE, [hole])), rel=1e-5)


@pytest.mark.parametrize(
    "ring",
    [
        # southern hemisphere (Sydney)
        [(151.20, -33.87), (151.21, -33.87), (151.21, -33.86), (151.20, -33.86), (151.20, -33.87)],
        # ~20 x 15 degrees: far wider than one UTM zone
        [(70, 10), (90, 10), (90, 25), (70, 25), (70, 10)],
        # above 84N, outside UTM's range
        [(10, 85), (20, 85), (20, 86), (10, 86), (10, 85)],
        # western hemisphere (Seattle)
        [(-122.35, 47.60), (-122.33, 47.60), (-122.33, 47.62), (-122.35, 47.60)],
    ],
    ids=["sydney", "subcontinent", "arctic", "seattle"],
)
def test_area_is_accurate_anywhere(upload, client, ring):
    file_id = upload("a.kml", kml_document(kml_polygon("p", ring))).json()["id"]
    result = measurements(client, file_id)["results"][0]
    assert result["value"] == pytest.approx(geodesic_area(Polygon(ring)), rel=1e-5)


def test_multigeometry_polygons_are_summed(upload, client):
    other = [(77.61, 12.97), (77.62, 12.97), (77.62, 12.98), (77.61, 12.98), (77.61, 12.97)]
    polys = "".join(
        "<Polygon><outerBoundaryIs><LinearRing><coordinates>"
        + " ".join(f"{x},{y}" for x, y in ring)
        + "</coordinates></LinearRing></outerBoundaryIs></Polygon>"
        for ring in (BLR_SQUARE, other)
    )
    placemark = f"<Placemark><name>two</name><MultiGeometry>{polys}</MultiGeometry></Placemark>"
    file_id = upload("m.kml", kml_document(placemark)).json()["id"]
    result = measurements(client, file_id)["results"][0]
    assert result["geometry_type"] == "MultiPolygon"
    expected = geodesic_area(Polygon(BLR_SQUARE)) + geodesic_area(Polygon(other))
    assert result["value"] == pytest.approx(expected, rel=1e-5)


def test_mixed_multigeometry_is_reported_as_unsupported_not_crash(upload, client):
    mixed = (
        "<Placemark><name>mixed</name><MultiGeometry>"
        "<Point><coordinates>77.59,12.97</coordinates></Point>"
        "<LineString><coordinates>77.59,12.97 77.60,12.98</coordinates></LineString>"
        "</MultiGeometry></Placemark>"
    )
    file_id = upload("mixed.kml", kml_document(mixed, kml_polygon("ok", BLR_SQUARE))).json()["id"]
    results = measurements(client, file_id)["results"]
    assert results[0]["geometry_type"] == "GeometryCollection"
    assert results[0]["status"] == "UNSUPPORTED_GEOMETRY"
    assert results[1]["status"] == "OK"  # other features still measured


def test_unsupported_kml_element_is_reported(upload, client):
    model = "<Placemark><name>3d</name><Model><Location/></Model></Placemark>"
    file_id = upload("model.kml", kml_document(model)).json()["id"]
    result = measurements(client, file_id)["results"][0]
    assert result["geometry_type"] == "Model"
    assert result["status"] == "UNSUPPORTED_GEOMETRY"


def test_self_intersecting_polygon_is_repaired(upload, client):
    bowtie = [(77.59, 12.97), (77.60, 12.98), (77.60, 12.97), (77.59, 12.98), (77.59, 12.97)]
    file_id = upload("bow.kml", kml_document(kml_polygon("bow", bowtie))).json()["id"]
    result = measurements(client, file_id)["results"][0]
    assert result["status"] == "OK"
    assert "repaired" in result["note"]
    # Two triangles, each a quarter of the bounding square (the raw shoelace sum would be ~0).
    square = geodesic_area(Polygon(BLR_SQUARE))
    assert result["value"] == pytest.approx(square / 2, rel=1e-3)


def test_kml_without_namespace_and_kmz(upload, client):
    kml = kml_document(kml_polygon("block", BLR_SQUARE), namespace=None)
    assert upload("plain.kml", kml).json()["feature_count"] == 1

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc.kml", kml_document(kml_polygon("block", BLR_SQUARE)))
    resp = upload("survey.kmz", buf.getvalue())
    assert resp.status_code == 201
    assert resp.json()["file_type"] == "kmz"


def test_summary_totals(upload, client):
    kml = kml_document(
        kml_polygon("a", BLR_SQUARE), kml_line("r", BLR_LINE), kml_point("p", 77.59, 12.97)
    )
    body = measurements(client, upload("t.kml", kml).json()["id"])
    assert body["summary"]["total_area_m2"] == pytest.approx(
        geodesic_area(Polygon(BLR_SQUARE)), rel=1e-3
    )
    assert body["summary"]["total_length_m"] == pytest.approx(
        geodesic_length(LineString(BLR_LINE)), rel=1e-3
    )
    assert body["summary"]["by_status"] == {"OK": 2, "NOT_APPLICABLE": 1}


def test_pagination(upload, client):
    kml = kml_document(*(kml_point(f"p{i}", 77.59, 12.97) for i in range(5)))
    file_id = upload("many.kml", kml).json()["id"]
    body = client.get(f"/api/files/{file_id}/measurements/?limit=2&offset=2").json()
    assert body["count"] == 5
    assert [r["feature_id"] for r in body["results"]] == [2, 3]


# --- measurements: Shapefile ----------------------------------------------------------------


def test_shapefile_wgs84_polygon(upload, client):
    data = shapefile_zip(shapefile.POLYGON, [[BLR_SQUARE]])
    resp = upload("parcels.zip", data)
    assert resp.status_code == 201, resp.text
    assert resp.json()["crs"] == "EPSG:4326"
    result = measurements(client, resp.json()["id"])["results"][0]
    assert result["value"] == pytest.approx(geodesic_area(Polygon(BLR_SQUARE)), rel=1e-3)


def test_shapefile_in_projected_crs_is_measured_directly(upload, client):
    # A 100 m x 200 m rectangle in UTM 43N coordinates: area must be exactly 20,000 m2.
    rect = [
        (500000, 1430000),
        (500100, 1430000),
        (500100, 1430200),
        (500000, 1430200),
        (500000, 1430000),
    ]
    data = shapefile_zip(shapefile.POLYGON, [[rect]], crs=CRS.from_epsg(32643))
    resp = upload("utm.zip", data)
    assert resp.json()["crs"] == "EPSG:32643"
    result = measurements(client, resp.json()["id"])["results"][0]
    assert result["measured_in_crs"] == "EPSG:32643"
    assert result["value"] == pytest.approx(20_000, abs=0.01)


def test_shapefile_in_feet_is_converted_to_metres(upload, client):
    # NY State Plane Long Island, US survey feet. 1000 ft x 1000 ft square.
    sq = [
        (1000000, 200000),
        (1001000, 200000),
        (1001000, 201000),
        (1000000, 201000),
        (1000000, 200000),
    ]
    data = shapefile_zip(shapefile.POLYGON, [[sq]], crs=CRS.from_epsg(2263))
    result = measurements(client, upload("ft.zip", data).json()["id"])["results"][0]
    us_survey_foot = 1200 / 3937
    assert result["value"] == pytest.approx((1000 * us_survey_foot) ** 2, rel=1e-6)


def test_shapefile_lines_points_and_null_shapes(upload, client):
    lines = shapefile_zip(shapefile.POLYLINE, [[BLR_LINE], None])
    results = measurements(client, upload("lines.zip", lines).json()["id"])["results"]
    assert results[0]["value"] == pytest.approx(geodesic_length(LineString(BLR_LINE)), rel=1e-3)
    assert results[1]["status"] == "EMPTY_GEOMETRY"

    points = shapefile_zip(shapefile.POINT, [(77.59, 12.97)])
    assert measurements(client, upload("pts.zip", points).json()["id"])["results"][0]["status"] == (
        "NOT_APPLICABLE"
    )


def test_shapefile_attributes_are_returned(upload, client):
    data = shapefile_zip(
        shapefile.POLYGON,
        [[BLR_SQUARE]],
        fields=(("name", "C", 50), ("plot_no", "N", 10, 0), ("rate", "F", 10, 2)),
        records=[["Plot A", 42, 3.5]],
    )
    file_id = upload("attrs.zip", data).json()["id"]
    props = client.get(f"/api/files/{file_id}/features/").json()["results"][0]["properties"]
    assert props == {"name": "Plot A", "plot_no": 42, "rate": 3.5}


def test_shapefile_in_subfolder_with_macos_metadata(upload, client):
    inner = zipfile.ZipFile(io.BytesIO(shapefile_zip(shapefile.POLYGON, [[BLR_SQUARE]])))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for info in inner.infolist():
            zf.writestr(f"export/{info.filename}", inner.read(info))
        zf.writestr("__MACOSX/export/._layer.shp", b"junk")
    resp = upload("nested.zip", buf.getvalue())
    assert resp.status_code == 201, resp.text
    assert resp.json()["feature_count"] == 1


def test_shapefile_without_prj_assumes_wgs84_with_warning(upload, client):
    data = shapefile_zip(shapefile.POLYGON, [[BLR_SQUARE]], include=(".shp", ".shx", ".dbf"))
    body = upload("noprj.zip", data).json()
    assert body["crs"] == "EPSG:4326"
    assert any("EPSG:4326 was assumed" in w for w in body["warnings"])


def test_shapefile_without_prj_and_projected_coords_is_not_measured(upload, client):
    rect = [(500000, 1430000), (500100, 1430000), (500100, 1430200), (500000, 1430000)]
    data = shapefile_zip(shapefile.POLYGON, [[rect]], crs=None)
    body = upload("unknown.zip", data).json()
    assert body["status"] == "COMPLETED"
    assert body["crs"] is None
    result = measurements(client, body["id"])["results"][0]
    assert result["status"] == "ERROR"
    assert "CRS is unknown" in result["note"]


# --- invalid input --------------------------------------------------------------------------


def test_unsupported_extension_is_rejected(upload):
    resp = upload("data.geojson", b'{"type": "FeatureCollection", "features": []}')
    assert resp.status_code == 415


def test_empty_upload_is_rejected(upload):
    assert upload("empty.kml", b"").status_code == 400


def test_too_large_upload_is_rejected(upload):
    assert upload("big.kml", b"x" * (3 * 1024 * 1024)).status_code == 413


@pytest.mark.parametrize(
    ("filename", "data", "message"),
    [
        ("bad.zip", b"definitely not a zip", "not a valid zip"),
        ("bad.kml", b"<kml><Placemark>", "Invalid KML"),
        ("notkml.kml", b"<html></html>", "root element is not <kml>"),
        (
            "bad.kml",
            kml_document(
                "<Placemark><LineString><coordinates>1</coordinates></LineString></Placemark>"
            ),
            "Invalid geometry",
        ),
    ],
)
def test_unreadable_files_are_stored_as_failed(upload, client, filename, data, message):
    resp = upload(filename, data)
    assert resp.status_code == 422
    body = resp.json()
    assert body["status"] == "FAILED"
    assert message in body["error"]
    # The failed upload is still queryable, but has no measurements.
    assert client.get(f"/api/files/{body['id']}/").json()["status"] == "FAILED"
    assert client.get(f"/api/files/{body['id']}/measurements/").status_code == 409


def test_zip_without_shapefile(upload):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hello")
    body = upload("nothing.zip", buf.getvalue()).json()
    assert "does not contain a .shp" in body["error"]


def test_zip_missing_dbf(upload):
    data = shapefile_zip(shapefile.POLYGON, [[BLR_SQUARE]], include=(".shp", ".shx", ".prj"))
    assert "missing its .dbf" in upload("nodbf.zip", data).json()["error"]


def test_zip_with_two_shapefiles(upload):
    a = zipfile.ZipFile(io.BytesIO(shapefile_zip(shapefile.POLYGON, [[BLR_SQUARE]], stem="a")))
    b = zipfile.ZipFile(io.BytesIO(shapefile_zip(shapefile.POLYGON, [[BLR_SQUARE]], stem="b")))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for src in (a, b):
            for info in src.infolist():
                zf.writestr(info.filename, src.read(info))
    assert "more than one shapefile" in upload("two.zip", buf.getvalue()).json()["error"]


def test_zip_bomb_guard(upload):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("layer.shp", b"\0" * (11 * 1024 * 1024))  # compresses to a few KB
    assert "too large when uncompressed" in upload("bomb.zip", buf.getvalue()).json()["error"]


def test_xml_entity_expansion_is_blocked(upload):
    billion_laughs = (
        b'<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY a "aaaaaaaaaa">'
        b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><kml>&b;</kml>'
    )
    resp = upload("evil.kml", billion_laughs)
    assert resp.status_code == 422
    assert "Invalid KML" in resp.json()["error"]


def test_unknown_file_id(client):
    assert client.get("/api/files/doesnotexist/").status_code == 404
    assert client.get("/api/files/doesnotexist/measurements/").status_code == 404
