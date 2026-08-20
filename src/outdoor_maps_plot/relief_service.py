"""Application-level orchestration for four-color printable reliefs."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from outdoor_maps_plot.elevation import (
    ElevationCancelled,
    ElevationError,
    ElevationProvider,
    ElevationRequest,
    TerrariumElevationProvider,
)
from outdoor_maps_plot.export_3mf import write_3mf
from outdoor_maps_plot.gpx import Route
from outdoor_maps_plot.mesh_validation import MeshValidationError
from outdoor_maps_plot.poster import simplify_line
from outdoor_maps_plot.projection import prepare_model_route
from outdoor_maps_plot.relief import build_relief_model
from outdoor_maps_plot.relief_options import ReliefConfig
from outdoor_maps_plot.service import (
    CancellationToken,
    ProgressEvent,
    ProgressReporter,
    RenderCancelled,
    RenderResult,
)
from outdoor_maps_plot.water import (
    OpenStreetMapWaterProvider,
    WaterCancelled,
    WaterError,
    WaterProvider,
)


class ReliefError(ValueError):
    """Raised when a requested relief cannot be generated safely."""


def _simplify_routes(
    segments: tuple[tuple[tuple[float, float], ...], ...],
    config: ReliefConfig,
    token: CancellationToken,
) -> list[list[tuple[float, float]]]:
    """Bound track mesh complexity at a tolerance below the printed line width."""

    tolerance = min(config.track_width_mm / 4, config.mesh_pitch_mm / 2)
    simplified: list[list[tuple[float, float]]] = []
    for segment in segments:
        token.raise_if_cancelled()
        radial: list[tuple[float, float]] = []
        for point in segment:
            if not radial or len(radial) == 1 or math.dist(radial[-1], point) >= tolerance / 2:
                radial.append(point)
        if radial and radial[-1] != segment[-1]:
            radial.append(segment[-1])
        if len(radial) >= 2:
            simplified.append(simplify_line(radial, tolerance, token.raise_if_cancelled))
    return simplified


def _report(
    reporter: ProgressReporter | None,
    phase: str,
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


def render_relief(
    routes: list[Route],
    destination: Path,
    cache: Path,
    config: ReliefConfig,
    progress: ProgressReporter | None = None,
    cancellation: CancellationToken | None = None,
    *,
    elevation_provider: ElevationProvider | None = None,
    water_provider: WaterProvider | None = None,
) -> RenderResult:
    """Fetch elevation data and create one validated, four-part 3MF model."""

    token = cancellation or CancellationToken()
    try:
        token.raise_if_cancelled()
        _report(progress, "validating", 2, "Validating 3D relief configuration")
        config = ReliefConfig.model_validate(config)
        if not routes:
            raise ReliefError("At least one usable route is required")

        segments = [segment for route in routes for segment in route.segments]
        model_route = prepare_model_route(
            segments,
            width_mm=config.width_mm,
            depth_mm=config.depth_mm,
            padding_fraction=config.padding_percent / 100,
        )
        token.raise_if_cancelled()
        _report(progress, "parsing", 10, "Preparing routes")

        request = ElevationRequest.for_model(
            model_route.projection,
            model_route.terrain_bounds_m,
            width_mm=config.width_mm,
            depth_mm=config.depth_mm,
            mesh_pitch_mm=config.mesh_pitch_mm,
        )
        provider = elevation_provider or TerrariumElevationProvider(cache_dir=cache / "elevation")
        _report(progress, "fetching_elevation", 15, "Preparing elevation data")
        elevation = provider.load(request, cancelled=lambda: token.cancelled)
        token.raise_if_cancelled()
        _report(progress, "fetching_elevation", 50, "Elevation data ready")

        mapped_water_provider = water_provider or OpenStreetMapWaterProvider(
            cache_dir=cache / "water"
        )
        _report(progress, "fetching_water", 52, "Loading lakes and rivers")
        water = mapped_water_provider.load(
            model_route.projection,
            model_route.terrain_bounds_m,
            width_mm=config.width_mm,
            depth_mm=config.depth_mm,
            minimum_line_width_mm=config.waterway_width_mm,
            cancelled=lambda: token.cancelled,
        )
        token.raise_if_cancelled()
        _report(progress, "fetching_water", 58, "Water geometry ready")

        _report(progress, "building_mesh", 60, "Building printable terrain")
        route_lines = _simplify_routes(model_route.segments_mm, config, token)
        model = build_relief_model(elevation.values_m, route_lines, config, water)
        token.raise_if_cancelled()
        _report(progress, "validating_mesh", 88, "Checking printable geometry")

        _report(progress, "packaging", 92, "Packaging 3MF model")
        attribution = "; ".join(
            (
                getattr(provider, "attribution", provider.cache_identity),
                getattr(
                    mapped_water_provider,
                    "attribution",
                    mapped_water_provider.cache_identity,
                ),
            )
        )
        write_3mf(model, destination, config, elevation_attribution=attribution)
        token.raise_if_cancelled()
        result = RenderResult(
            path=destination,
            output_format="3mf",
            media_type="model/3mf",
            size_bytes=destination.stat().st_size,
        )
        _report(progress, "finalizing", 100, "3D relief ready")
        return result
    except ElevationCancelled as exc:
        raise RenderCancelled("Render cancelled") from exc
    except WaterCancelled as exc:
        raise RenderCancelled("Render cancelled") from exc
    except RenderCancelled:
        destination.unlink(missing_ok=True)
        raise
    except ReliefError:
        destination.unlink(missing_ok=True)
        raise
    except (
        ElevationError,
        WaterError,
        MeshValidationError,
        ValidationError,
        OSError,
        ValueError,
    ) as exc:
        destination.unlink(missing_ok=True)
        raise ReliefError(str(exc)) from exc
