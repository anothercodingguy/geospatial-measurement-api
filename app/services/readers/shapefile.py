"""Shapefile reader for .zip uploads, built on pyshp (pure Python, no GDAL needed).

A shapefile is several sibling files sharing one name: .shp (geometry), .dbf (attributes),
.shx (index), and optionally .prj (CRS as WKT) and .cpg (attribute text encoding).
"""

import datetime as dt
import io
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

import shapefile
from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely.geometry import shape

from app.services.readers.archive import data_members, open_zip
from app.services.readers.base import InvalidGeospatialFile, ParsedDataset, RawFeature


def read_shapefile_zip(path: Path, max_uncompressed_bytes: int) -> ParsedDataset:
    with open_zip(path, max_uncompressed_bytes) as archive:
        members = {m.filename: m for m in data_members(archive)}

        shp_names = [name for name in members if name.lower().endswith(".shp")]
        if not shp_names:
            raise InvalidGeospatialFile("Zip archive does not contain a .shp file.")
        if len(shp_names) > 1:
            raise InvalidGeospatialFile(
                "Zip archive contains more than one shapefile "
                f"({', '.join(sorted(shp_names))}); upload one shapefile per zip."
            )

        stem = str(PurePosixPath(shp_names[0]).with_suffix(""))
        siblings = {
            PurePosixPath(name).suffix.lower(): name
            for name in members
            if str(PurePosixPath(name).with_suffix("")).lower() == stem.lower()
        }
        if ".dbf" not in siblings:
            raise InvalidGeospatialFile(f"Shapefile '{shp_names[0]}' is missing its .dbf file.")

        def read(ext: str) -> bytes | None:
            return archive.read(members[siblings[ext]]) if ext in siblings else None

        shp, shx, dbf, prj, cpg = (read(e) for e in (".shp", ".shx", ".dbf", ".prj", ".cpg"))

    warnings: list[str] = []
    crs = _parse_prj(prj, warnings)
    encoding = _encoding(cpg)

    try:
        reader = shapefile.Reader(
            shp=io.BytesIO(shp),
            shx=io.BytesIO(shx) if shx else None,
            dbf=io.BytesIO(dbf),
            encoding=encoding,
            encodingErrors="replace",
        )
        features = [
            RawFeature(
                index=index,
                source_id=str(item.record.oid),
                geometry=None if item.shape.shapeType == shapefile.NULL else shape(item.shape),
                properties={k: _json_safe(v) for k, v in item.record.as_dict().items()},
            )
            for index, item in enumerate(reader.iterShapeRecords())
        ]
    except (shapefile.ShapefileException, ValueError, IndexError, OSError) as exc:
        raise InvalidGeospatialFile(f"Could not read shapefile: {exc}") from exc

    if shx is None:
        warnings.append("Shapefile has no .shx index file; it was read without it.")
    return ParsedDataset(crs=crs, features=features, warnings=warnings)


def _parse_prj(prj: bytes | None, warnings: list[str]) -> CRS | None:
    if prj is None:
        return None
    try:
        return CRS.from_wkt(prj.decode("utf-8", errors="replace"))
    except CRSError:
        warnings.append("The .prj file could not be parsed; the CRS is treated as unknown.")
        return None


def _encoding(cpg: bytes | None) -> str:
    if cpg:
        name = cpg.decode("ascii", errors="ignore").strip()
        try:
            "".encode(name)
            return name
        except LookupError:
            pass
    return "utf-8"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
