"""Behaviour of the SVG text-fit checker, driven through its command line.

The checker exists because an overrunning SVG label is silent in the source, so
the one thing it must never do is report success for a diagram it did not
actually measure. These tests run the real tool against real files and assert on
its exit status and output.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).parent.parent / "tools" / "check-svg-text-fit.py"

CHROME_CANDIDATES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")

pytestmark = pytest.mark.skipif(
    not any(shutil.which(name) for name in CHROME_CANDIDATES),
    reason="needs a Chrome or Chromium binary to render with",
)

PANEL = '<rect x="10" y="10" width="240" height="60" fill="#fff" stroke="#000" stroke-width="2"/>'


def diagram(body: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 200" '
        'width="400" height="200">' + body + "</svg>"
    )


def run(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), str(path)],
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_passes_when_every_label_sits_inside_its_panel(tmp_path: Path) -> None:
    svg = tmp_path / "fits.svg"
    svg.write_text(
        diagram(PANEL + '<text x="20" y="45" font-family="sans-serif" font-size="12">short</text>'),
        encoding="utf-8",
    )

    result = run(svg)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout


def test_fails_when_a_label_overruns_its_panel(tmp_path: Path) -> None:
    svg = tmp_path / "overruns.svg"
    svg.write_text(
        diagram(
            PANEL + '<text x="20" y="45" font-family="sans-serif" font-size="14">'
            "a label far too long to stay inside the border of this panel</text>"
        ),
        encoding="utf-8",
    )

    result = run(svg)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "overruns.svg" in result.stdout
    assert "crosses the right border" in result.stdout


def test_fails_loudly_when_the_probe_measures_nothing(tmp_path: Path) -> None:
    """A file with labels the probe cannot reach must fail, not report success.

    The probe only measures elements under an <svg> root, so this file's two
    labels are never measured. Before the checker demanded a completion marker,
    that empty result was indistinguishable from a clean one and it printed ok.
    """
    svg = tmp_path / "unmeasurable.svg"
    svg.write_text(
        '<drawing xmlns="http://example.invalid/notsvg">'
        '<text x="10" y="20">a label the probe can never measure</text>'
        '<text x="10" y="40">and another one</text>'
        "</drawing>",
        encoding="utf-8",
    )

    result = run(svg)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "unmeasurable.svg" in result.stdout
    assert "could not measure this diagram" in result.stdout
    assert not result.stdout.startswith("ok")


def test_reports_no_collisions_for_a_diagram_with_no_labels(tmp_path: Path) -> None:
    """Zero measured nodes is only a failure when the file has labels to measure."""
    svg = tmp_path / "wordless.svg"
    svg.write_text(diagram(PANEL), encoding="utf-8")

    result = run(svg)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout
