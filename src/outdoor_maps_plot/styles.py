"""Built-in poster appearance presets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Style:
    label: str
    paper: str
    ink: str
    muted: str
    route: str
    halo: str
    provider: str


STYLES = {
    "classic": Style(
        "Classic Topographic", "#F2EFE6", "#17332B", "#66776F", "#E4431B", "#FFFDFB", "opentopo"
    ),
    "muted-alpine": Style(
        "Muted Alpine", "#F3F0E8", "#19372F", "#68776F", "#ED5B2A", "#FFFDFB", "opentopo"
    ),
    "monochrome-relief": Style(
        "Monochrome Relief", "#F4F2EC", "#202724", "#717570", "#E44A24", "#FFFEFA", "opentopo"
    ),
    "vintage-expedition": Style(
        "Vintage Expedition", "#E9DDC2", "#273D31", "#786C56", "#B8422D", "#F8EED8", "opentopo"
    ),
    "cool-minimal": Style(
        "Cool Minimal", "#E8EFED", "#17384A", "#68808A", "#153F63", "#F7FAF9", "opentopo"
    ),
    "dark-topographic": Style(
        "Dark Topographic", "#101918", "#F1EEE4", "#A8B5AE", "#FF6338", "#EDEBE3", "opentopo"
    ),
    "high-contrast-hiking": Style(
        "High-Contrast Hiking", "#EFF1E8", "#153A2B", "#5C7467", "#E22E1B", "#FFFFFF", "opentopo"
    ),
    "esri-topographic": Style(
        "Esri World Topographic", "#F0F1EE", "#213B42", "#6C7C7D", "#E84B22", "#FFFFFF", "esri"
    ),
    "stamen-terrain": Style(
        "Stamen Terrain", "#F1EEE5", "#263A32", "#6C776F", "#E34B25", "#FFFFFF", "stadia"
    ),
    "thunderforest-outdoors": Style(
        "Thunderforest Outdoors",
        "#EEF1E8",
        "#173A2B",
        "#617469",
        "#E43F20",
        "#FFFFFF",
        "thunderforest",
    ),
}

PROVIDERS = ("opentopo", "esri", "stadia", "thunderforest")

ATTRIBUTIONS = {
    "esri": "MAP: © ESRI AND ITS DATA SUPPLIERS  /  ROUTE: USER-PROVIDED TRACK",
    "stadia": (
        "© STADIA MAPS  /  © STAMEN DESIGN  /  © OPENMAPTILES  /  © OPENSTREETMAP CONTRIBUTORS"
    ),
    "thunderforest": "MAP: © THUNDERFOREST  /  DATA: © OPENSTREETMAP CONTRIBUTORS",
    "opentopo": ("MAP DATA: © OPENSTREETMAP CONTRIBUTORS, SRTM  /  MAP: © OPENTOPOMAP (CC-BY-SA)"),
}
