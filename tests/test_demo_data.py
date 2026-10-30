"""The demo data must meet the brief and contain realistic problems to show."""
import pandas as pd

from src import demo_data


def test_meets_minimum_volumes(demo_frames):
    assert len(demo_frames["orders"]) >= 30
    assert len(demo_frames["products"]) >= 20
    assert len(demo_frames["tickets"]) >= 15


def test_generation_is_reproducible():
    first = demo_data.generate_all(demo_data.DEFAULT_AS_OF, seed=1)
    second = demo_data.generate_all(demo_data.DEFAULT_AS_OF, seed=1)
    for name in first:
        pd.testing.assert_frame_equal(first[name], second[name])


def test_nothing_happens_after_the_snapshot(demo_bundle):
    as_of = demo_bundle.as_of
    frames = demo_bundle.frames
    assert frames["orders"]["order_date"].max() <= as_of
    assert frames["tickets"]["created_at"].max() <= as_of
    assert frames["tickets"]["resolved_at"].dropna().max() <= as_of
    assert frames["shipments"]["actual_delivery_date"].dropna().max() <= as_of
    assert frames["payments"]["paid_date"].dropna().max() <= as_of


def test_contains_problems_for_the_dashboard_to_find(demo_bundle):
    frames = demo_bundle.frames
    today = pd.Timestamp(demo_bundle.as_of).normalize()

    products = frames["products"]
    assert (products["stock_on_hand"] < products["min_stock"]).any()

    shipments = frames["shipments"]
    overdue = shipments["actual_delivery_date"].isna() & (shipments["expected_delivery_date"] < today)
    assert overdue.any()

    tickets = frames["tickets"]
    assert (tickets["priority"] == "Critical").any()


def test_is_clearly_labelled_as_demo(demo_bundle):
    assert demo_bundle.is_demo
    assert demo_bundle.metadata["data_classification"] == "FICTIONAL DEMO DATA"
