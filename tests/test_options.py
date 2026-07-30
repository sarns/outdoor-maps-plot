import pytest
from pydantic import ValidationError

from outdoor_maps_plot.options import PosterConfig


def test_poster_config_normalizes_custom_paper_and_exposes_ui_metadata() -> None:
    config = PosterConfig(paper_size=" 300x400mm ")
    title_schema = PosterConfig.model_json_schema()["properties"]["title"]
    orientation_schema = PosterConfig.model_json_schema()["properties"]["orientation"]

    assert config.paper_size == "300X400MM"
    assert title_schema["label"] == "Title"
    assert title_schema["group"] == "content"
    assert title_schema["help"]
    assert orientation_schema["choices"] == ["landscape", "portrait"]


def test_poster_config_rejects_unknown_fields_and_provider_zoom() -> None:
    with pytest.raises(ValidationError):
        PosterConfig(unexpected=True)
    with pytest.raises(ValidationError, match="OpenTopoMap supports zoom"):
        PosterConfig(provider="opentopo", zoom=18)


def test_route_color_override_is_normalized_and_validated() -> None:
    assert PosterConfig(style_name="cool-minimal").effective_route_color == "#153F63"
    assert PosterConfig(route_color="#2b6cb0").effective_route_color == "#2B6CB0"
    with pytest.raises(ValidationError, match="six-digit hexadecimal"):
        PosterConfig(route_color="blue")
