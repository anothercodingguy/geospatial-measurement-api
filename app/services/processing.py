"""Upload pipeline: store the raw file, parse it, measure every feature, persist results."""

import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Feature, FileStatus, MeasurementStatus, UploadedFile
from app.services.crs import WGS84, crs_label, looks_like_lon_lat
from app.services.measurement import Measurement, measure
from app.services.readers.base import InvalidGeospatialFile, ParsedDataset
from app.services.readers.kml import read_kml, read_kmz
from app.services.readers.shapefile import read_shapefile_zip

SUPPORTED_EXTENSIONS = {".zip": "shapefile", ".kml": "kml", ".kmz": "kmz"}


class UnsupportedFileType(Exception):
    pass


def detect_file_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        allowed = ", ".join(SUPPORTED_EXTENSIONS)
        raise UnsupportedFileType(
            f"Unsupported file type '{suffix or filename}'. Use one of: {allowed}."
        )
    return SUPPORTED_EXTENSIONS[suffix]


def process_upload(
    session: Session, settings: Settings, filename: str, data: bytes
) -> UploadedFile:
    file_type = detect_file_type(filename)
    record = UploadedFile(filename=filename, file_type=file_type, status=FileStatus.PROCESSING)
    session.add(record)
    session.flush()  # assigns record.id

    path = _store(settings.upload_dir, record.id, filename, data)
    try:
        dataset = _read(file_type, path, settings)
        _apply(record, dataset)
    except InvalidGeospatialFile as exc:
        record.status = FileStatus.FAILED
        record.error = str(exc)
    record.processed_at = datetime.now(UTC)
    session.commit()
    return record


def _store(upload_dir: Path, file_id: str, filename: str, data: bytes) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name) or "upload"
    target_dir = upload_dir / file_id
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / safe_name
    path.write_bytes(data)
    return path


def _read(file_type: str, path: Path, settings: Settings) -> ParsedDataset:
    if file_type == "kml":
        return read_kml(path)
    if file_type == "kmz":
        return read_kmz(path, settings.max_uncompressed_bytes)
    return read_shapefile_zip(path, settings.max_uncompressed_bytes)


def _apply(record: UploadedFile, dataset: ParsedDataset) -> None:
    warnings = list(dataset.warnings)
    crs = dataset.crs
    if crs is None and looks_like_lon_lat([f.geometry for f in dataset.features]):
        crs = WGS84
        warnings.append(
            "No CRS was provided (missing .prj). Coordinates are within longitude/latitude "
            "bounds, so EPSG:4326 was assumed."
        )
    elif crs is None and dataset.features:
        warnings.append("No CRS was provided (missing .prj); features cannot be measured.")

    for raw in dataset.features:
        if raw.unsupported_type:
            geometry_type, geojson = raw.unsupported_type, None
            result = Measurement(
                MeasurementStatus.UNSUPPORTED_GEOMETRY,
                note=f"KML <{raw.unsupported_type}> geometries are not supported.",
            )
        else:
            geometry_type = raw.geometry.geom_type if raw.geometry is not None else None
            geojson = raw.geometry.__geo_interface__ if raw.geometry is not None else None
            result = measure(raw.geometry, crs)
        record.features.append(
            Feature(
                index=raw.index,
                source_id=raw.source_id,
                geometry_type=geometry_type,
                geometry=_to_json(geojson),
                properties=raw.properties,
                measurement_type=result.type,
                measurement_value=result.value,
                measurement_unit=result.unit,
                measurement_crs=result.crs,
                measurement_status=result.status,
                measurement_note=result.note,
            )
        )

    record.crs = crs_label(crs) if crs else None
    record.crs_name = crs.name if crs else None
    record.feature_count = len(dataset.features)
    record.warnings = warnings
    record.status = FileStatus.COMPLETED


def _to_json(value):
    """shapely's __geo_interface__ uses tuples; JSON columns round-trip lists more predictably."""
    if isinstance(value, dict):
        return {k: _to_json(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_to_json(v) for v in value]
    return value
