"""Shared test fixtures. Tests write their own copy of the demo data into a
temporary folder, so they never touch (or depend on) the files in data/."""
import pandas as pd
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


@pytest.fixture
def quiet_frames(demo_bundle):
    """The demo data with every operational problem removed: a day where all is well."""
    f = {name: df.copy() for name, df in demo_bundle.frames.items()}
    as_of = pd.Timestamp(demo_bundle.as_of)
    f["orders"]["status"] = "Delivered"
    s = f["shipments"]
    s["status"] = "Delivered"
    s["actual_delivery_date"] = s["expected_delivery_date"].where(
        s["expected_delivery_date"] < as_of, as_of.normalize() - pd.Timedelta(days=1))
    f["products"]["stock_on_hand"] = f["products"]["min_stock"] * 3
    f["payments"]["status"] = "Paid"
    f["payments"]["paid_date"] = f["payments"]["due_date"]
    f["tickets"]["status"] = "Resolved"
    f["tickets"]["resolved_at"] = f["tickets"]["created_at"] + pd.Timedelta(hours=1)
    return f
