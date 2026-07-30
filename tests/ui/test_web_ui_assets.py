"""Structural checks for the server-rendered, dependency-free web interface."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

WEB_ROOT = Path(__file__).parents[2] / "src" / "outdoor_maps_plot" / "web"
TEMPLATES = WEB_ROOT / "templates"
STATIC = WEB_ROOT / "static"


class MarkupInspector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.labels: list[str] = []
        self.controls: dict[str, str] = {}
        self.scripts: list[str] = []
        self.stylesheets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if element_id := attributes.get("id"):
            self.ids.append(element_id)
        if tag == "label" and (target := attributes.get("for")):
            self.labels.append(target)
        if tag in {"input", "select", "textarea"} and (control_id := attributes.get("id")):
            self.controls[control_id] = tag
        if tag == "script" and (source := attributes.get("src")):
            self.scripts.append(source)
        if (
            tag == "link"
            and attributes.get("rel") == "stylesheet"
            and (href := attributes.get("href"))
        ):
            self.stylesheets.append(href)


def render_index() -> str:
    html = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    include_pattern = re.compile(r'{%\s*include\s+"([^"]+)"\s*%}')
    return include_pattern.sub(
        lambda match: (TEMPLATES / match.group(1)).read_text(encoding="utf-8"),
        html,
    )


def test_index_has_unique_ids_and_programmatic_labels() -> None:
    inspector = MarkupInspector()
    inspector.feed(render_index())

    assert len(inspector.ids) == len(set(inspector.ids))
    assert set(inspector.labels) <= set(inspector.controls)
    assert "/static/theme.js" in inspector.scripts
    assert "/static/app.js" in inspector.scripts
    assert "/static/app.css" in inspector.stylesheets


def test_every_poster_config_field_is_represented() -> None:
    html = render_index()
    expected_names = {
        "title",
        "subtitle",
        "paper_size",
        "orientation",
        "provider",
        "zoom",
        "padding_percent",
        "margin_mm",
        "basemap_width",
        "max_tiles",
        "simplify_points",
        "route_width",
        "route_color",
        "route_order",
        "output_format",
        "dpi",
        "jpeg_quality",
    }

    for name in expected_names:
        assert f'name="{name}"' in html
    assert 'radio.name = "style_name"' in (STATIC / "app.js").read_text(encoding="utf-8")


def test_ui_has_no_runtime_cdn_and_supports_documented_workflow() -> None:
    html = render_index()
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "https://" not in html
    assert "http://" not in html
    assert 'api("/api/config")' in javascript
    assert 'api("/api/uploads"' in javascript
    assert 'api("/api/renders"' in javascript
    assert "EventSource" in javascript
    assert "setInterval" in javascript
    assert "maxFiles = Math.min" in javascript
    assert 'createRender("preview")' in javascript
    assert 'createRender("final")' in javascript


def test_ui_supports_persistent_light_and_dark_themes() -> None:
    html = render_index()
    stylesheet = (STATIC / "app.css").read_text(encoding="utf-8")
    theme_javascript = (STATIC / "theme.js").read_text(encoding="utf-8")

    assert 'id="theme-toggle"' in html
    assert 'content="light dark"' in html
    assert ':root[data-theme="dark"]' in stylesheet
    assert "prefers-color-scheme: dark" in theme_javascript
    assert "window.localStorage" in theme_javascript
    assert 'root.dataset.theme === "dark" ? "light" : "dark"' in theme_javascript
