"""Application-level poster rendering contract."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from outdoor_maps_plot.gpx import Route
from outdoor_maps_plot.options import PosterConfig
from outdoor_maps_plot.poster import create_poster

RenderPhase = Literal[
    "validating",
    "parsing",
    "fetching_map",
    "fetching_elevation",
    "fetching_water",
    "drawing",
    "building_mesh",
    "validating_mesh",
    "packaging",
    "rasterizing",
    "finalizing",
]


@dataclass(frozen=True)
class ProgressEvent:
    """Stable progress information safe to expose through adapters."""

    phase: RenderPhase
    percent: int
    message: str
    updated_at: datetime


class ProgressReporter(Protocol):
    def __call__(self, event: ProgressEvent) -> None: ...


class RenderCancelled(RuntimeError):
    """Raised when cooperative cancellation stops a render."""


class CancellationToken:
    """Thread-safe cooperative cancellation flag."""

    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise RenderCancelled("Render cancelled")


@dataclass(frozen=True)
class RenderResult:
    path: Path
    output_format: str
    media_type: str
    size_bytes: int
    warnings: tuple[str, ...] = ()


MEDIA_TYPES = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpeg": "image/jpeg",
}


def _report(
    reporter: ProgressReporter | None,
    phase: RenderPhase,
    percent: int,
    message: str,
) -> None:
    if reporter is not None:
        reporter(
            ProgressEvent(
                phase=phase,
                percent=percent,
                message=message,
                updated_at=datetime.now(UTC),
            )
        )


def render_poster(
    routes: list[Route],
    destination: Path,
    cache: Path,
    config: PosterConfig,
    progress: ProgressReporter | None = None,
    cancellation: CancellationToken | None = None,
) -> RenderResult:
    """Render one poster with stable progress and cooperative cancellation."""
    token = cancellation or CancellationToken()
    token.raise_if_cancelled()
    _report(progress, "validating", 2, "Validating poster configuration")
    config = PosterConfig.model_validate(config)
    if not routes:
        raise ValueError("At least one usable route is required")
    token.raise_if_cancelled()
    _report(progress, "validating", 5, "Poster configuration ready")

    def report_renderer_phase(
        phase: Literal["fetching_map", "drawing", "rasterizing"],
        percent: int,
        message: str,
    ) -> None:
        token.raise_if_cancelled()
        _report(progress, phase, percent, message)

    create_poster(
        routes,
        destination,
        config.output_format,
        cache,
        config,
        progress=report_renderer_phase,
        cancellation_check=token.raise_if_cancelled,
    )
    token.raise_if_cancelled()
    _report(progress, "finalizing", 96, "Finalizing poster")
    result = RenderResult(
        path=destination,
        output_format=config.output_format,
        media_type=MEDIA_TYPES[config.output_format],
        size_bytes=destination.stat().st_size,
    )
    _report(progress, "finalizing", 100, "Poster ready")
    return result
