from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fclights.config import Config, OPCConfig, PowerConfig, ServerConfig
from fclights.layout import Layout, simple_layout


@pytest.fixture
def layout() -> Layout:
    """The shipping scale: one Fadecandy, 512 pixels across eight outputs."""
    return simple_layout(512)


@pytest.fixture
def small_layout() -> Layout:
    return simple_layout(16)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        fps=60.0,
        power=PowerConfig(limit_amps=10.0),
        opc=OPCConfig(host="127.0.0.1", port=0),
        server=ServerConfig(host="127.0.0.1", port=0),
        layout_path=tmp_path / "layout.json",
        state_path=tmp_path / "state.json",
        simulate=True,
        simulate_pixels=512,
    )


@pytest.fixture
def frame(layout: Layout) -> np.ndarray:
    return np.zeros((layout.pixel_count, 3), dtype=np.float32)
