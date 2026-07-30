"""Public request and response schemas for the web API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from outdoor_maps_plot.options import PosterConfig

RenderMode = Literal["preview", "final"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "expired"]


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[Any] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorBody


class FileSummary(BaseModel):
    display_name: str
    size_bytes: int
    route_count: int


class RouteSummary(BaseModel):
    name: str
    point_count: int
    distance_km: float
    ascent_m: float


class UploadTotals(BaseModel):
    route_count: int
    point_count: int
    distance_km: float
    ascent_m: float


class UploadResponse(BaseModel):
    upload_id: str
    files: list[FileSummary]
    summary: UploadTotals
    routes: list[RouteSummary]
    expires_at: datetime


class RenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: str = Field(min_length=16, max_length=128)
    mode: RenderMode = "final"
    config: PosterConfig = Field(default_factory=PosterConfig)


class RenderAccepted(BaseModel):
    job_id: str
    status: JobStatus
    status_url: str
    events_url: str


class ProgressResponse(BaseModel):
    phase: str
    percent: int
    message: str
    updated_at: datetime


class ArtifactResponse(BaseModel):
    filename: str
    media_type: str
    size_bytes: int
    download_url: str


class RenderStatusResponse(BaseModel):
    job_id: str
    upload_id: str
    mode: RenderMode
    status: JobStatus
    progress: ProgressResponse
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    expires_at: datetime
    artifact: ArtifactResponse | None = None
    error: ErrorBody | None = None


class DeleteResponse(BaseModel):
    id: str
    status: Literal["deleted", "cancellation_requested"]


class HealthResponse(BaseModel):
    status: Literal["ok", "ready", "not_ready"]
