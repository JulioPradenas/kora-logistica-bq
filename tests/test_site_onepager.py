"""
Tests Paso 10 — site/index.html one-pager.

Verifica existencia, secciones clave, link Looker y que todo src/href relativo
apunte a un archivo real del repo (sin duplicar PNGs bajo site/).
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]
SITE_INDEX = ROOT / "site" / "index.html"

LOOKER_URL = (
    "https://datastudio.google.com/reporting/"
    "172aa667-fb63-4c36-84d4-4585c8ad89ba"
)

# Textos / anclas que el one-pager debe cubrir (ES).
REQUIRED_SNIPPETS = (
    "Abrir dashboard en vivo",
    "Qué responde cada página",
    "Por qué confiar en los números",
    "De un CSV estilo ERP al reporte",
    "datos sintéticos",
    "No hay conexión real a SAP",
)


class _AttrCollector(HTMLParser):
    """Collect src/href attribute values from HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.values: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        mapping = {k: v for k, v in attrs if v is not None}
        for key in ("src", "href"):
            if key in mapping:
                self.values.append((key, mapping[key]))


def _is_relative_repo_path(url: str) -> bool:
    """True for paths that should resolve inside the git tree."""
    if not url or url.startswith("#"):
        return False
    if url.startswith(("mailto:", "tel:", "data:", "javascript:")):
        return False
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https"):
        return False
    # Absolute URL without scheme (//cdn...) — skip.
    if url.startswith("//"):
        return False
    return True


@pytest.fixture(scope="module")
def html_text() -> str:
    assert SITE_INDEX.is_file(), f"missing {SITE_INDEX.relative_to(ROOT)}"
    return SITE_INDEX.read_text(encoding="utf-8")


def test_site_index_exists():
    assert SITE_INDEX.is_file()


def test_site_has_key_sections(html_text: str):
    for snippet in REQUIRED_SNIPPETS:
        assert snippet in html_text, f"missing section/snippet: {snippet!r}"
    # Section markers for hand-editing.
    for marker in (
        "<!-- SECCIÓN: Hero -->",
        "<!-- SECCIÓN: Dashboard en vivo -->",
        "<!-- SECCIÓN: Qué responde cada página -->",
        "<!-- SECCIÓN: Por qué confiar en los números -->",
        "<!-- SECCIÓN: Arquitectura -->",
        "<!-- SECCIÓN: Disclaimer -->",
    ):
        assert marker in html_text, f"missing HTML comment: {marker}"


def test_site_has_looker_link(html_text: str):
    assert LOOKER_URL in html_text


def test_site_relative_paths_exist(html_text: str):
    parser = _AttrCollector()
    parser.feed(html_text)
    assert parser.values, "expected at least one src/href"

    site_dir = SITE_INDEX.parent
    missing: list[str] = []
    checked = 0
    for attr, raw in parser.values:
        if not _is_relative_repo_path(raw):
            continue
        # Strip query/fragment; keep path only.
        path_part = unquote(urlparse(raw).path)
        if not path_part:
            continue
        resolved = (site_dir / path_part).resolve()
        checked += 1
        if not resolved.is_file():
            missing.append(f"{attr}={raw!r} → {resolved}")

    assert checked >= 5, "expected screenshot relative paths under site/"
    assert not missing, "broken relative paths:\n" + "\n".join(missing)


def test_site_screenshots_point_to_looker_dir_not_duplicated(html_text: str):
    """PNGs live in looker/screenshots/; site/ must not re-host them."""
    for name in (
        "01-overview.png",
        "02-heatmap.png",
        "03-funnel.png",
        "04-drill.png",
        "05-stock.png",
    ):
        assert f"../looker/screenshots/{name}" in html_text, name
        assert not (ROOT / "site" / name).exists(), f"do not duplicate {name} into site/"
