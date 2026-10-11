from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models import FileStatus, MeasurementStatus


class FileInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: str
    feature_count: int
    crs: str | None
    crs_name: str | None
    status: FileStatus
    error: str | None = None
    warnings: list[str] = []
    geometry_types: dict[str, int] = {}
    created_at: datetime
    processed_at: datetime | None


class FileList(BaseModel):
    count: int
    limit: int
    offset: int
    results: list[FileInfo]


class FeatureOut(BaseModel):
    feature_id: int
    source_id: str | None
    geometry_type: str | None
    crs: str | None
    geometry: dict[str, Any] | None  # GeoJSON geometry in the file's CRS
    properties: dict[str, Any]


class FeatureList(BaseModel):
    file_id: str
    count: int
    limit: int
    offset: int
    results: list[FeatureOut]


class MeasurementOut(BaseModel):
    feature_id: int
    source_id: str | None
    geometry_type: str | None
    status: MeasurementStatus
    measurement: str | None  # "area" | "length"
    value: float | None
    unit: str | None  # "m2" | "m"
    measured_in_crs: str | None
    note: str | None


class MeasurementSummary(BaseModel):
    total_area_m2: float
    total_length_m: float
    by_status: dict[str, int]


class MeasurementList(BaseModel):
    file_id: str
    crs: str | None
    summary: MeasurementSummary
    count: int
    limit: int
    offset: int
    results: list[MeasurementOut]
