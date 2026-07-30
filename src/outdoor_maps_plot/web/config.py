"""Validated, environment-backed settings for the web application."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

HARD_MAX_FILES = 15
MIB = 1024 * 1024


def _default_job_root() -> Path:
    return (
        Path("/tmp/poster-jobs") if os.name != "nt" else Path(tempfile.gettempdir()) / "poster-jobs"
    )


def _default_cache_root() -> Path:
    if os.name != "nt":
        return Path("/var/cache/outdoor-maps-plot")
    return Path(tempfile.gettempdir()) / "outdoor-maps-plot-cache"


class WebSettings(BaseModel):
    """Server-owned limits and storage locations.

    Settings are deliberately independent of FastAPI so they can be created in
    tests and container startup code without application side effects.
    """

    model_config = ConfigDict(extra="forbid")

    job_root: Path = Field(default_factory=_default_job_root)
    cache_root: Path = Field(default_factory=_default_cache_root)
    job_ttl_seconds: int = Field(default=3600, ge=60, le=7 * 24 * 3600)
    max_files: int = Field(default=HARD_MAX_FILES, ge=1, le=HARD_MAX_FILES)
    max_file_bytes: int = Field(default=25 * MIB, ge=1024)
    max_upload_bytes: int = Field(default=100 * MIB, ge=1024)
    max_points_total: int = Field(default=1_000_000, ge=2)
    max_queued_jobs: int = Field(default=10, ge=1, le=1000)
    max_concurrent_jobs: int = Field(default=2, ge=1, le=32)
    max_tiles: int = Field(default=500, ge=1, le=500)
    cleanup_interval_seconds: float = Field(default=30.0, ge=0.1, le=3600)

    @field_validator("job_root", "cache_root", mode="before")
    @classmethod
    def expand_path(cls, value: object) -> Path:
        return Path(str(value)).expanduser()

    @model_validator(mode="after")
    def validate_upload_limits(self) -> WebSettings:
        if self.max_upload_bytes < self.max_file_bytes:
            raise ValueError("max_upload_bytes must be at least max_file_bytes")
        return self

    @classmethod
    def from_env(cls) -> WebSettings:
        names: dict[str, tuple[str, type[int] | type[float] | type[Path]]] = {
            "job_root": ("OMP_JOB_ROOT", Path),
            "cache_root": ("OMP_CACHE_ROOT", Path),
            "job_ttl_seconds": ("OMP_JOB_TTL_SECONDS", int),
            "max_files": ("OMP_MAX_FILES", int),
            "max_file_bytes": ("OMP_MAX_FILE_BYTES", int),
            "max_upload_bytes": ("OMP_MAX_UPLOAD_BYTES", int),
            "max_points_total": ("OMP_MAX_POINTS", int),
            "max_queued_jobs": ("OMP_MAX_QUEUED_JOBS", int),
            "max_concurrent_jobs": ("OMP_MAX_CONCURRENT_JOBS", int),
            "max_tiles": ("OMP_MAX_TILES", int),
            "cleanup_interval_seconds": ("OMP_CLEANUP_INTERVAL_SECONDS", float),
        }
        values: dict[str, object] = {}
        for field_name, (environment_name, converter) in names.items():
            raw = os.getenv(environment_name)
            if raw is not None:
                values[field_name] = converter(raw)
        return cls.model_validate(values)

    @property
    def provider_credentials(self) -> dict[str, bool]:
        return {
            "opentopo": True,
            "esri": True,
            "stadia": bool(os.getenv("STADIA_MAPS_API_KEY")),
            "thunderforest": bool(os.getenv("THUNDERFOREST_API_KEY")),
        }
