"""Shared, validated poster configuration for CLI and web adapters."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from outdoor_maps_plot.styles import PROVIDERS, STYLES

Orientation = Literal["landscape", "portrait"]
OutputFormat = Literal["pdf", "png", "jpeg"]
RouteOrder = Literal["auto", "input"]

NAMED_PAPER_SIZES = ("A0", "A1", "A2", "A3", "A4", "A5", "LETTER", "LEGAL", "TABLOID")
CUSTOM_PAPER_PATTERN = re.compile(
    r"(?P<width>\d+(?:\.\d+)?)\s*[X×]\s*(?P<height>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>MM|CM|IN)",
    re.IGNORECASE,
)
HEX_COLOR_PATTERN = re.compile(r"#[0-9A-Fa-f]{6}")


def _ui(
    label: str,
    group: str,
    help_text: str,
    *,
    advanced: bool = False,
    unit: str | None = None,
    choices: tuple[str, ...] = (),
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "label": label,
        "group": group,
        "advanced": advanced,
        "choices": list(choices),
        "help": help_text,
    }
    if unit:
        metadata["unit"] = unit
    return metadata


class PosterConfig(BaseModel):
    """Canonical configuration accepted by every application adapter."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(
        default="My GPX Adventure",
        min_length=1,
        max_length=120,
        description="Poster heading.",
        json_schema_extra=_ui("Title", "content", "Main heading printed above the map."),
    )
    subtitle: str = Field(
        default="",
        max_length=200,
        description="Optional poster subheading.",
        json_schema_extra=_ui(
            "Subtitle", "content", "Optional supporting text printed below the title."
        ),
    )
    paper_size: str = Field(
        default="A3",
        description="Named paper size or custom WIDTHxHEIGHTmm/cm/in.",
        json_schema_extra=_ui(
            "Paper size",
            "page",
            "Choose a standard paper size or enter custom dimensions.",
            choices=NAMED_PAPER_SIZES,
        ),
    )
    orientation: Orientation = Field(
        default="landscape",
        description="Poster page orientation.",
        json_schema_extra=_ui(
            "Orientation",
            "page",
            "Choose a wide or tall poster layout.",
            choices=("landscape", "portrait"),
        ),
    )
    style_name: str = Field(
        default="classic",
        description="Built-in poster appearance preset.",
        json_schema_extra=_ui(
            "Topographic style",
            "appearance",
            "Select the map treatment and poster color palette.",
            choices=tuple(STYLES),
        ),
    )
    provider: str | None = Field(
        default=None,
        description="Optional map-provider override.",
        json_schema_extra=_ui(
            "Map provider",
            "appearance",
            "Override the provider selected by the topographic style.",
            advanced=True,
            choices=PROVIDERS,
        ),
    )
    zoom: int = Field(
        default=10,
        ge=0,
        le=19,
        description="Slippy-map zoom level.",
        json_schema_extra=_ui(
            "Map zoom",
            "map",
            "Higher zoom shows more detail but may require many more tiles.",
            advanced=True,
        ),
    )
    padding_percent: float = Field(
        default=6.0,
        ge=0,
        le=50,
        description="Map padding around every route.",
        json_schema_extra=_ui(
            "Route padding",
            "map",
            "Add space between the route and the map edges.",
            unit="percent",
        ),
    )
    margin_mm: float = Field(
        default=14.8,
        ge=1,
        le=50,
        description="Page margin.",
        json_schema_extra=_ui(
            "Page margin",
            "page",
            "Set the clear border between poster content and the page edge.",
            advanced=True,
            unit="mm",
        ),
    )
    basemap_width: int = Field(
        default=2400,
        ge=512,
        le=10_000,
        description="Minimum embedded basemap width.",
        json_schema_extra=_ui(
            "Basemap width",
            "map",
            "Set the minimum map image width used during rendering.",
            advanced=True,
            unit="pixels",
        ),
    )
    max_tiles: int = Field(
        default=200,
        ge=1,
        le=500,
        description="Maximum map tiles allowed for one render.",
        json_schema_extra=_ui(
            "Tile limit",
            "map",
            "Stop a render before it requests more than this number of tiles.",
            advanced=True,
        ),
    )
    simplify_points: float = Field(
        default=0.35,
        ge=0,
        le=10,
        description="Visual line-simplification tolerance.",
        json_schema_extra=_ui(
            "Line simplification",
            "route",
            "Reduce route detail for smaller output; zero preserves every point.",
            advanced=True,
            unit="points",
        ),
    )
    route_width: float = Field(
        default=3.5,
        ge=0.5,
        le=20,
        description="Route stroke width at A3 scale.",
        json_schema_extra=_ui(
            "Route width",
            "route",
            "Set the route line thickness at A3 scale.",
            unit="points",
        ),
    )
    route_color: str | None = Field(
        default=None,
        description="Optional route color override as a six-digit hexadecimal color.",
        json_schema_extra=_ui(
            "Track color",
            "route",
            "Override the track color supplied by the selected topographic style.",
        ),
    )
    route_order: RouteOrder = Field(
        default="auto",
        description="Automatic endpoint ordering or input order.",
        json_schema_extra=_ui(
            "Route order",
            "route",
            "Join nearby stage endpoints automatically or preserve upload order.",
            choices=("auto", "input"),
        ),
    )
    output_format: OutputFormat = Field(
        default="pdf",
        description="Final artifact format.",
        json_schema_extra=_ui(
            "Output format",
            "output",
            "Choose a print-ready PDF or a raster image.",
            choices=("pdf", "png", "jpeg"),
        ),
    )
    dpi: int = Field(
        default=300,
        ge=72,
        le=1200,
        description="PNG or JPEG raster resolution.",
        json_schema_extra=_ui(
            "Raster resolution",
            "output",
            "Set the resolution used for PNG and JPEG output.",
            unit="dpi",
        ),
    )
    jpeg_quality: int = Field(
        default=92,
        ge=1,
        le=100,
        description="JPEG encoder quality.",
        json_schema_extra=_ui(
            "JPEG quality",
            "output",
            "Balance JPEG detail against output file size.",
            advanced=True,
            unit="percent",
        ),
    )

    @field_validator("paper_size")
    @classmethod
    def validate_paper_size(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized in NAMED_PAPER_SIZES:
            return normalized
        match = CUSTOM_PAPER_PATTERN.fullmatch(normalized)
        if not match:
            raise ValueError("use A0-A5, Letter, Legal, Tabloid, or custom WIDTHxHEIGHTmm/cm/in")
        if float(match.group("width")) <= 0 or float(match.group("height")) <= 0:
            raise ValueError("custom paper dimensions must be greater than zero")
        return normalized

    @field_validator("style_name")
    @classmethod
    def validate_style(cls, value: str) -> str:
        if value not in STYLES:
            raise ValueError(f"unknown style: {value}")
        return value

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str | None) -> str | None:
        if value is not None and value not in PROVIDERS:
            raise ValueError(f"unknown tile provider: {value}")
        return value

    @field_validator("route_color")
    @classmethod
    def validate_route_color(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not HEX_COLOR_PATTERN.fullmatch(value):
            raise ValueError("use a six-digit hexadecimal color such as #E4431B")
        return value.upper()

    @model_validator(mode="after")
    def validate_provider_zoom(self) -> PosterConfig:
        provider = self.provider or STYLES[self.style_name].provider
        if provider == "opentopo" and self.zoom > 17:
            raise ValueError("OpenTopoMap supports zoom levels up to 17")
        return self

    @property
    def effective_provider(self) -> str:
        return self.provider or STYLES[self.style_name].provider

    @property
    def effective_route_color(self) -> str:
        return self.route_color or STYLES[self.style_name].route
