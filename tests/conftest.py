"""Shared test fixtures. Tests write their own copy of the demo data into a
temporary folder, so they never touch (or depend on) the files in data/."""
import pytest

from src import demo_data
from src.data_loader import load_all


@pytest.fixture
def demo_frames():
    return demo_data.generate_all(demo_data.DEFAULT_AS_OF, demo_data.SEED)


@pytest.fixture
def demo_dir(tmp_path, demo_frames):
    demo_data.write_all(demo_frames, demo_data.DEFAULT_AS_OF, tmp_path)
    return tmp_path


@pytest.fixture
def demo_bundle(demo_dir):
    return load_all(demo_dir, mode="demo")
