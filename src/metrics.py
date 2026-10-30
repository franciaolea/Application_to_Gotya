"""Business logic: turn raw operational data into flags, KPIs and alerts.

The pattern used throughout:
1. "enrich" functions add yes/no flags and measures to each record
   (is this shipment late? by how many days? should it be escalated?).
2. Summary functions count those flags into KPIs, trends and alert lists.

Keeping every rule in one place means the dashboard, the daily briefing and the
tests all agree on what "delayed" or "overdue" means. No Streamlit code here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from src import config

Frames = Dict[str, pd.DataFrame]

OPEN_ORDER_STATUSES = ["Pending", "Processing"]
CLOSED_TICKET_STATUSES = ["Resolved", "Closed"]
SLA_PAUSED_STATUSES = ["Waiting on Customer"]  # SLA clock stops while we wait on the customer
URGENT_PRIORITIES = ["Critical", "High"]

SEVERITIES = ["Critical", "High", "Medium"]
AREAS = ["Orders", "Shipping", "Inventory", "Payments", "Support"]

# Area status derived from the most severe open issue in that area.
HEALTH_BY_SEVERITY = {"Critical": "Critical", "High": "At risk", "Medium": "Watch", None: "On track"}


def _now(as_of: datetime) -> pd.Timestamp:
    return pd.Timestamp(as_of)


def _today(as_of: datetime) -> pd.Timestamp:
    return pd.Timestamp(as_of).normalize()


# --- 1. Enrichment: per-record flags ---------------------------------------

def enrich_orders(orders: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
    df = orders.copy()
    df["age_hours"] = (_now(as_of) - df["order_date"]).dt.total_seconds() / 3600
    df["is_open"] = df["status"].isin(OPEN_ORDER_STATUSES)          # not yet shipped
    df["is_stuck"] = df["is_open"] & (df["age_hours"] > config.ORDER_FULFILMENT_HOURS)
    df["is_cancelled"] = df["status"] == "Cancelled"
    return df


def enrich_shipments(shipments: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
    df = shipments.copy()
    today = _today(as_of)
    df["is_open"] = df["actual_delivery_date"].isna()
    # Delivered: compare actual vs expected. Still travelling: compare today vs expected.
    reference = df["actual_delivery_date"].fillna(today)
    df["days_late"] = (reference - df["expected_delivery_date"]).dt.days.clip(lower=0).fillna(0).astype(int)
    df["is_delayed"] = df["is_open"] & (df["days_late"] > 0)
    df["delivered_on_time"] = ~df["is_open"] & (df["days_late"] == 0)
    df["has_exception"] = df["status"] == "Exception"
    df["needs_escalation"] = df["is_open"] & (df["days_late"] >= config.SHIPMENT_ESCALATION_DAYS)
    return df


def enrich_inventory(products: pd.DataFrame) -> pd.DataFrame:
    df = products.copy()
    stock, minimum = df["stock_on_hand"], df["min_stock"]
    df["stock_status"] = np.select(
        [stock <= 0, stock <= minimum * config.CRITICAL_STOCK_RATIO, stock <= minimum],
        ["Out of Stock", "Critical", "Low"],
        default="Healthy",
    )
    df["needs_replenishment"] = stock <= minimum
    df["shortfall"] = (minimum - stock).clip(lower=0)
    # Order the standard reorder quantity, or more if that wouldn't reach the minimum.
    df["suggested_order_qty"] = np.where(df["needs_replenishment"],
                                         np.maximum(df["reorder_qty"], df["shortfall"]), 0)
    df["stock_value"] = stock * df["unit_cost"]
    return df


def enrich_payments(payments: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
    df = payments.copy()
    df["is_outstanding"] = df["status"] != "Paid"
    overdue_days = (_today(as_of) - df["due_date"]).dt.days.clip(lower=0).fillna(0).astype(int)
    df["days_overdue"] = np.where(df["is_outstanding"], overdue_days, 0)
    df["is_overdue"] = df["days_overdue"] > 0
    df["needs_escalation"] = df["is_outstanding"] & (
        (df["days_overdue"] >= config.PAYMENT_ESCALATION_DAYS) | (df["status"] == "Failed"))
    return df


def enrich_tickets(tickets: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
    df = tickets.copy()
    now = _now(as_of)
    df["is_open"] = ~df["status"].isin(CLOSED_TICKET_STATUSES)
    sla_running = df["is_open"] & ~df["status"].isin(SLA_PAUSED_STATUSES)
    df["hours_to_sla"] = (df["sla_deadline"] - now).dt.total_seconds() / 3600
    df["is_breached"] = sla_running & (df["hours_to_sla"] < 0)
    df["is_at_risk"] = sla_running & df["hours_to_sla"].between(0, config.TICKET_AT_RISK_HOURS)
    # For closed tickets: was it resolved before the deadline?
    df["met_sla"] = (~df["is_open"]) & (df["resolved_at"] <= df["sla_deadline"])
    df["needs_escalation"] = df["is_breached"] & df["priority"].isin(URGENT_PRIORITIES)
    return df


def enrich_all(frames: Frames, as_of: datetime) -> Frames:
    """Return a new set of frames with every flag added. Inputs are not changed."""
    return {
        "orders": enrich_orders(frames["orders"], as_of),
        "shipments": enrich_shipments(frames["shipments"], as_of),
        "products": enrich_inventory(frames["products"]),
        "payments": enrich_payments(frames["payments"], as_of),
        "tickets": enrich_tickets(frames["tickets"], as_of),
        "actions": frames["actions"].copy(),
    }


def apply_order_filters(frames: Frames, regions: Optional[Sequence[str]] = None,
                        channels: Optional[Sequence[str]] = None) -> Frames:
    """Limit orders (and the shipments that belong to them) to chosen regions/channels.

    Inventory, payments and support are company-wide and are left unfiltered.
    """
    out = dict(frames)
    orders = frames["orders"]
    if regions:
        orders = orders[orders["region"].isin(regions)]
    if channels:
        orders = orders[orders["channel"].isin(channels)]
    out["orders"] = orders
    out["shipments"] = frames["shipments"][frames["shipments"]["order_id"].isin(orders["order_id"])]
    return out


# --- 2. KPIs ---------------------------------------------------------------

def _pct_change(current: float, previous: float) -> Optional[float]:
    if not previous:
        return None  # no comparison possible (no data in the previous period)
    return (current - previous) / previous


def _rate(numerator: float, denominator: float) -> Optional[float]:
    return numerator / denominator if denominator else None


def executive_kpis(e: Frames, as_of: datetime, period_days: int = 7) -> dict:
    """Headline numbers for the Executive Dashboard.

    Two kinds of KPI:
    - Flow KPIs (orders, revenue, on-time %, SLA %) measure what happened during
      the selected period and are compared with the period before it.
    - Snapshot KPIs (pending orders, delayed shipments, low stock, open tickets)
      describe the situation right now, whatever period is selected.
    """
    now = _now(as_of)
    start = now - pd.Timedelta(days=period_days)
    prev_start = start - pd.Timedelta(days=period_days)
    # Only compare if order history reaches back (give or take a day) to the previous period.
    has_previous_data = bool(len(e["orders"])) and \
        e["orders"]["order_date"].min() <= prev_start + pd.Timedelta(days=1)

    orders = e["orders"][~e["orders"]["is_cancelled"]]
    current = orders[(orders["order_date"] >= start) & (orders["order_date"] < now)]
    previous = orders[(orders["order_date"] >= prev_start) & (orders["order_date"] < start)]

    shipments = e["shipments"]
    delivered = shipments[(shipments["actual_delivery_date"] >= start.normalize())
                          & (shipments["actual_delivery_date"] < now)]
    tickets = e["tickets"]
    resolved = tickets[(tickets["resolved_at"] >= start) & (tickets["resolved_at"] < now)]
    products, payments = e["products"], e["payments"]
    outstanding = payments[payments["is_outstanding"]]

    return {
        "period_days": period_days,
        # Orders (flow)
        "orders_processed": len(current),
        "orders_previous": len(previous) if has_previous_data else None,
        "orders_change": _pct_change(len(current), len(previous)) if has_previous_data else None,
        "revenue": float(current["order_value"].sum()),
        "revenue_change": (_pct_change(current["order_value"].sum(), previous["order_value"].sum())
                           if has_previous_data else None),
        # Orders (snapshot)
        "pending_orders": int(e["orders"]["is_open"].sum()),
        "stuck_orders": int(e["orders"]["is_stuck"].sum()),
        # Shipping
        "delayed_shipments": int(shipments["is_delayed"].sum()),
        "shipments_to_escalate": int(shipments["needs_escalation"].sum()),
        "shipment_exceptions": int((shipments["has_exception"] & shipments["is_open"]).sum()),
        "in_transit": int(shipments["is_open"].sum()),
        "on_time_rate": _rate(delivered["delivered_on_time"].sum(), len(delivered)),
        # Inventory
        "low_stock_items": int(products["needs_replenishment"].sum()),
        "out_of_stock_items": int((products["stock_status"] == "Out of Stock").sum()),
        # Payments
        "pending_payouts": len(outstanding),
        "receivable_eur": float(outstanding.loc[outstanding["direction"] == "Incoming", "amount_eur"].sum()),
        "payable_eur": float(outstanding.loc[outstanding["direction"] == "Outgoing", "amount_eur"].sum()),
        "overdue_payments": int(payments["is_overdue"].sum()),
        # Support
        "open_tickets": int(tickets["is_open"].sum()),
        "breached_tickets": int(tickets["is_breached"].sum()),
        "at_risk_tickets": int(tickets["is_at_risk"].sum()),
        "sla_compliance": _rate(resolved["met_sla"].sum(), len(resolved)),
    }


def yesterday_overview(e: Frames, as_of: datetime) -> dict:
    """What happened during the last full day before the briefing."""
    day = _today(as_of) - pd.Timedelta(days=1)

    def on_day(series: pd.Series) -> int:
        return int((series.dt.normalize() == day).sum())

    return {
        "date": day,
        "orders_received": on_day(e["orders"]["order_date"]),
        "shipments_dispatched": on_day(e["shipments"]["ship_date"]),
        "deliveries": on_day(e["shipments"]["actual_delivery_date"]),
        "tickets_opened": on_day(e["tickets"]["created_at"]),
        "tickets_resolved": on_day(e["tickets"]["resolved_at"]),
        "payments_completed": on_day(e["payments"]["paid_date"]),
    }


# --- 3. Trends -------------------------------------------------------------

def _days(as_of: datetime, days: int) -> pd.DatetimeIndex:
    """The last `days` calendar days up to and including the as-of day."""
    end = _today(as_of)
    return pd.date_range(end - pd.Timedelta(days=days - 1), end, freq="D")


def order_trend(orders: pd.DataFrame, as_of: datetime, days: int) -> pd.DataFrame:
    """Orders and revenue per day, plus a 7-day moving average to show the direction."""
    valid = orders[~orders["is_cancelled"]]
    by_day = valid.groupby(valid["order_date"].dt.normalize()).agg(
        orders=("order_id", "count"), revenue=("order_value", "sum"))
    # Compute the average over all history first, so the first days of the window have one too.
    full = by_day.reindex(pd.date_range(by_day.index.min(), _today(as_of), freq="D"), fill_value=0) \
        if len(by_day) else by_day
    full["orders_7d_avg"] = full["orders"].rolling(7, min_periods=1).mean()
    out = full.reindex(_days(as_of, days), fill_value=0)
    out.index.name = "date"
    return out.reset_index()


def on_time_trend(shipments: pd.DataFrame, as_of: datetime, days: int) -> pd.DataFrame:
    """Rolling 7-day on-time delivery rate per day.

    A single day has only a handful of deliveries, so its own rate jumps around
    (one late parcel = 75%). A rolling 7-day window shows the real trend.
    """
    delivered = shipments[~shipments["is_open"]]
    grouped = delivered.groupby(delivered["actual_delivery_date"].dt.normalize())
    by_day = pd.DataFrame({"delivered": grouped.size(),
                           "on_time": grouped["delivered_on_time"].sum()})
    index = pd.date_range(_today(as_of) - pd.Timedelta(days=days + 6), _today(as_of), freq="D")
    by_day = by_day.reindex(index, fill_value=0)
    rolled = by_day.rolling(7, min_periods=1).sum()
    by_day["on_time_rate_7d"] = (rolled["on_time"] / rolled["delivered"]).where(rolled["delivered"] > 0)
    out = by_day.reindex(_days(as_of, days))
    out.index.name = "date"
    return out.reset_index()


def support_backlog(tickets: pd.DataFrame) -> pd.DataFrame:
    """Open tickets per priority, split by SLA state."""
    open_tickets = tickets[tickets["is_open"]].copy()
    open_tickets["sla_state"] = np.select(
        [open_tickets["is_breached"], open_tickets["is_at_risk"],
         open_tickets["status"].isin(SLA_PAUSED_STATUSES)],
        ["Breached", "At risk", "Paused"], default="Within SLA")
    table = pd.crosstab(open_tickets["priority"], open_tickets["sla_state"])
    states = ["Breached", "At risk", "Within SLA", "Paused"]
    priorities = list(config.TICKET_SLA_HOURS)
    return table.reindex(index=priorities, columns=states, fill_value=0).rename_axis("priority").reset_index()


# --- 4. Attention list -----------------------------------------------------

def _day_count(n: pd.Series, suffix: str) -> pd.Series:
    """'1 day late', '3 days late'."""
    n = n.astype(int)
    return n.astype(str) + np.where(n == 1, " day ", " days ") + suffix


def _issues(df: pd.DataFrame, area: str, severity, reference: str, issue, owner: str, detail) -> pd.DataFrame:
    """Build a uniform issue table from any dataset. severity/issue/detail may be columns."""
    return pd.DataFrame({
        "severity": severity, "area": area, "reference": df[reference].values,
        "issue": issue, "owner": df[owner].values, "detail": detail,
    }, index=df.index)


# One builder per area. Each receives only the problem records (never an empty table).

def _order_issues(o: pd.DataFrame) -> pd.DataFrame:
    return _issues(
        o, "Orders",
        np.where(o["age_hours"] > config.ORDER_ESCALATION_HOURS, "Critical", "High"),
        "order_id", "Order not shipped (" + o["status"].str.lower() + ")", "owner",
        o["age_hours"].round().astype(int).astype(str) + "h since order")


def _shipment_issues(s: pd.DataFrame) -> pd.DataFrame:
    return _issues(
        s, "Shipping",
        np.select([s["needs_escalation"], s["has_exception"]], ["Critical", "High"], "Medium"),
        "shipment_id",
        np.where(s["has_exception"], "Carrier exception - " + s["carrier"], "Delivery overdue - " + s["carrier"]),
        "owner", np.where(s["days_late"] > 0, _day_count(s["days_late"], "late"),
                          "Due " + s["expected_delivery_date"].dt.strftime("%d %b")))


def _inventory_issues(p: pd.DataFrame) -> pd.DataFrame:
    return _issues(
        p, "Inventory",
        p["stock_status"].map({"Out of Stock": "Critical", "Critical": "High", "Low": "Medium"}),
        "sku", p["stock_status"] + ": " + p["product_name"], "owner",
        p["stock_on_hand"].astype(int).astype(str) + " / min " + p["min_stock"].astype(int).astype(str))


def _payment_issues(pay: pd.DataFrame) -> pd.DataFrame:
    return _issues(
        pay, "Payments",
        np.select([pay["needs_escalation"], pay["status"] == "On Hold"], ["Critical", "High"], "Medium"),
        "payment_id", pay["status"] + " " + pay["type"].str.lower() + " - " + pay["counterparty"], "owner",
        "€" + pay["amount_eur"].map("{:,.0f}".format) + ", " + _day_count(pay["days_overdue"], "overdue"))


def _ticket_issues(t: pd.DataFrame) -> pd.DataFrame:
    urgent = t["priority"].isin(URGENT_PRIORITIES)
    return _issues(
        t, "Support",
        np.select([t["is_breached"] & urgent, urgent | t["is_breached"]], ["Critical", "High"], "Medium"),
        "ticket_id", t["priority"] + " ticket: " + t["subject"], "owner",
        np.where(t["is_breached"],
                 "SLA breached " + (-t["hours_to_sla"]).round().astype(int).astype(str) + "h ago",
                 "SLA due in " + t["hours_to_sla"].round(1).astype(str) + "h"))


def attention_items(e: Frames) -> pd.DataFrame:
    """Every open problem in one list, ranked by severity.

    Severity rules (Critical = escalate to leadership today):
      Orders     stuck > 96h -> Critical, stuck > 48h -> High
      Shipping   >= 3 days late -> Critical, carrier exception -> High, 1-2 days late -> Medium
      Inventory  out of stock -> Critical, critical stock -> High, low -> Medium
      Payments   failed or >= 7 days overdue -> Critical, on hold -> High, overdue -> Medium
      Support    urgent ticket past SLA -> Critical, urgent at risk or other past SLA -> High,
                 other at risk -> Medium
    """
    orders, shipments, products = e["orders"], e["shipments"], e["products"]
    payments, tickets = e["payments"], e["tickets"]
    problems = [
        (orders[orders["is_stuck"]], _order_issues),
        (shipments[shipments["is_delayed"] | (shipments["has_exception"] & shipments["is_open"])],
         _shipment_issues),
        (products[products["needs_replenishment"]], _inventory_issues),
        (payments[payments["is_outstanding"]
                  & (payments["is_overdue"] | payments["status"].isin(["Failed", "On Hold"]))],
         _payment_issues),
        (tickets[tickets["is_breached"] | tickets["is_at_risk"]], _ticket_issues),
    ]
    parts = [build(records) for records, build in problems if not records.empty]

    columns = ["severity", "area", "reference", "issue", "owner", "detail", "escalate"]
    if not parts:
        return pd.DataFrame(columns=columns)
    items = pd.concat(parts, ignore_index=True)
    items["escalate"] = items["severity"] == "Critical"
    items["severity"] = pd.Categorical(items["severity"], SEVERITIES, ordered=True)
    items["area"] = pd.Categorical(items["area"], AREAS, ordered=True)
    return items.sort_values(["severity", "area"]).reset_index(drop=True)[columns]


def area_health(items: pd.DataFrame) -> pd.DataFrame:
    """One status per area, taken from its most severe open issue."""
    rows = []
    for area in AREAS:
        in_area = items[items["area"] == area]
        worst = in_area["severity"].min() if len(in_area) else None  # ordered: Critical < High < Medium
        counts = in_area["severity"].value_counts()
        rows.append({
            "area": area,
            "status": HEALTH_BY_SEVERITY[None if pd.isna(worst) else worst],
            "open_issues": len(in_area),
            "critical": int(counts.get("Critical", 0)),
            "high": int(counts.get("High", 0)),
        })
    return pd.DataFrame(rows)


def issues_by_owner(items: pd.DataFrame) -> pd.DataFrame:
    """Open issues per owner and area: who is carrying the operational load."""
    if items.empty:
        return pd.DataFrame(columns=["owner", "area", "issues"])
    table = items.groupby(["owner", "area"], observed=True).size().rename("issues").reset_index()
    totals = table.groupby("owner")["issues"].transform("sum")
    return table.assign(total=totals).sort_values(["total", "owner"], ascending=[True, True]).drop(columns="total")
