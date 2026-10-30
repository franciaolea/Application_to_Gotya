"""Business logic: turn raw operational data into flags, KPIs and alerts.

The pattern used throughout:
1. "enrich" functions add yes/no flags and measures to each record
   (is this shipment late? by how many days? should it be escalated?).
2. Summary functions count those flags into KPIs, trends and alert lists.

Keeping every rule in one place means the dashboard, the daily briefing and the
tests all agree on what "delayed" or "overdue" means. No Streamlit code here.
"""
from __future__ import annotations

from dataclasses import dataclass
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


# --- Reporting periods: one rule for every page ----------------------------------

@dataclass(frozen=True)
class Window:
    """A reporting period of whole days: from `start` (inclusive) to `end` (exclusive), both midnight."""
    start: pd.Timestamp
    end: pd.Timestamp

    @property
    def days(self) -> int:
        return int((self.end - self.start).days)

    def contains(self, values: pd.Series) -> pd.Series:
        return (values >= self.start) & (values < self.end)

    def dates(self) -> pd.DatetimeIndex:
        return pd.date_range(self.start, self.end - pd.Timedelta(days=1), freq="D")

    @property
    def label(self) -> str:
        """'23–29 Oct', '25 Oct – 7 Nov' or '29 Oct'."""
        last = self.end - pd.Timedelta(days=1)
        if self.days == 1:
            return f"{last.day} {last:%b}"
        if self.start.month == last.month:
            return f"{self.start.day}–{last.day} {last:%b}"
        return f"{self.start.day} {self.start:%b} – {last.day} {last:%b}"


def reporting_window(as_of: datetime, days: int) -> Window:
    """'Last N days' = the N complete calendar days before the snapshot.

    A snapshot taken on Friday 30 Oct at 08:00 with days=7 covers Fri 23 – Thu 29 Oct.
    Today's partial morning is left out, so every period is made of whole days and
    a period can be compared fairly with the one before it. Every KPI, trend chart
    and scorecard that reports "last N days" uses this function.
    """
    end = _today(as_of)
    return Window(end - pd.Timedelta(days=days), end)


def previous_window(window: Window) -> Window:
    """The period of the same length immediately before `window`."""
    return Window(window.start - pd.Timedelta(days=window.days), window.start)


def on_time_summary(shipments: pd.DataFrame, window: Window) -> dict:
    """On-time delivery for parcels delivered within `window` (the single definition used everywhere)."""
    delivered = shipments[~shipments["is_open"] & window.contains(shipments["actual_delivery_date"])]
    on_time = int(delivered["delivered_on_time"].sum())
    return {"delivered": len(delivered), "on_time": on_time, "rate": _rate(on_time, len(delivered))}


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
    df["days_to_expected"] = (df["expected_delivery_date"] - today).dt.days  # negative = late
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
    df["days_to_due"] = (df["due_date"] - _today(as_of)).dt.days  # negative = overdue
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
      the reporting period (see reporting_window) and are compared with the period before it.
    - Snapshot KPIs (pending orders, delayed shipments, low stock, payments, open
      tickets) describe the situation right now, whatever period is selected.
    Module pages call the same functions, so the same KPI shows the same number everywhere.
    """
    window = reporting_window(as_of, period_days)
    previous_period = previous_window(window)
    # Only compare if order history reaches back (give or take a day) to the previous period.
    has_previous_data = bool(len(e["orders"])) and \
        e["orders"]["order_date"].min() <= previous_period.start + pd.Timedelta(days=1)

    orders = e["orders"][~e["orders"]["is_cancelled"]]
    current = orders[window.contains(orders["order_date"])]
    previous = orders[previous_period.contains(orders["order_date"])]

    shipments = e["shipments"]
    delivery = on_time_summary(shipments, window)
    tickets = e["tickets"]
    resolved = tickets[window.contains(tickets["resolved_at"])]
    products = e["products"]
    pay = payment_kpis(e["payments"], as_of)

    return {
        "period_days": period_days,
        "window": window,
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
        # Shipping (snapshot) - delayed and exceptions can overlap; "follow-up" counts each parcel once
        "delayed_shipments": int(shipments["is_delayed"].sum()),
        "shipments_to_escalate": int(shipments["needs_escalation"].sum()),
        "shipment_exceptions": int((shipments["has_exception"] & shipments["is_open"]).sum()),
        "shipments_follow_up": int((shipments["is_delayed"] | (shipments["has_exception"] & shipments["is_open"])).sum()),
        "in_transit": int(shipments["is_open"].sum()),
        # Shipping (flow)
        "on_time_rate": delivery["rate"],
        "delivered": delivery["delivered"],
        # Inventory (snapshot)
        "low_stock_items": int(products["needs_replenishment"].sum()),
        "out_of_stock_items": int((products["stock_status"] == "Out of Stock").sum()),
        # Payments (snapshot) - same definitions as the Payments page (payment_kpis)
        "pending_payouts_count": pay["incoming_count"],
        "pending_payouts_eur": pay["incoming_eur"],
        "payables_count": pay["outgoing_count"],
        "payables_eur": pay["outgoing_eur"],
        "overdue_payments": pay["overdue_count"],
        # Support (snapshot + flow)
        "open_tickets": int(tickets["is_open"].sum()),
        "breached_tickets": int(tickets["is_breached"].sum()),
        "at_risk_tickets": int(tickets["is_at_risk"].sum()),
        "sla_resolved": len(resolved),
        "sla_compliance": _rate(resolved["met_sla"].sum(), len(resolved)),
    }


def yesterday_overview(e: Frames, as_of: datetime) -> dict:
    """What happened during the last full day before the briefing (a 1-day reporting window)."""
    window = reporting_window(as_of, 1)

    def on_day(series: pd.Series) -> int:
        return int(window.contains(series).sum())

    return {
        "date": window.start,
        "orders_received": on_day(e["orders"]["order_date"]),
        "shipments_dispatched": on_day(e["shipments"]["ship_date"]),
        "deliveries": on_day(e["shipments"]["actual_delivery_date"]),
        "tickets_opened": on_day(e["tickets"]["created_at"]),
        "tickets_resolved": on_day(e["tickets"]["resolved_at"]),
        "payments_completed": on_day(e["payments"]["paid_date"]),
    }


# --- 3. Trends -------------------------------------------------------------

def order_trend(orders: pd.DataFrame, as_of: datetime, days: int) -> pd.DataFrame:
    """Orders and revenue per day over the reporting window, plus a 7-day moving average.

    The bars add up exactly to the "Orders processed" KPI for the same period.
    """
    window = reporting_window(as_of, days)
    valid = orders[~orders["is_cancelled"]]
    by_day = valid.groupby(valid["order_date"].dt.normalize()).agg(
        orders=("order_id", "count"), revenue=("order_value", "sum"))
    # Compute the average over all history first, so the first days of the window have one too.
    last_day = window.end - pd.Timedelta(days=1)
    full = by_day.reindex(pd.date_range(min(by_day.index.min(), window.start), last_day, freq="D"),
                          fill_value=0) if len(by_day) else by_day
    full["orders_7d_avg"] = full["orders"].rolling(7, min_periods=1).mean()
    out = full.reindex(window.dates(), fill_value=0)
    out.index.name = "date"
    return out.reset_index()


def on_time_trend(shipments: pd.DataFrame, as_of: datetime, days: int) -> pd.DataFrame:
    """Rolling 7-day on-time delivery rate for each day of the reporting window.

    A single day has only a handful of deliveries, so its own rate jumps around
    (one late parcel = 75%). Each point is on_time_summary() over the 7 days ending
    that day, so the last point equals the dashboard's 7-day on-time KPI.
    """
    window = reporting_window(as_of, days)
    delivered = shipments[~shipments["is_open"]]
    grouped = delivered.groupby(delivered["actual_delivery_date"].dt.normalize())
    by_day = pd.DataFrame({"delivered": grouped.size(),
                           "on_time": grouped["delivered_on_time"].sum()})
    index = pd.date_range(window.start - pd.Timedelta(days=6), window.end - pd.Timedelta(days=1), freq="D")
    by_day = by_day.reindex(index, fill_value=0)
    rolled = by_day.rolling(7, min_periods=1).sum()
    by_day["on_time_rate_7d"] = (rolled["on_time"] / rolled["delivered"]).where(rolled["delivered"] > 0)
    out = by_day.reindex(window.dates())
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


# --- 3b. Module drill-downs: inventory and shipping ----------------------------

def filter_frame(df: pd.DataFrame, selections: Dict[str, Sequence]) -> pd.DataFrame:
    """Keep rows matching every non-empty selection, e.g. {"category": ["Kitchen"]}.
    An empty selection means "all"."""
    for column, values in selections.items():
        if values:
            df = df[df[column].isin(values)]
    return df


def add_demand(products: pd.DataFrame, orders: pd.DataFrame, as_of: datetime,
               window_days: int = config.DEMAND_WINDOW_DAYS) -> pd.DataFrame:
    """Add sales velocity and "days of cover" to enriched products.

    Days of cover = stock / average daily units sold: how long the stock lasts
    at the current selling rate. If that is shorter than the supplier lead time,
    the product will sell out before a new order could arrive.
    """
    df = products.copy()
    window = reporting_window(as_of, window_days)
    recent = orders[window.contains(orders["order_date"]) & (orders["status"] != "Cancelled")]
    units = recent.groupby("sku")["quantity"].sum()
    df["units_sold"] = df["sku"].map(units).fillna(0).astype(int)
    df["daily_demand"] = df["units_sold"] / window_days
    df["days_of_cover"] = (df["stock_on_hand"] / df["daily_demand"]).where(df["daily_demand"] > 0)
    df["stockout_risk"] = (df["days_of_cover"] < df["lead_time_days"]).fillna(False)
    df["reorder_cost"] = df["suggested_order_qty"] * df["unit_cost"]
    return df


LATENESS_BUCKETS = ["On schedule", "1-2 days late", "3+ days late"]


def carrier_performance(shipments: pd.DataFrame, as_of: datetime, days: int = 28) -> pd.DataFrame:
    """Scorecard per carrier: on-time rate over the last `days` (reporting window), plus what is open now."""
    window = reporting_window(as_of, days)
    delivered = shipments[~shipments["is_open"] & window.contains(shipments["actual_delivery_date"])]
    late = delivered[~delivered["delivered_on_time"]]
    open_now = shipments[shipments["is_open"]]
    carriers = sorted(shipments["carrier"].dropna().unique())
    table = pd.DataFrame({
        "delivered": delivered.groupby("carrier").size(),
        "on_time": delivered.groupby("carrier")["delivered_on_time"].sum(),
        "avg_days_late": late.groupby("carrier")["days_late"].mean(),
        "open": open_now.groupby("carrier").size(),
        "delayed": open_now.groupby("carrier")["is_delayed"].sum(),
        "exceptions": open_now.groupby("carrier")["has_exception"].sum(),
    }).reindex(carriers)
    count_columns = ["delivered", "on_time", "open", "delayed", "exceptions"]
    table[count_columns] = table[count_columns].fillna(0).astype(int)
    table["on_time_rate"] = (table["on_time"] / table["delivered"]).where(table["delivered"] > 0)
    table["below_target"] = table["on_time_rate"] < config.ON_TIME_DELIVERY_TARGET
    return table.rename_axis("carrier").reset_index()


def open_shipments_by_lateness(shipments: pd.DataFrame) -> pd.DataFrame:
    """Open shipments per carrier, split into on schedule / 1-2 days late / 3+ days late."""
    open_now = shipments[shipments["is_open"]].copy()
    open_now["lateness"] = np.select(
        [open_now["days_late"] >= config.SHIPMENT_ESCALATION_DAYS, open_now["days_late"] > 0],
        ["3+ days late", "1-2 days late"], default="On schedule")
    table = pd.crosstab(open_now["carrier"], open_now["lateness"])
    carriers = sorted(shipments["carrier"].dropna().unique())
    return table.reindex(index=carriers, columns=LATENESS_BUCKETS, fill_value=0) \
        .rename_axis("carrier").reset_index()


# --- 3c. Module drill-downs: payments and support --------------------------------

def _due_bucket_labels():
    esc = config.PAYMENT_ESCALATION_DAYS
    return [f"{esc}+ days overdue", f"1-{esc - 1} days overdue", "Due within 7 days", "Due later"]


def payment_kpis(payments: pd.DataFrame, as_of: datetime) -> dict:
    """Money owed to us (incoming) and by us (outgoing), and what needs chasing."""
    today = _today(as_of)
    outstanding = payments[payments["is_outstanding"]]
    days_to_due = (outstanding["due_date"] - today).dt.days
    due_soon = outstanding[(days_to_due >= 0) & (days_to_due <= 7)]
    blocked = outstanding[outstanding["status"].isin(["Failed", "On Hold"])]
    overdue = outstanding[outstanding["is_overdue"]]
    paid_recently = payments[reporting_window(as_of, 30).contains(payments["paid_date"])]

    def total(df: pd.DataFrame) -> float:
        return float(df["amount_eur"].sum())

    incoming = outstanding[outstanding["direction"] == "Incoming"]
    outgoing = outstanding[outstanding["direction"] == "Outgoing"]
    return {
        "incoming_count": len(incoming), "incoming_eur": total(incoming),
        "outgoing_count": len(outgoing), "outgoing_eur": total(outgoing),
        "overdue_count": len(overdue), "overdue_eur": total(overdue),
        "due_soon_count": len(due_soon), "due_soon_eur": total(due_soon),
        "blocked_count": len(blocked), "blocked_eur": total(blocked),
        "paid_30d_count": len(paid_recently), "paid_30d_eur": total(paid_recently),
    }


def payment_due_buckets(payments: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
    """Outstanding amounts by due-date bucket and direction (a simple ageing report)."""
    labels = _due_bucket_labels()
    outstanding = payments[payments["is_outstanding"]].copy()
    days_to_due = (outstanding["due_date"] - _today(as_of)).dt.days
    outstanding["bucket"] = np.select(
        [outstanding["days_overdue"] >= config.PAYMENT_ESCALATION_DAYS, outstanding["days_overdue"] > 0,
         days_to_due <= 7], labels[:3], default=labels[3])
    table = outstanding.groupby(["bucket", "direction"]).agg(
        amount_eur=("amount_eur", "sum"), count=("payment_id", "count"))
    index = pd.MultiIndex.from_product([labels, ["Incoming", "Outgoing"]], names=["bucket", "direction"])
    return table.reindex(index, fill_value=0).reset_index()


def outstanding_by_type(payments: pd.DataFrame) -> pd.DataFrame:
    outstanding = payments[payments["is_outstanding"]]
    return outstanding.groupby(["type", "direction"]).agg(
        amount_eur=("amount_eur", "sum"), count=("payment_id", "count")) \
        .reset_index().sort_values("amount_eur")


def support_kpis(tickets: pd.DataFrame, as_of: datetime, days: int = 7) -> dict:
    """Backlog right now, plus how well tickets resolved in the last `days` met their SLA."""
    now = _now(as_of)
    open_tickets = tickets[tickets["is_open"]]
    resolved = tickets[reporting_window(as_of, days).contains(tickets["resolved_at"])]
    hours_to_resolve = (resolved["resolved_at"] - resolved["created_at"]).dt.total_seconds() / 3600
    return {
        "open": len(open_tickets),
        "breached": int(tickets["is_breached"].sum()),
        "urgent_breached": int(tickets["needs_escalation"].sum()),
        "at_risk": int(tickets["is_at_risk"].sum()),
        "paused": int(open_tickets["status"].isin(SLA_PAUSED_STATUSES).sum()),
        "resolved": len(resolved),
        "sla_compliance": _rate(resolved["met_sla"].sum(), len(resolved)),
        "avg_resolution_hours": float(hours_to_resolve.mean()) if len(resolved) else None,
        "oldest_open_hours": float((now - open_tickets["created_at"]).dt.total_seconds().max() / 3600)
        if len(open_tickets) else None,
    }


def resolution_vs_sla(tickets: pd.DataFrame) -> pd.DataFrame:
    """Average time to resolve per priority, next to the SLA allowed for that priority."""
    resolved = tickets[~tickets["is_open"]].copy()
    resolved["hours"] = (resolved["resolved_at"] - resolved["created_at"]).dt.total_seconds() / 3600
    table = resolved.groupby("priority").agg(
        resolved=("ticket_id", "count"), avg_hours=("hours", "mean"), met_sla=("met_sla", "mean"))
    table = table.reindex(list(config.TICKET_SLA_HOURS))
    table["sla_hours"] = list(config.TICKET_SLA_HOURS.values())
    table["resolved"] = table["resolved"].fillna(0).astype(int)
    return table.rename_axis("priority").reset_index()


def contacts_by_category(tickets: pd.DataFrame) -> pd.DataFrame:
    """Why customers contact us: tickets per category, open vs resolved."""
    table = pd.crosstab(tickets["category"], np.where(tickets["is_open"], "Open", "Resolved"))
    table = table.reindex(columns=["Open", "Resolved"], fill_value=0).rename_axis(columns=None)
    table["total"] = table.sum(axis=1)
    return table.sort_values("total").rename_axis("category").reset_index()


DELIVERY_CATEGORIES = ["Where is my order", "Damaged item"]


def delivery_contacts_by_carrier(tickets: pd.DataFrame, shipments: pd.DataFrame, as_of: datetime,
                                 days: int = 14) -> pd.DataFrame:
    """Delivery-related tickets per carrier, per 100 parcels shipped.

    Raw counts would favour the smallest carrier, so we compare a rate:
    tickets in the last `days` days / parcels shipped in the same period x 100.
    """
    window = reporting_window(as_of, days)
    recent = tickets[window.contains(tickets["created_at"]) & tickets["category"].isin(DELIVERY_CATEGORIES)]
    linked = recent.merge(shipments[["order_id", "carrier"]], on="order_id", how="inner")
    shipped = shipments[window.contains(shipments["ship_date"])].groupby("carrier").size()
    carriers = sorted(shipments["carrier"].dropna().unique())
    table = pd.DataFrame({"tickets": linked.groupby("carrier").size(), "shipped": shipped}) \
        .reindex(carriers).fillna(0).astype(int)
    table["per_100_shipments"] = (table["tickets"] / table["shipped"] * 100).where(table["shipped"] > 0)
    return table.rename_axis("carrier").reset_index()


# --- 4. Attention list -----------------------------------------------------

def _day_count(n: pd.Series, suffix: str) -> pd.Series:
    """'1 day late', '3 days late'."""
    n = n.astype(int)
    return n.astype(str) + np.where(n == 1, " day ", " days ") + suffix


def _issues(df: pd.DataFrame, area: str, severity, reference: str, issue, owner: str, detail,
            hours_to_due=np.nan, impact_eur=np.nan, date_deadline: bool = False) -> pd.DataFrame:
    """Build a uniform issue table from any dataset. severity/issue/detail may be columns.

    hours_to_due: hours until the record's own deadline (negative = already overdue);
    NaN when the record has no deadline field. impact_eur: money at stake where the
    data has it (order value, payment amount). Both drive the urgency ordering.
    date_deadline: the deadline is a date (delivery/due date), so urgency is shown in days.
    """
    return pd.DataFrame({
        "severity": severity, "area": area, "reference": df[reference].values,
        "issue": issue, "owner": df[owner].values, "detail": detail,
        "hours_to_due": hours_to_due, "impact_eur": impact_eur, "date_deadline": date_deadline,
    }, index=df.index)


# One builder per area. Each receives only the problem records (never an empty table).

def _order_issues(o: pd.DataFrame) -> pd.DataFrame:
    return _issues(
        o, "Orders",
        np.where(o["age_hours"] > config.ORDER_ESCALATION_HOURS, "Critical", "High"),
        "order_id", "Order not shipped (" + o["status"].str.lower() + ")", "owner",
        o["age_hours"].round().astype(int).astype(str) + "h since order",
        hours_to_due=config.ORDER_FULFILMENT_HOURS - o["age_hours"], impact_eur=o["order_value"])


def _shipment_issues(s: pd.DataFrame) -> pd.DataFrame:
    return _issues(
        s, "Shipping",
        np.select([s["needs_escalation"], s["has_exception"]], ["Critical", "High"], "Medium"),
        "shipment_id",
        np.where(s["has_exception"], "Carrier exception - " + s["carrier"], "Delivery overdue - " + s["carrier"]),
        "owner", np.where(s["days_late"] > 0, _day_count(s["days_late"], "late"),
                          "Due " + s["expected_delivery_date"].dt.strftime("%d %b")),
        hours_to_due=s["days_to_expected"] * 24, date_deadline=True)


def _inventory_issues(p: pd.DataFrame) -> pd.DataFrame:
    return _issues(
        p, "Inventory",
        p["stock_status"].map({"Out of Stock": "Critical", "Critical": "High", "Low": "Medium"}),
        "sku", p["stock_status"] + ": " + p["product_name"], "owner",
        "Stock " + p["stock_on_hand"].astype(int).astype(str) + " / min " + p["min_stock"].astype(int).astype(str))


def _payment_issues(pay: pd.DataFrame) -> pd.DataFrame:
    return _issues(
        pay, "Payments",
        np.select([pay["needs_escalation"], pay["status"] == "On Hold"], ["Critical", "High"], "Medium"),
        "payment_id", pay["status"] + " " + pay["type"].str.lower() + " - " + pay["counterparty"], "owner",
        "€" + pay["amount_eur"].map("{:,.0f}".format) + ", " + np.where(
            pay["days_overdue"] > 0, _day_count(pay["days_overdue"], "overdue"),
            "due " + pay["due_date"].dt.strftime("%d %b")),
        hours_to_due=pay["days_to_due"] * 24, impact_eur=pay["amount_eur"], date_deadline=True)


def _ticket_issues(t: pd.DataFrame) -> pd.DataFrame:
    urgent = t["priority"].isin(URGENT_PRIORITIES)
    return _issues(
        t, "Support",
        np.select([t["is_breached"] & urgent, urgent | t["is_breached"]], ["Critical", "High"], "Medium"),
        "ticket_id", t["priority"] + " ticket: " + t["subject"], "owner",
        np.where(t["is_breached"],
                 "SLA breached " + (-t["hours_to_sla"]).round().astype(int).astype(str) + "h ago",
                 "SLA due in " + t["hours_to_sla"].round(1).astype(str) + "h"),
        hours_to_due=t["hours_to_sla"])


# Per area: which enriched dataset to read, which records are problems, how to describe them.
AREA_RULES = {
    "Orders": ("orders", lambda o: o["is_stuck"], _order_issues),
    "Shipping": ("shipments", lambda s: s["is_delayed"] | (s["has_exception"] & s["is_open"]), _shipment_issues),
    "Inventory": ("products", lambda p: p["needs_replenishment"], _inventory_issues),
    "Payments": ("payments", lambda p: p["is_outstanding"] & (
        p["is_overdue"] | p["status"].isin(["Failed", "On Hold"])), _payment_issues),
    "Support": ("tickets", lambda t: t["is_breached"] | t["is_at_risk"], _ticket_issues),
}
ISSUE_COLUMNS = ["severity", "area", "reference", "issue", "owner", "detail", "urgency", "overdue_hours",
                 "hours_to_due", "impact_eur", "escalate"]


def _urgency_text(hours_to_due: float, detail: str, date_deadline: bool) -> str:
    """'Overdue 5 days', 'Overdue 20h', 'Due in 4h', 'Due today', or the detail when there is no deadline."""
    if pd.isna(hours_to_due):
        return detail
    if date_deadline:  # whole days: today, tomorrow, N days
        days = int(round(hours_to_due / 24))
        if days == 0:
            return "Due today"
        unit = "day" if abs(days) == 1 else "days"
        return f"Overdue {-days} {unit}" if days < 0 else f"Due in {days} {unit}"
    hours = abs(hours_to_due)
    amount = f"{hours / 24:.0f} days" if hours >= 48 else f"{hours:.0f}h"
    return f"Overdue {amount}" if hours_to_due < 0 else f"Due in {amount}"


def _finalise(parts: List[pd.DataFrame]) -> pd.DataFrame:
    """Combine issue tables and rank them by urgency.

    Order: severity (Critical first) -> already overdue -> longest overdue -> soonest
    deadline -> most money at stake. Issues without a deadline field (inventory) come
    after time-based issues of the same severity. All inputs are existing data fields.
    """
    parts = [part for part in parts if not part.empty]
    if not parts:
        # Typed empty table: "escalate" must be boolean so items[items["escalate"]] still filters rows.
        return pd.DataFrame(columns=ISSUE_COLUMNS).astype({"escalate": bool})
    items = pd.concat(parts, ignore_index=True)
    items["escalate"] = items["severity"].astype(str) == "Critical"
    items["severity"] = pd.Categorical(items["severity"].astype(str), SEVERITIES, ordered=True)
    items["area"] = pd.Categorical(items["area"].astype(str), AREAS, ordered=True)
    items["hours_to_due"] = pd.to_numeric(items["hours_to_due"], errors="coerce")
    items["impact_eur"] = pd.to_numeric(items["impact_eur"], errors="coerce")
    items["overdue_hours"] = (-items["hours_to_due"]).clip(lower=0).fillna(0)
    if "urgency" not in items.columns:  # area tables already carry it when combined
        items["urgency"] = [_urgency_text(h, d, bool(dd)) for h, d, dd in
                            zip(items["hours_to_due"], items["detail"], items["date_deadline"])]
    items["_overdue"] = items["overdue_hours"] > 0
    items = items.sort_values(
        ["severity", "_overdue", "overdue_hours", "hours_to_due", "impact_eur", "area", "reference"],
        ascending=[True, False, False, True, False, True, True], na_position="last")
    return items.reset_index(drop=True)[ISSUE_COLUMNS]


def area_issues(area: str, records: pd.DataFrame) -> pd.DataFrame:
    """Open issues for one area, e.g. area_issues("Inventory", enriched_products).

    Module pages use this on their filtered data; the dashboard combines all areas.
    Either way the same rules apply.
    """
    _, is_problem, build = AREA_RULES[area]
    if records.empty:
        return _finalise([])
    problems = records[is_problem(records)]
    return _finalise([build(problems)] if not problems.empty else [])


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
    return _finalise([area_issues(area, e[dataset]) for area, (dataset, _, _) in AREA_RULES.items()])


def status_of(items: pd.DataFrame) -> str:
    """'On track', 'Watch', 'At risk' or 'Critical', from the most severe issue."""
    if items.empty:
        return HEALTH_BY_SEVERITY[None]
    return HEALTH_BY_SEVERITY[items["severity"].min()]  # ordered: Critical < High < Medium


def area_health(items: pd.DataFrame) -> pd.DataFrame:
    """One status per area, taken from its most severe open issue."""
    rows = []
    for area in AREAS:
        in_area = items[items["area"] == area]
        counts = in_area["severity"].value_counts()
        rows.append({
            "area": area,
            "status": status_of(in_area),
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
