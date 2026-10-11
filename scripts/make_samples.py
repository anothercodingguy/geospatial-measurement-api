"""Generate the sample files in samples/ (run: uv run python scripts/make_samples.py)."""

import io
import zipfile
from pathlib import Path

import shapefile
from pyproj import CRS
from pyproj.enums import WktVersion

OUT = Path(__file__).resolve().parent.parent / "samples"

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Cubbon Park survey</name>
    <Placemark id="park-boundary">
      <name>Survey block</name>
      <ExtendedData><Data name="surveyor"><value>Team A</value></Data></ExtendedData>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        77.5900,12.9700,0 77.6000,12.9700,0 77.6000,12.9800,0 77.5900,12.9800,0 77.5900,12.9700,0
      </coordinates></LinearRing></outerBoundaryIs>
      <innerBoundaryIs><LinearRing><coordinates>
        77.5920,12.9720,0 77.5940,12.9720,0 77.5940,12.9740,0 77.5920,12.9740,0 77.5920,12.9720,0
      </coordinates></LinearRing></innerBoundaryIs></Polygon>
    </Placemark>
    <Placemark id="access-road">
      <name>Access road</name>
      <LineString><coordinates>
        77.5900,12.9700 77.6100,12.9900 77.6300,12.9900
      </coordinates></LineString>
    </Placemark>
    <Placemark id="control-point">
      <name>Ground control point 1</name>
      <Point><coordinates>77.5950,12.9750,920</coordinates></Point>
    </Placemark>
  </Document>
</kml>
"""


def parcels_zip() -> bytes:
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    with shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON) as w:
        w.field("parcel_id", "C", 20)
        w.field("owner", "C", 50)
        w.field("land_use", "C", 20)
        parcels = [
            ("P-001", "Ravi Kumar", "agriculture", 77.70, 13.10),
            ("P-002", "Anita Rao", "residential", 77.71, 13.10),
            ("P-003", "Village Panchayat", "public", 77.70, 13.11),
        ]
        for pid, owner, use, x, y in parcels:
            w.poly([[(x, y), (x + 0.008, y), (x + 0.008, y + 0.006), (x, y + 0.006), (x, y)]])
            w.record(pid, owner, use)
    files = {
        "parcels.shp": shp.getvalue(),
        "parcels.shx": shx.getvalue(),
        "parcels.dbf": dbf.getvalue(),
        "parcels.prj": CRS.from_epsg(4326).to_wkt(WktVersion.WKT1_ESRI).encode(),
        "parcels.cpg": b"UTF-8",
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return out.getvalue()


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    (OUT / "survey.kml").write_text(KML)
    (OUT / "parcels_shapefile.zip").write_bytes(parcels_zip())
    print(f"Wrote samples to {OUT}")
