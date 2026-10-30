"""Business rules, tested on small hand-made examples so each test reads like a rule."""
from datetime import datetime

import pandas as pd
import pytest

from src import metrics

AS_OF = datetime(2026, 10, 30, 8, 0)  # Friday 08:00, the morning briefing
TODAY = pd.Timestamp("2026-10-30")


def days_ago(n):
    return TODAY - pd.Timedelta(days=n)


def make_orders(rows):
    """rows: (order_id, hours_ago, status, region, channel, value)."""
    return pd.DataFrame([{
        "order_id": oid, "order_date": pd.Timestamp(AS_OF) - pd.Timedelta(hours=h), "status": status,
        "region": region, "channel": channel, "order_value": value, "owner": "Mei Tanaka",
    } for oid, h, status, region, channel, value in rows])


def make_shipments(rows):
    """rows: (shipment_id, expected_days_ago, delivered_days_ago or None, status)."""
    return pd.DataFrame([{
        "shipment_id": sid, "order_id": "ORD-1", "carrier": "SwiftParcel", "status": status,
        "ship_date": days_ago(expected + 3),
        "expected_delivery_date": days_ago(expected),
        "actual_delivery_date": days_ago(delivered) if delivered is not None else pd.NaT,
        "owner": "Amara Okafor",
    } for sid, expected, delivered, status in rows])


def make_products(rows):
    """rows: (sku, stock_on_hand, min_stock, reorder_qty)."""
    return pd.DataFrame([{
        "sku": sku, "product_name": f"Product {sku}", "stock_on_hand": stock, "min_stock": minimum,
        "reorder_qty": reorder, "unit_cost": 10.0, "owner": "Priya Raman",
    } for sku, stock, minimum, reorder in rows])


def make_payments(rows):
    """rows: (payment_id, status, due_days_ago)."""
    return pd.DataFrame([{
        "payment_id": pid, "status": status, "due_date": days_ago(due), "type": "Supplier Invoice",
        "direction": "Outgoing", "counterparty": "Atlas Ceramics", "amount_eur": 1000.0, "owner": "Daniel Mensah",
    } for pid, status, due in rows])


def make_tickets(rows):
    """rows: (ticket_id, priority, status, hours_until_sla, resolved_hours_before_sla or None)."""
    out = []
    for tid, priority, status, hours_to_sla, resolved_early in rows:
        sla = pd.Timestamp(AS_OF) + pd.Timedelta(hours=hours_to_sla)
        out.append({
            "ticket_id": tid, "priority": priority, "status": status, "subject": "Parcel late",
            "created_at": sla - pd.Timedelta(hours=24), "sla_deadline": sla, "owner": "Ravi Patel",
            "resolved_at": sla - pd.Timedelta(hours=resolved_early) if resolved_early is not None else pd.NaT,
        })
    return pd.DataFrame(out)


# --- Orders ----------------------------------------------------------------

def test_order_is_stuck_after_48_hours_without_shipping():
    orders = metrics.enrich_orders(make_orders([
        ("A", 47, "Processing", "Spain", "Website", 50),
        ("B", 49, "Processing", "Spain", "Website", 50),
        ("C", 200, "Delivered", "Spain", "Website", 50),
    ]), AS_OF).set_index("order_id")
    assert not orders.loc["A", "is_stuck"]
    assert orders.loc["B", "is_stuck"]
    assert not orders.loc["C", "is_stuck"]


# --- Shipping --------------------------------------------------------------

def test_shipment_lateness_and_escalation():
    s = metrics.enrich_shipments(make_shipments([
        ("due-today", 0, None, "In Transit"),
        ("1-day-late", 1, None, "In Transit"),
        ("3-days-late", 3, None, "In Transit"),
        ("arrived-late", 5, 3, "Delivered"),
        ("arrived-on-time", 5, 5, "Delivered"),
    ]), AS_OF).set_index("shipment_id")

    assert not s.loc["due-today", "is_delayed"]
    assert s.loc["1-day-late", "is_delayed"] and s.loc["1-day-late", "days_late"] == 1
    assert not s.loc["1-day-late", "needs_escalation"]
    assert s.loc["3-days-late", "needs_escalation"]
    # Delivered shipments are no longer "delayed", but late ones count against on-time %.
    assert not s.loc["arrived-late", "is_delayed"]
    assert not s.loc["arrived-late", "delivered_on_time"]
    assert s.loc["arrived-on-time", "delivered_on_time"]


# --- Inventory -------------------------------------------------------------

@pytest.mark.parametrize("stock, expected_status, needs_order", [
    (0, "Out of Stock", True),
    (15, "Critical", True),    # at or below 50% of the minimum (30)
    (20, "Low", True),
    (30, "Low", True),         # at the minimum = time to reorder
    (31, "Healthy", False),
])
def test_stock_status(stock, expected_status, needs_order):
    p = metrics.enrich_inventory(make_products([("X", stock, 30, 60)])).iloc[0]
    assert p["stock_status"] == expected_status
    assert bool(p["needs_replenishment"]) is needs_order


def test_suggested_order_covers_the_shortfall():
    p = metrics.enrich_inventory(make_products([
        ("normal", 20, 30, 60),     # standard reorder quantity is enough
        ("big-gap", 0, 100, 40),    # reorder qty (40) would not reach the minimum (100)
        ("healthy", 50, 30, 60),
    ])).set_index("sku")
    assert p.loc["normal", "suggested_order_qty"] == 60
    assert p.loc["big-gap", "suggested_order_qty"] == 100
    assert p.loc["healthy", "suggested_order_qty"] == 0


# --- Payments --------------------------------------------------------------

def test_payment_overdue_and_escalation():
    p = metrics.enrich_payments(make_payments([
        ("paid-late", "Paid", 10),
        ("due-tomorrow", "Pending", -1),
        ("3-days-overdue", "Pending", 3),
        ("7-days-overdue", "Pending", 7),
        ("failed", "Failed", -2),
    ]), AS_OF).set_index("payment_id")
    assert not p.loc["paid-late", "is_overdue"]
    assert not p.loc["due-tomorrow", "is_overdue"]
    assert p.loc["3-days-overdue", "days_overdue"] == 3 and not p.loc["3-days-overdue", "needs_escalation"]
    assert p.loc["7-days-overdue", "needs_escalation"]
    assert p.loc["failed", "needs_escalation"]  # failed payments escalate even before the due date


# --- Support ---------------------------------------------------------------

def test_ticket_sla_states():
    t = metrics.enrich_tickets(make_tickets([
        ("breached-high", "High", "Open", -2, None),
        ("breached-low", "Low", "Open", -2, None),
        ("at-risk", "Medium", "In Progress", 3, None),
        ("comfortable", "Medium", "Open", 30, None),
        ("waiting", "High", "Waiting on Customer", -5, None),
        ("closed-in-time", "Critical", "Resolved", -10, 1),
    ]), AS_OF).set_index("ticket_id")

    assert t.loc["breached-high", "is_breached"] and t.loc["breached-high", "needs_escalation"]
    assert t.loc["breached-low", "is_breached"] and not t.loc["breached-low", "needs_escalation"]
    assert t.loc["at-risk", "is_at_risk"]
    assert not t.loc["comfortable", "is_at_risk"]
    assert not t.loc["waiting", "is_breached"]  # SLA clock is paused while waiting on the customer
    assert not t.loc["closed-in-time", "is_open"] and t.loc["closed-in-time", "met_sla"]


# --- Attention list and area health ----------------------------------------

def enriched_example():
    frames = {
        "orders": make_orders([("ORD-1", 60, "Processing", "Spain", "Website", 50)]),
        "shipments": make_shipments([("SHP-1", 4, None, "In Transit"), ("SHP-2", 1, None, "In Transit")]),
        "products": make_products([("OUT", 0, 30, 60), ("OK", 100, 30, 60)]),
        "payments": make_payments([("PAY-1", "Paid", 3)]),
        "tickets": make_tickets([("TCK-1", "Low", "Open", 40, None)]),
        "actions": pd.DataFrame(),
    }
    return metrics.enrich_all(frames, AS_OF)


def test_attention_list_ranks_by_severity_and_flags_escalations():
    items = metrics.attention_items(enriched_example())
    assert list(items["reference"]) == ["SHP-1", "OUT", "ORD-1", "SHP-2"]
    assert list(items["severity"]) == ["Critical", "Critical", "High", "Medium"]
    assert list(items["escalate"]) == [True, True, False, False]
    assert items.set_index("reference").loc["SHP-1", "owner"] == "Amara Okafor"


def test_area_health_uses_the_worst_issue_in_each_area():
    health = metrics.area_health(metrics.attention_items(enriched_example())).set_index("area")["status"]
    assert health["Shipping"] == "Critical"
    assert health["Orders"] == "At risk"
    assert health["Payments"] == "On track"
    assert health["Support"] == "On track"


def test_attention_list_handles_a_day_with_no_problems():
    # Regression test: an area with nothing to report used to crash the list.
    e = enriched_example()
    for name, flag in [("orders", "is_stuck"), ("shipments", "is_delayed"),
                       ("products", "needs_replenishment")]:
        e[name] = e[name][~e[name][flag]]
    items = metrics.attention_items(e)
    assert items.empty
    assert set(metrics.area_health(items)["status"]) == {"On track"}


# --- KPIs, trends and filters -----------------------------------------------

def test_kpis_compare_with_the_previous_period_and_exclude_cancellations():
    orders = make_orders([
        ("now-1", 24, "Delivered", "Spain", "Website", 100),
        ("now-2", 48, "Delivered", "Spain", "Website", 100),
        ("now-cancelled", 30, "Cancelled", "Spain", "Website", 999),
        ("before-1", 24 * 9, "Delivered", "Spain", "Website", 100),
        ("history", 24 * 20, "Delivered", "Spain", "Website", 100),
    ])
    e = enriched_example()
    e["orders"] = metrics.enrich_orders(orders, AS_OF)
    kpi = metrics.executive_kpis(e, AS_OF, period_days=7)
    assert kpi["orders_processed"] == 2
    assert kpi["revenue"] == 200
    assert kpi["orders_change"] == pytest.approx(1.0)  # 2 this week vs 1 the week before = +100%
    assert kpi["orders_previous"] == 1


def test_no_comparison_when_history_is_too_short():
    e = enriched_example()
    e["orders"] = metrics.enrich_orders(make_orders([("A", 24, "Delivered", "Spain", "Website", 10)]), AS_OF)
    assert metrics.executive_kpis(e, AS_OF, period_days=7)["orders_change"] is None


def test_order_trend_has_one_row_per_day_with_no_gaps():
    orders = metrics.enrich_orders(make_orders([
        ("A", 1, "Pending", "Spain", "Website", 10),
        ("B", 24 * 5, "Delivered", "Spain", "Website", 10),
    ]), AS_OF)
    trend = metrics.order_trend(orders, AS_OF, days=7)
    assert len(trend) == 7
    assert trend["orders"].sum() == 2
    assert (trend["orders"] == 0).sum() == 5  # quiet days appear as 0, not missing


def test_region_filter_also_filters_shipments():
    frames = {
        "orders": make_orders([("ORD-ES", 30, "Shipped", "Spain", "Website", 10),
                               ("ORD-FR", 30, "Shipped", "France", "Website", 10)]),
        "shipments": pd.DataFrame({"shipment_id": ["S1", "S2"], "order_id": ["ORD-ES", "ORD-FR"]}),
    }
    filtered = metrics.apply_order_filters(frames, regions=["Spain"])
    assert list(filtered["orders"]["order_id"]) == ["ORD-ES"]
    assert list(filtered["shipments"]["shipment_id"]) == ["S1"]
