# Geospatial File Measurement API

A FastAPI service that accepts a geospatial file (a zipped **Shapefile**, **KML** or **KMZ**),
extracts every feature, and returns **area** for polygons and **length** for lines, computed in
an appropriate projected coordinate system (never on raw latitude/longitude degrees).

```
POST /api/files/                     upload + process a file
GET  /api/files/{id}/                file information (status, CRS, feature count, ...)
GET  /api/files/{id}/features/       features: id, geometry type, geometry, CRS, properties
GET  /api/files/{id}/measurements/   per-feature area / length + totals
```

Interactive docs (Swagger UI) are served at `http://localhost:8000/docs` when the app runs.

---

## Setup

Requires **Python 3.12+**. No GDAL installation is needed (see [Design decisions](#design-decisions)).

### With uv (recommended)

```bash
uv sync                                  # creates .venv and installs locked dependencies
uv run uvicorn app.main:app --reload     # http://localhost:8000
```

### With pip

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### With Docker

```bash
docker build -t geo-measure-api .
docker run -p 8000:8000 -v geo-data:/data geo-measure-api
```

### Configuration

All settings can be overridden with environment variables (or a `.env` file):

| Variable | Default | Meaning |
|---|---|---|
| `GEO_DATABASE_URL` | `sqlite:///./data/geo.db` | SQLAlchemy database URL |
| `GEO_UPLOAD_DIR` | `./data/uploads` | Where raw uploads are stored |
| `GEO_MAX_UPLOAD_BYTES` | `52428800` (50 MB) | Maximum upload size |
| `GEO_MAX_UNCOMPRESSED_BYTES` | `524288000` (500 MB) | Maximum uncompressed size of a .zip/.kmz |

### Tests

```bash
uv run pytest          # 39 tests
uv run ruff check .    # lint
```

Sample files live in [`samples/`](samples) (regenerate with `uv run python scripts/make_samples.py`).

---

## API

### `POST /api/files/` — upload and process a file

Multipart form with a single `file` field. Accepted: `.zip` (containing one Shapefile),
`.kml`, `.kmz`.

```bash
curl -F "file=@samples/survey.kml" http://localhost:8000/api/files/
```

`201 Created`

```json
{
  "id": "c786f9a1835d4b6e967c5d81b469a232",
  "filename": "survey.kml",
  "file_type": "kml",
  "feature_count": 3,
  "crs": "EPSG:4326",
  "crs_name": "WGS 84",
  "status": "COMPLETED",
  "error": null,
  "warnings": [],
  "geometry_types": { "LineString": 1, "Point": 1, "Polygon": 1 },
  "created_at": "2026-10-11T08:17:27.734666Z",
  "processed_at": "2026-10-11T08:17:27.755395Z"
}
```

| Status | When |
|---|---|
| `201` | File processed (`status: COMPLETED`). Individual features may still be unmeasurable — see `measurements`. |
| `400` | Empty upload |
| `413` | Larger than `GEO_MAX_UPLOAD_BYTES` |
| `415` | Unsupported extension (nothing is stored) |
| `422` | File was stored but could not be read (corrupt zip, no `.shp`, invalid KML, ...). Body is the file record with `status: FAILED` and an `error` message; the record stays queryable. |

### `GET /api/files/{id}/` — file information

Same body as the upload response. `404` if the id is unknown.

`GET /api/files/?limit=50&offset=0` lists uploads, newest first.

### `GET /api/files/{id}/features/` — extracted features

```bash
curl "http://localhost:8000/api/files/c786f9a1835d4b6e967c5d81b469a232/features/?limit=1"
```

```json
{
  "file_id": "c786f9a1835d4b6e967c5d81b469a232",
  "count": 3,
  "limit": 1,
  "offset": 0,
  "results": [
    {
      "feature_id": 0,
      "source_id": "park-boundary",
      "geometry_type": "Polygon",
      "crs": "EPSG:4326",
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [[77.59, 12.97], [77.6, 12.97], [77.6, 12.98], [77.59, 12.98], [77.59, 12.97]],
          [[77.592, 12.972], [77.594, 12.972], [77.594, 12.974], [77.592, 12.974], [77.592, 12.972]]
        ]
      },
      "properties": { "name": "Survey block", "surveyor": "Team A" }
    }
  ]
}
```

`feature_id` is the 0-based position in the file; `source_id` is the KML `id` attribute or the
Shapefile record number. Geometry is GeoJSON in the file's own CRS.

### `GET /api/files/{id}/measurements/` — measurements

```bash
curl http://localhost:8000/api/files/c786f9a1835d4b6e967c5d81b469a232/measurements/
```

```json
{
  "file_id": "c786f9a1835d4b6e967c5d81b469a232",
  "crs": "EPSG:4326",
  "summary": {
    "total_area_m2": 1152277.868,
    "total_length_m": 5268.807,
    "by_status": { "NOT_APPLICABLE": 1, "OK": 2 }
  },
  "count": 3,
  "limit": 100,
  "offset": 0,
  "results": [
    {
      "feature_id": 0,
      "source_id": "park-boundary",
      "geometry_type": "Polygon",
      "status": "OK",
      "measurement": "area",
      "value": 1152277.868,
      "unit": "m2",
      "measured_in_crs": "+proj=laea +lat_0=12.975083 +lon_0=77.595083 +datum=WGS84 +units=m +no_defs",
      "note": null
    },
    {
      "feature_id": 1,
      "source_id": "access-road",
      "geometry_type": "LineString",
      "status": "OK",
      "measurement": "length",
      "value": 5268.807,
      "unit": "m",
      "measured_in_crs": "+proj=tmerc +lat_0=12.984142 +lon_0=77.608284 +k=1 +datum=WGS84 +units=m +no_defs",
      "note": null
    },
    {
      "feature_id": 2,
      "source_id": "control-point",
      "geometry_type": "Point",
      "status": "NOT_APPLICABLE",
      "measurement": null,
      "value": null,
      "unit": null,
      "measured_in_crs": null,
      "note": "Points have no area or length."
    }
  ]
}
```

Measurement `status` values:

| Status | Meaning |
|---|---|
| `OK` | Measured. `value` is in `m2` (area) or `m` (length). |
| `NOT_APPLICABLE` | Point / MultiPoint — nothing to measure. |
| `UNSUPPORTED_GEOMETRY` | e.g. a mixed `GeometryCollection` or a KML `<Model>`; reported, not crashed on. |
| `EMPTY_GEOMETRY` | Feature has no geometry (e.g. a Shapefile null shape). |
| `ERROR` | Measurement attempted but impossible, e.g. the CRS is unknown. `note` explains. |

`409 Conflict` is returned for `features/` and `measurements/` of a `FAILED` file.
Both list endpoints accept `limit` (1–1000) and `offset`.

---

## Architecture

### Application structure

```
app/
├── main.py                 create_app(): settings, DB engine, routers
├── config.py               Settings (GEO_* env vars)
├── db.py                   SQLAlchemy engine/session + get_session dependency
├── models.py               UploadedFile, Feature (+ status enums)
├── schemas.py              Pydantic response models
├── api/files.py            HTTP layer: validation, status codes, pagination
└── services/
    ├── processing.py       upload pipeline (store → read → measure → persist)
    ├── measurement.py      area/length for one geometry
    ├── crs.py              CRS labelling + choice of projected CRS
    └── readers/
        ├── base.py         RawFeature / ParsedDataset / InvalidGeospatialFile
        ├── archive.py      safe zip opening (size / entry limits)
        ├── shapefile.py    .zip → features (pyshp)
        └── kml.py          .kml / .kmz → features (defusedxml)
tests/                      API-level tests with in-memory generated fixtures
samples/                    example KML and zipped Shapefile
```

The HTTP layer knows nothing about geometry, and the readers know nothing about HTTP or the
database. Each reader turns its format into the same `ParsedDataset` (CRS + list of
shapely geometries with properties), so measurement and persistence are format-agnostic.
Adding GeoJSON or GeoPackage support means adding one reader.

### File-processing flow

1. **Validate** the extension (`415` if unsupported) and stream the body with a size cap (`413`).
2. **Create** an `UploadedFile` row (`PROCESSING`) and **store** the raw bytes under
   `data/uploads/{id}/`.
3. **Read** with the matching reader:
   - **Shapefile (.zip):** open the archive with entry-count and uncompressed-size limits (zip
     bomb guard); ignore `__MACOSX/` and dot-files; require exactly one `.shp` and its `.dbf`;
     read `.shp/.shx/.dbf` **from memory** (never extracted, so no path traversal); CRS from
     `.prj` (WKT), text encoding from `.cpg`.
   - **KML / KMZ:** parse with `defusedxml` (blocks entity-expansion attacks); strip
     namespaces so KML 2.2, older Google namespaces and namespace-less files all work; walk
     every `<Placemark>` (including inside nested `<Folder>`s); convert `Point`, `LineString`,
     `LinearRing`, `Polygon` (with holes) and `MultiGeometry` to shapely; collect `name`,
     `description` and `ExtendedData` as properties. KMZ: read `doc.kml` from the archive.
4. **Resolve the CRS.** KML is always EPSG:4326 by specification. A Shapefile without `.prj`
   is assumed to be EPSG:4326 *only* if every coordinate is within lon/lat bounds (with a
   warning); otherwise the CRS stays unknown and measurements report `ERROR`.
5. **Measure** each feature (below) and persist it with its GeoJSON geometry, properties and
   measurement result.
6. Mark the file `COMPLETED`. If the reader raised `InvalidGeospatialFile`, mark it `FAILED`
   with a client-safe error message instead (`422`).

### Measurement calculation flow

For each feature (`services/measurement.py`):

1. No/empty geometry → `EMPTY_GEOMETRY`. Point/MultiPoint → `NOT_APPLICABLE`.
   Anything other than (Multi)Polygon / (Multi)LineString / LinearRing → `UNSUPPORTED_GEOMETRY`.
2. Unknown CRS → `ERROR`.
3. Polygons that are invalid (e.g. a self-intersecting "bow-tie") are repaired with
   `shapely.make_valid` first, and a note is attached. Otherwise the two lobes of a bow-tie
   cancel out and the area comes out near zero.
4. Choose a projected CRS (next section), densify geographic edges, reproject, and take
   shapely's planar `area` / `length`, scaled by the CRS's unit factor to m² / m.

### CRS handling

Degrees aren't a unit of distance: one degree of longitude is ~111 km at the equator and 0 at
the poles. So **geographic** geometries are always projected first, using a projection built
for each feature and centred on its centroid:

| Measurement | Projection | Why |
|---|---|---|
| Area | **Lambert Azimuthal Equal-Area** (`+proj=laea`) | Equal-area by construction, so area is exact on the WGS84 ellipsoid for any location or size. |
| Length | **Transverse Mercator**, central meridian through the centroid, scale 1 (`+proj=tmerc`) | Conformal; scale error is ~0.003% 50 km from the centre. |

Geographic edges are densified to ≤ 0.05° before projecting. A long edge that is straight
in lon/lat (as it's drawn on a map) therefore keeps that shape after projection.

**Projected** input (e.g. EPSG:32643 UTM, or EPSG:2263 in US survey feet) is measured
directly in its own CRS, converting the axis unit to metres.

The CRS used is returned per feature as `measured_in_crs`, so every number can be reproduced.
The tests check projected results against **geodesic** values on the WGS84 ellipsoid
(`pyproj.Geod`), to within 0.001%. Test locations include Bengaluru, Sydney, Seattle, the
Arctic above 84°N, and a 20°×15° polygon.

---

## Design decisions

**FastAPI over Django + DRF.** The service is a handful of JSON endpoints with file upload. FastAPI
gives request validation, typed response models and OpenAPI docs with little code. Django's
admin, auth and ORM migrations aren't needed here, though they would make sense if this grew
into a multi-user product.

**Per-feature equal-area projection instead of one UTM zone.** I first used the UTM zone of each
feature's centroid, which is the textbook answer. Testing against geodesic areas showed a
**0.116% error** on a 1.2 km² block in Bengaluru: 1,201,684 m² vs. 1,200,290 m², about 1,400 m²
on a single block. The cause is UTM's 0.9996 central scale factor and its growth towards zone
edges. UTM also can't handle features wider than a 6° zone or above 84°N. An equal-area
projection centred on the feature has none of these problems.

The alternatives were:
- **Geodesic computation** (`pyproj.Geod`): the most accurate option, but the brief asks for a
  projected CRS. I use it as the reference in the tests instead.
- **A single global equal-area CRS** such as EPSG:6933: fine for areas, but it distorts the
  shapes used for lengths.

**pyshp + defusedxml instead of GeoPandas/Fiona (GDAL).** GDAL is the standard, but it's a large
native dependency that often causes installation problems. Two narrow formats don't need it.
pyshp is pure Python, and KML is XML, so the whole stack installs from wheels on any OS.
Writing the KML parser by hand also let me control namespace handling, `MultiGeometry` typing
and `ExtendedData`. The trade-off is fewer formats. GeoPandas would be the right call if the
service had to accept many formats (see Future scope).

**Synchronous processing.** Uploads are processed within the request, which keeps the API and
the tests simple and is fast for typical survey files (the samples take ~20 ms). The
data model already has a `PROCESSING` status. Moving to a background worker would mean
returning `202` from the upload, letting a worker set `COMPLETED`/`FAILED`, and having clients
poll `GET /api/files/{id}/`. The API contract would not change.

**Measure at upload, store results.** Measurements are computed once and stored, so the
`GET` endpoints are plain database reads and are cheap to paginate. Storing GeoJSON geometry
alongside the results means the features endpoint never re-reads the file.

**SQLite via SQLAlchemy.** It needs zero setup for reviewers. Swapping `GEO_DATABASE_URL` to
PostgreSQL works without code changes. PostGIS would be the next step if spatial queries were
needed.

**Fail per feature, not per file.** A bad geometry in one placemark shouldn't hide 999 good
ones. Unsupported, empty or unmeasurable features get a status and a note, and the rest of the
file is still processed. Only unreadable files fail as a whole.

**Defensive input handling.** The service enforces:
- an upload size cap
- zip entry and uncompressed-size limits
- in-memory reading of archive members (no extraction to disk)
- `defusedxml` to block XML entity attacks
- sanitised stored filenames

Each of these has a test.

---

## Limitations

- One shapefile per `.zip`. Multiple layers are rejected with a clear message rather than
  guessed at.
- Altitude (Z) values are ignored; measurements are 2D.
- Lengths of extremely long lines (thousands of km) carry Transverse Mercator distortion
  (~0.3% at 500 km from the centre). Geodesic length would be the fix (see Future scope).
- Features crossing the antimeridian (±180°) aren't specially handled.
- No authentication or per-user isolation.

## Learnings

- **Check "standard" answers against a reference.** UTM is the usual recommendation, and it
  looked correct until I compared it against geodesic areas. Testing against an independent
  reference (`pyproj.Geod`) instead of hard-coded expected numbers is what exposed the 0.1%
  error.
- **Geometry operations can silently fail on invalid input.** Densifying a self-intersecting
  polygon with `shapely.segmentize` silently turned a bow-tie into a triangle. Validating and
  repairing *before* any transformation fixed it, and only a test with a known answer caught
  the bug.
- **Formats have unwritten rules.** Real shapefile zips contain `__MACOSX/` folders and subfolders,
  and `.prj` files are often missing. KML appears with and without namespaces. Handling these
  mattered more than the core maths.

## Future scope

- **Background processing** (Celery/RQ or arq) for large files, with `202 Accepted`, progress
  reporting and retries.
- **More formats** (GeoJSON, GeoPackage, GML) through a GDAL-backed reader, and **multi-layer**
  archives.
- **Geodesic measurements** as an alternative/extra output (`?method=geodesic`), plus
  perimeter for polygons and unit selection (ha, km², ft²).
- **PostGIS** storage, enabling spatial queries (features within a bounding box, intersections).
- **Object storage** (S3) for uploads, with `DELETE /api/files/{id}/` and retention policies.
- **Auth + rate limiting**, structured logging, and metrics.
- **Antimeridian-aware** handling and per-feature output in the file's original CRS as GeoJSON
  FeatureCollection downloads.
