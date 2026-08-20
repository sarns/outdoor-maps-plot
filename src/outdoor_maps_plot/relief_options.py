"""Validated configuration for printable 3D relief models."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

HEX_COLOR_PATTERN = re.compile(r"#[0-9A-Fa-f]{6}")


class ReliefConfig(BaseModel):
    """Configuration shared by relief geometry and export adapters.

    The X/Y limits deliberately match the requested 256 mm build plate.  A
    printer-specific margin is left to the caller; the safer defaults are
    240 mm in both directions.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    width_mm: float = Field(default=240.0, gt=0, le=256)
    depth_mm: float = Field(default=240.0, gt=0, le=256)
    base_thickness_mm: float = Field(default=2.4, gt=0, le=50)
    relief_height_mm: float = Field(default=18.0, gt=0, le=200)
    track_width_mm: float = Field(default=1.6, gt=0, le=25)
    track_height_mm: float = Field(default=0.8, gt=0, le=25)
    mesh_pitch_mm: float = Field(default=0.8, gt=0, le=10)
    padding_percent: float = Field(default=6.0, ge=0, le=50)

    low_color: str = "#4D6B50"
    mid_color: str = "#B88A4A"
    high_color: str = "#E8E0CA"
    track_color: str = "#E4431B"
    output_format: Literal["3mf"] = "3mf"

    @field_validator("low_color", "mid_color", "high_color", "track_color")
    @classmethod
    def validate_color(cls, value: str) -> str:
        if not HEX_COLOR_PATTERN.fullmatch(value):
            raise ValueError("use a six-digit hexadecimal color such as #E4431B")
        return value.upper()

    @model_validator(mode="after")
    def validate_distinct_colors(self) -> ReliefConfig:
        if len(set(self.colors)) != 4:
            raise ValueError("low, mid, high, and track colors must be distinct")
        return self

    @property
    def colors(self) -> tuple[str, str, str, str]:
        """Return the four materials in terrain-low to track order."""

        return self.low_color, self.mid_color, self.high_color, self.track_color
