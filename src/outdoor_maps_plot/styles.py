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
    route_palette: tuple[str, ...]


STYLES = {
    "classic": Style(
        "Classic Topographic",
        "#F2EFE6",
        "#17332B",
        "#66776F",
        "#E4431B",
        "#FFFDFB",
        "opentopo",
        ("#E4431B", "#147D92", "#C18A17", "#76549A", "#2F7A4F"),
    ),
    "muted-alpine": Style(
        "Muted Alpine",
        "#F3F0E8",
        "#19372F",
        "#68776F",
        "#ED5B2A",
        "#FFFDFB",
        "opentopo",
        ("#ED5B2A", "#397C78", "#B98A3D", "#7B648C", "#557A45"),
    ),
    "monochrome-relief": Style(
        "Monochrome Relief",
        "#F4F2EC",
        "#202724",
        "#717570",
        "#E44A24",
        "#FFFEFA",
        "opentopo",
        ("#E44A24", "#167D9A", "#C68B00", "#6B58A6", "#23845C"),
    ),
    "vintage-expedition": Style(
        "Vintage Expedition",
        "#E9DDC2",
        "#273D31",
        "#786C56",
        "#B8422D",
        "#F8EED8",
        "opentopo",
        ("#B8422D", "#A57C24", "#35634C", "#4F5D75", "#7A4E62"),
    ),
    "cool-minimal": Style(
        "Cool Minimal",
        "#E8EFED",
        "#17384A",
        "#68808A",
        "#153F63",
        "#F7FAF9",
        "opentopo",
        ("#153F63", "#247B78", "#B65C3A", "#6B5B95", "#4F772D"),
    ),
    "dark-topographic": Style(
        "Dark Topographic",
        "#101918",
        "#F1EEE4",
        "#A8B5AE",
        "#FF6338",
        "#EDEBE3",
        "opentopo",
        ("#FF6338", "#3FC7D3", "#F2C94C", "#B692F6", "#5DD39E"),
    ),
    "high-contrast-hiking": Style(
        "High-Contrast Hiking",
        "#EFF1E8",
        "#153A2B",
        "#5C7467",
        "#E22E1B",
        "#FFFFFF",
        "opentopo",
        ("#E22E1B", "#005F99", "#247A37", "#6F42A8", "#A86400"),
    ),
    "esri-topographic": Style(
        "Esri World Topographic",
        "#F0F1EE",
        "#213B42",
        "#6C7C7D",
        "#E84B22",
        "#FFFFFF",
        "esri",
        ("#E84B22", "#176B87", "#2F7D55", "#7251A2", "#B27A00"),
    ),
    "stamen-terrain": Style(
        "Stamen Terrain",
        "#F1EEE5",
        "#263A32",
        "#6C776F",
        "#E34B25",
        "#FFFFFF",
        "stadia",
        ("#E34B25", "#17758A", "#347B4D", "#73549B", "#AD7900"),
    ),
    "thunderforest-outdoors": Style(
        "Thunderforest Outdoors",
        "#EEF1E8",
        "#173A2B",
        "#617469",
        "#E43F20",
        "#FFFFFF",
        "thunderforest",
        ("#E43F20", "#116F8A", "#287A48", "#6D4F9C", "#B47500"),
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
