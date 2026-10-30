"""The same KPI must show the same number on every page, and differences must come from a stated period.

These tests use the demo data and compare the figures that different pages display.
"""
from datetime import datetime

import pandas as pd
import pytest

from src import config, demo_data, metrics
from src.data_loader import load_all
from src.demo_data import DEFAULT_AS_OF, SEED

AS_OF = datetime(2026, 10, 30, 8, 0)


@pytest.fixture
def e(demo_bundle):
    return metrics.enrich_all(demo_bundle.frames, demo_bundle.as_of)


# --- Reporting periods -------------------------------------------------------------

@pytest.mark.parametrize("days", [1, 7, 14, 28, 30])
def test_every_window_is_whole_days_ending_before_today(days):
    window = metrics.reporting_window(AS_OF, days)
    assert window.end == pd.Timestamp("2026-10-30")          # the snapshot day is not included
    assert window.days == days and len(window.dates()) == days
    previous = metrics.previous_window(window)
    assert previous.end == window.start and previous.days == days   # back to back, same length


def test_yesterday_at_a_glance_is_the_one_day_window(e, demo_bundle):
    day = metrics.yesterday_overview(e, demo_bundle.as_of)
    one_day = metrics.order_trend(e["orders"], demo_bundle.as_of, days=1)
    assert day["date"] == pd.Timestamp("2026-10-29")
    received = e["orders"][metrics.reporting_window(demo_bundle.as_of, 1).contains(e["orders"]["order_date"])]
    assert day["orders_received"] == len(received)
    assert one_day["orders"].sum() == (~received["is_cancelled"]).sum()


# --- On-time delivery: dashboard card, trend chart, Shipping page -----------------------------

@pytest.mark.parametrize("days", [7, 14, 28])
def test_dashboard_on_time_card_equals_last_point_of_the_7_day_trend(e, demo_bundle, days):
    card = metrics.executive_kpis(e, demo_bundle.as_of, 7)["on_time_rate"]
    trend = metrics.on_time_trend(e["shipments"], demo_bundle.as_of, days)
    assert trend["on_time_rate_7d"].iloc[-1] == pytest.approx(card)


def test_28_day_card_equals_the_shipping_page_and_carrier_scorecard(e, demo_bundle):
    card = metrics.executive_kpis(e, demo_bundle.as_of, 28)["on_time_rate"]
    carriers = metrics.carrier_performance(e["shipments"], demo_bundle.as_of, days=28)
    assert carriers["on_time"].sum() / carriers["delivered"].sum() == pytest.approx(card)


def test_7_and_28_day_rates_differ_only_because_the_period_differs(e, demo_bundle):
    shipments = e["shipments"]
    for days in (7, 28):
        window = metrics.reporting_window(demo_bundle.as_of, days)
        delivered = shipments[~shipments["is_open"] & window.contains(shipments["actual_delivery_date"])]
        expected = delivered["delivered_on_time"].mean()
        assert metrics.on_time_summary(shipments, window)["rate"] == pytest.approx(expected)


# --- Orders -------------------------------------------------------------------------------------

@pytest.mark.parametrize("days", [7, 14, 28])
def test_orders_card_equals_the_sum_of_the_order_chart(e, demo_bundle, days):
    card = metrics.executive_kpis(e, demo_bundle.as_of, days)
    chart = metrics.order_trend(e["orders"], demo_bundle.as_of, days)
    assert chart["orders"].sum() == card["orders_processed"]
    assert chart["revenue"].sum() == pytest.approx(card["revenue"])


def test_pending_orders_are_a_snapshot_independent_of_the_period(e, demo_bundle):
    values = {metrics.executive_kpis(e, demo_bundle.as_of, d)["pending_orders"] for d in (7, 14, 28)}
    assert values == {int(e["orders"]["is_open"].sum())}


# --- Payments --------------------------------------------------------------------------------

def test_pending_payouts_mean_the_same_on_dashboard_and_payments_page(e, demo_bundle):
    card = metrics.executive_kpis(e, demo_bundle.as_of, 7)
    page = metrics.payment_kpis(e["payments"], demo_bundle.as_of)
    assert (card["pending_payouts_count"], card["pending_payouts_eur"]) == (page["incoming_count"], page["incoming_eur"])
    assert (card["payables_count"], card["payables_eur"]) == (page["outgoing_count"], page["outgoing_eur"])
    incoming = e["payments"][e["payments"]["is_outstanding"] & (e["payments"]["direction"] == "Incoming")]
    assert card["pending_payouts_count"] == len(incoming)       # payouts = money owed to us only


# --- Support -----------------------------------------------------------------------------------

def test_support_figures_match_between_dashboard_and_support_page(e, demo_bundle):
    card = metrics.executive_kpis(e, demo_bundle.as_of, 7)
    page = metrics.support_kpis(e["tickets"], demo_bundle.as_of, days=7)
    assert card["sla_compliance"] == pytest.approx(page["sla_compliance"])
    assert card["sla_resolved"] == page["resolved"]
    assert (card["open_tickets"], card["breached_tickets"], card["at_risk_tickets"]) == \
        (page["open"], page["breached"], page["at_risk"])


# --- Shipping: delayed vs exceptions ---------------------------------------------------------------

def test_a_late_parcel_with_an_exception_is_followed_up_once(e, demo_bundle):
    s = e["shipments"].copy()
    late = s.index[s["is_delayed"]][0]
    s.loc[late, ["status", "has_exception"]] = ["Exception", True]   # now both late and an exception
    card = metrics.executive_kpis({**e, "shipments": s}, demo_bundle.as_of, 7)
    assert card["shipments_follow_up"] == card["delayed_shipments"] + card["shipment_exceptions"] - 1
    shipping_issues = metrics.area_issues("Shipping", s)
    assert len(shipping_issues) == card["shipments_follow_up"]          # one issue per parcel, not two


# --- Inventory -----------------------------------------------------------------------------------

def test_stockout_risk_uses_the_same_28_day_sales_window(e, demo_bundle):
    window = metrics.reporting_window(demo_bundle.as_of, 28)
    products = metrics.add_demand(e["products"], e["orders"], demo_bundle.as_of)
    sku = products.sort_values("units_sold").iloc[-1]["sku"]
    orders = e["orders"]
    expected = orders[(orders["sku"] == sku) & window.contains(orders["order_date"]) & ~orders["is_cancelled"]]
    assert products.set_index("sku").loc[sku, "units_sold"] == expected["quantity"].sum()


# --- Determinism ---------------------------------------------------------------------------------

def test_committed_demo_data_is_exactly_what_the_generator_produces():
    """Anyone can regenerate the demo data and get the same files (only the generation time differs)."""
    committed = load_all(mode="demo").frames
    fresh = demo_data.generate_all(DEFAULT_AS_OF, SEED)
    for name, df in fresh.items():
        on_disk = pd.read_csv(config.data_dir("demo") / config.DATASET_FILES[name], dtype=str, keep_default_na=False)
        pd.testing.assert_frame_equal(on_disk, df.astype(str).replace("nan", ""), check_dtype=False)
    assert set(committed) == set(fresh)
