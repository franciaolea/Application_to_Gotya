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
        "shipment_id": sid, "order_id": "ORD-1", "carrier": "Veloxa Courier", "status": status,
        "ship_date": days_ago(expected + 3),
        "expected_delivery_date": days_ago(expected),
        "actual_delivery_date": days_ago(delivered) if delivered is not None else pd.NaT,
        "owner": "Amara Okafor",
    } for sid, expected, delivered, status in rows])


def make_products(rows):
    """rows: (sku, stock_on_hand, min_stock, reorder_qty)."""
    return pd.DataFrame([{
        "sku": sku, "product_name": f"Product {sku}", "stock_on_hand": stock, "min_stock": minimum,
        "reorder_qty": reorder, "unit_cost": 10.0, "lead_time_days": 7, "owner": "Priya Raman",
    } for sku, stock, minimum, reorder in rows])


def make_payments(rows):
    """rows: (payment_id, status, due_days_ago)."""
    return pd.DataFrame([{
        "payment_id": pid, "status": status, "due_date": days_ago(due), "type": "Supplier Invoice",
        "direction": "Outgoing", "counterparty": "Atlas Ceramics", "amount_eur": 1000.0, "owner": "Daniel Mensah",
        "paid_date": days_ago(due) if status == "Paid" else pd.NaT,
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
        ("yesterday", 10, "Pending", "Spain", "Website", 10),       # Thu 29 Oct, 22:00
        ("5-days-ago", 24 * 5, "Delivered", "Spain", "Website", 10),
    ]), AS_OF)
    trend = metrics.order_trend(orders, AS_OF, days=7)
    assert len(trend) == 7
    assert trend["orders"].sum() == 2
    assert (trend["orders"] == 0).sum() == 5  # quiet days appear as 0, not missing


def test_reporting_window_is_whole_days_before_the_snapshot():
    window = metrics.reporting_window(AS_OF, 7)
    assert (window.start, window.end) == (pd.Timestamp("2026-10-23"), pd.Timestamp("2026-10-30"))
    assert window.days == 7 and window.label == "23–29 Oct"
    assert metrics.previous_window(window).label == "16–22 Oct"
    # Activity from this morning (before the 08:00 snapshot) is not part of "the last 7 days".
    this_morning = pd.Series([pd.Timestamp(AS_OF) - pd.Timedelta(hours=1)])
    assert not window.contains(this_morning).iloc[0]


def test_this_mornings_orders_are_not_counted_in_period_kpis():
    orders = metrics.enrich_orders(make_orders([
        ("this-morning", 1, "Pending", "Spain", "Website", 10),
        ("yesterday", 10, "Pending", "Spain", "Website", 10),
    ]), AS_OF)
    assert metrics.order_trend(orders, AS_OF, days=7)["orders"].sum() == 1
    e = enriched_example()
    e["orders"] = orders
    assert metrics.executive_kpis(e, AS_OF, period_days=7)["orders_processed"] == 1


def test_region_filter_also_filters_shipments():
    frames = {
        "orders": make_orders([("ORD-ES", 30, "Shipped", "Spain", "Website", 10),
                               ("ORD-FR", 30, "Shipped", "France", "Website", 10)]),
        "shipments": pd.DataFrame({"shipment_id": ["S1", "S2"], "order_id": ["ORD-ES", "ORD-FR"]}),
    }
    filtered = metrics.apply_order_filters(frames, regions=["Spain"])
    assert list(filtered["orders"]["order_id"]) == ["ORD-ES"]
    assert list(filtered["shipments"]["shipment_id"]) == ["S1"]


# --- Inventory drill-down ------------------------------------------------------

def test_days_of_cover_and_stockout_risk():
    products = metrics.enrich_inventory(make_products([
        ("FAST", 10, 30, 60),   # sells 1/day -> 10 days of cover
        ("SLOW", 10, 30, 60),   # sells 4 in 28 days (1 every 7 days) -> 70 days of cover
        ("NONE", 10, 30, 60),   # no recent sales
    ])).assign(lead_time_days=14)
    orders = make_orders([("A", 24 * d, "Delivered", "Spain", "Website", 10) for d in range(1, 29)])
    orders["sku"] = ["FAST"] * 28
    orders["quantity"] = 1
    extra = make_orders([("B", 24 * d, "Delivered", "Spain", "Website", 10) for d in (1, 8, 15, 22)])
    extra["sku"], extra["quantity"] = "SLOW", 1
    p = metrics.add_demand(products, pd.concat([orders, extra]), AS_OF, window_days=28).set_index("sku")

    assert p.loc["FAST", "days_of_cover"] == pytest.approx(10)
    assert p.loc["FAST", "stockout_risk"]          # 10 days of stock, 14 days to restock
    assert p.loc["SLOW", "days_of_cover"] == pytest.approx(70)
    assert not p.loc["SLOW", "stockout_risk"]
    assert pd.isna(p.loc["NONE", "days_of_cover"]) and not p.loc["NONE", "stockout_risk"]
    assert p.loc["FAST", "reorder_cost"] == 60 * 10.0


def test_cancelled_orders_do_not_count_as_demand():
    products = metrics.enrich_inventory(make_products([("X", 10, 30, 60)]))
    orders = make_orders([("A", 24, "Cancelled", "Spain", "Website", 10)])
    orders["sku"], orders["quantity"] = "X", 5
    assert metrics.add_demand(products, orders, AS_OF).iloc[0]["units_sold"] == 0


# --- Shipping drill-down --------------------------------------------------------

def test_carrier_scorecard():
    s = make_shipments([
        ("A1", 5, 5, "Delivered"), ("A2", 5, 5, "Delivered"), ("A3", 5, 3, "Delivered"),  # 2 of 3 on time
        ("A4", 4, None, "In Transit"),                                                      # 4 days overdue
        ("B1", 5, 5, "Delivered"), ("B2", 0, None, "Exception"),
    ])
    s["carrier"] = ["Slow Co"] * 4 + ["Fast Co"] * 2
    perf = metrics.carrier_performance(metrics.enrich_shipments(s, AS_OF), AS_OF).set_index("carrier")

    assert perf.loc["Slow Co", "on_time_rate"] == pytest.approx(2 / 3)
    assert perf.loc["Slow Co", "below_target"]
    assert perf.loc["Slow Co", "delayed"] == 1
    assert perf.loc["Fast Co", "on_time_rate"] == 1.0
    assert perf.loc["Fast Co", "exceptions"] == 1


def test_open_shipments_are_bucketed_by_lateness():
    s = make_shipments([("on-time", 0, None, "In Transit"), ("late", 2, None, "In Transit"),
                        ("very-late", 5, None, "In Transit"), ("done", 9, 9, "Delivered")])
    table = metrics.open_shipments_by_lateness(metrics.enrich_shipments(s, AS_OF)).iloc[0]
    assert (table["On schedule"], table["1-2 days late"], table["3+ days late"]) == (1, 1, 1)


# --- Shared helpers ---------------------------------------------------------------

def test_filter_frame_treats_empty_selection_as_all():
    df = pd.DataFrame({"category": ["Kitchen", "Bath", "Decor"], "warehouse": ["A", "A", "B"]})
    assert len(metrics.filter_frame(df, {"category": [], "warehouse": None})) == 3
    assert list(metrics.filter_frame(df, {"category": ["Kitchen", "Decor"], "warehouse": ["B"]})["category"]) \
        == ["Decor"]


def test_area_issues_and_status_for_a_single_module():
    products = metrics.enrich_inventory(make_products([("LOW", 20, 30, 60), ("OK", 50, 30, 60)]))
    issues = metrics.area_issues("Inventory", products)
    assert list(issues["reference"]) == ["LOW"]
    assert metrics.status_of(issues) == "Watch"
    assert metrics.status_of(metrics.area_issues("Inventory", products.iloc[[1]])) == "On track"


# --- Payments drill-down ----------------------------------------------------------

def payment(pid, direction, amount, status, due_days_ago, paid_days_ago=None):
    return {"payment_id": pid, "type": "Supplier Invoice", "direction": direction, "counterparty": "X",
            "amount_eur": amount, "status": status, "due_date": days_ago(due_days_ago),
            "paid_date": days_ago(paid_days_ago) if paid_days_ago is not None else pd.NaT,
            "owner": "Daniel Mensah"}


def test_payment_kpis_split_money_in_and_out():
    p = metrics.enrich_payments(pd.DataFrame([
        payment("in-pending", "Incoming", 500, "Pending", -3),         # due in 3 days
        payment("in-overdue", "Incoming", 200, "Pending", 2),          # 2 days overdue
        payment("out-on-hold", "Outgoing", 1000, "On Hold", -20),
        payment("out-paid", "Outgoing", 300, "Paid", 5, paid_days_ago=6),
    ]), AS_OF)
    kpi = metrics.payment_kpis(p, AS_OF)
    assert (kpi["incoming_count"], kpi["incoming_eur"]) == (2, 700)
    assert (kpi["outgoing_count"], kpi["outgoing_eur"]) == (1, 1000)
    assert (kpi["overdue_count"], kpi["overdue_eur"]) == (1, 200)
    assert (kpi["blocked_count"], kpi["blocked_eur"]) == (1, 1000)
    assert (kpi["due_soon_count"], kpi["due_soon_eur"]) == (1, 500)
    assert (kpi["paid_30d_count"], kpi["paid_30d_eur"]) == (1, 300)


def test_payment_due_buckets():
    p = metrics.enrich_payments(pd.DataFrame([
        payment("very-late", "Outgoing", 100, "Pending", 9),
        payment("late", "Outgoing", 200, "Pending", 2),
        payment("this-week", "Incoming", 300, "Pending", -4),
        payment("later", "Incoming", 400, "Pending", -30),
        payment("paid", "Incoming", 999, "Paid", 2, paid_days_ago=2),  # paid: not outstanding
    ]), AS_OF)
    t = metrics.payment_due_buckets(p, AS_OF).set_index(["bucket", "direction"])["amount_eur"]
    assert t[("7+ days overdue", "Outgoing")] == 100
    assert t[("1-6 days overdue", "Outgoing")] == 200
    assert t[("Due within 7 days", "Incoming")] == 300
    assert t[("Due later", "Incoming")] == 400
    assert t.sum() == 1000


# --- Support drill-down -----------------------------------------------------------

def test_support_kpis_and_resolution_vs_sla():
    t = metrics.enrich_tickets(make_tickets([
        ("met", "High", "Resolved", -30, 4),          # resolved 4h before its deadline (yesterday)
        ("missed", "High", "Closed", -30, -2),        # resolved 2h after its deadline (yesterday)
        ("open-breached", "Low", "Open", -1, None),
        ("waiting", "Medium", "Waiting on Customer", 5, None),
    ]), AS_OF)
    kpi = metrics.support_kpis(t, AS_OF, days=7)
    assert kpi["open"] == 2 and kpi["paused"] == 1 and kpi["breached"] == 1
    assert kpi["urgent_breached"] == 0          # the breached ticket is Low priority
    assert kpi["sla_compliance"] == pytest.approx(0.5)
    assert kpi["avg_resolution_hours"] == pytest.approx(((24 - 4) + (24 + 2)) / 2)

    high = metrics.resolution_vs_sla(t).set_index("priority").loc["High"]
    assert high["resolved"] == 2 and high["sla_hours"] == 24 and high["met_sla"] == pytest.approx(0.5)


def test_delivery_complaints_are_a_rate_per_100_parcels():
    shipments = pd.DataFrame({
        "order_id": [f"BIG-{i}" for i in range(50)] + [f"SMALL-{i}" for i in range(10)],
        "carrier": ["Big Co"] * 50 + ["Small Co"] * 10,
        "ship_date": [days_ago(3)] * 60,
    })
    tickets = pd.DataFrame({
        "order_id": ["BIG-1", "BIG-2", "SMALL-1", "SMALL-2", "BIG-3"],
        "category": ["Where is my order"] * 4 + ["Product question"],   # questions don't count
        "created_at": [pd.Timestamp(AS_OF) - pd.Timedelta(days=1)] * 5,
    })
    rate = metrics.delivery_contacts_by_carrier(tickets, shipments, AS_OF).set_index("carrier")
    # Same number of complaints, but Small Co ships far less: 20 per 100 vs 4 per 100.
    assert rate.loc["Big Co", "per_100_shipments"] == pytest.approx(4)
    assert rate.loc["Small Co", "per_100_shipments"] == pytest.approx(20)


# --- Issue prioritisation -------------------------------------------------------------------

def test_urgency_order_within_and_across_severities():
    frames = {
        "orders": make_orders([("ORD-STUCK", 60, "Processing", "Spain", "Website", 50)]),   # High, 12h overdue
        "shipments": make_shipments([
            ("SHP-5-LATE", 5, None, "In Transit"),        # Critical, 5 days overdue
            ("SHP-3-LATE", 3, None, "In Transit"),        # Critical, 3 days overdue
            ("SHP-EXC-SOON", -1, None, "Exception"),      # High, due tomorrow
            ("SHP-EXC-LATER", -4, None, "Exception"),     # High, due in 4 days
        ]),
        "products": make_products([("OUT", 0, 30, 60), ("CRIT", 5, 30, 60)]),               # no deadline field
        "payments": make_payments([("PAY-1", "Paid", 3)]),
        "tickets": make_tickets([("TCK-BREACH", "High", "Open", -30, None)]),               # Critical, 30h overdue
        "actions": pd.DataFrame(),
    }
    items = metrics.attention_items(metrics.enrich_all(frames, AS_OF))
    assert list(items["reference"]) == [
        "SHP-5-LATE", "SHP-3-LATE", "TCK-BREACH", "OUT",     # Critical: longest overdue first, no deadline last
        "ORD-STUCK", "SHP-EXC-SOON", "SHP-EXC-LATER", "CRIT",  # High: overdue, then soonest due, then no deadline
    ]
    assert list(items["urgency"])[:3] == ["Overdue 5 days", "Overdue 3 days", "Overdue 30h"]
    assert items.set_index("reference").loc["SHP-EXC-SOON", "urgency"] == "Due in 1 day"
    assert items.set_index("reference").loc["OUT", "urgency"] == "Stock 0 / min 30"


def test_money_at_stake_breaks_ties():
    payments = metrics.enrich_payments(pd.DataFrame([
        {**row, "amount_eur": amount} for row, amount in zip(
            make_payments([("SMALL", "Pending", 2), ("BIG", "Pending", 2)]).to_dict("records"), (100.0, 5000.0))
    ]), AS_OF)
    items = metrics.area_issues("Payments", payments)
    assert list(items["reference"]) == ["BIG", "SMALL"]         # same severity and overdue: larger amount first


def test_urgency_wording():
    assert metrics._urgency_text(0, "", date_deadline=True) == "Due today"
    assert metrics._urgency_text(-48, "", date_deadline=True) == "Overdue 2 days"
    assert metrics._urgency_text(24, "", date_deadline=True) == "Due in 1 day"
    assert metrics._urgency_text(-20, "", date_deadline=False) == "Overdue 20h"
    assert metrics._urgency_text(3.6, "", date_deadline=False) == "Due in 4h"
    assert metrics._urgency_text(float("nan"), "Stock 1 / min 9", date_deadline=False) == "Stock 1 / min 9"
