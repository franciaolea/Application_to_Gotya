"""Plotly chart builders with one consistent, restrained style.

Colour rules used across the app:
- SERIES colours identify things (an area, a series). Their order is a
  colour-blind-safe sequence, and each area always keeps the same colour.
- STATUS colours (green/amber/orange/red) only ever mean good -> critical,
  and are always shown together with a text label, never on their own.
- One chart = one y-axis. Two measures with different units get two charts.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from src import config
from src.metrics import AREAS

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
NEUTRAL = "#c3c2b7"
INK, INK_SECONDARY, MUTED = "#1F2937", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
PAGE_BG = "#F7F8FA"  # used as the thin gap between stacked segments

AREA_COLORS = dict(zip(AREAS, SERIES))  # colour follows the area, never its rank


def _style(fig: go.Figure, height: int = 300, y_format: str = "") -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=16, t=36, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif", size=12, color=INK_SECONDARY),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title_text="", traceorder="normal"),
        hoverlabel=dict(bgcolor="white", font_color=INK, bordercolor=GRID),
        bargap=0.35,
        barcornerradius=4,
    )
    # automargin makes room for long tick labels (owner names, percentages) instead of clipping them.
    fig.update_xaxes(showgrid=False, linecolor=AXIS, tickcolor=AXIS, ticks="outside", automargin=True)
    fig.update_yaxes(gridcolor=GRID, gridwidth=1, zeroline=False, showline=False, tickformat=y_format,
                     automargin=True)
    return fig


def orders_trend(trend: pd.DataFrame) -> go.Figure:
    """Daily orders (bars) with the 7-day average (line) on the same scale."""
    fig = go.Figure()
    fig.add_bar(
        x=trend["date"], y=trend["orders"], name="Orders per day", marker_color=SERIES[0],
        customdata=trend["revenue"],
        hovertemplate="%{y} orders · €%{customdata:,.0f} revenue<extra></extra>")
    fig.add_scatter(
        x=trend["date"], y=trend["orders_7d_avg"], name="7-day average", mode="lines",
        line=dict(color=SERIES[1], width=2),
        hovertemplate="7-day avg %{y:.1f}<extra></extra>")
    fig.update_layout(hovermode="x unified")
    fig.update_xaxes(tickformat="%d %b")
    return _style(fig)


def on_time_trend(trend: pd.DataFrame) -> go.Figure:
    """Rolling 7-day on-time delivery rate against the target."""
    target = config.ON_TIME_DELIVERY_TARGET
    rates = trend["on_time_rate_7d"].dropna()
    low = min(rates.min() if len(rates) else target, target) - 0.05
    fig = go.Figure()
    fig.add_scatter(
        x=trend["date"], y=trend["on_time_rate_7d"], name="On-time rate (7-day)", mode="lines",
        line=dict(color=SERIES[0], width=2),
        customdata=trend[["on_time", "delivered"]],
        hovertemplate="%{y:.0%} on time<extra></extra>")
    fig.add_hline(y=target, line=dict(color=MUTED, width=1, dash="dot"),
                  annotation_text=f"Target {target:.0%}", annotation_position="top left",
                  annotation_font_color=INK_SECONDARY)
    if len(rates):  # label only the latest value
        fig.add_annotation(x=trend.loc[rates.index[-1], "date"], y=rates.iloc[-1],
                           text=f"<b>{rates.iloc[-1]:.0%}</b>", showarrow=False, xanchor="left",
                           xshift=6, font=dict(color=INK))
    fig.update_layout(hovermode="x unified", showlegend=False)
    fig.update_xaxes(tickformat="%d %b")
    fig.update_yaxes(range=[max(low, 0), 1.02])
    return _style(fig, y_format=".0%")


SLA_STATE_COLORS = {"Breached": STATUS["critical"], "At risk": STATUS["serious"],
                    "Within SLA": STATUS["good"], "Paused": NEUTRAL}


def support_backlog(backlog: pd.DataFrame) -> go.Figure:
    """Open tickets per priority, stacked by SLA state (status colours + legend labels)."""
    data = backlog.iloc[::-1]  # Critical at the top
    fig = go.Figure()
    for state, color in SLA_STATE_COLORS.items():
        fig.add_bar(
            y=data["priority"], x=data[state], name=state, orientation="h",
            marker=dict(color=color, line=dict(color=PAGE_BG, width=2)),
            hovertemplate=f"%{{y}}: %{{x}} {state.lower()}<extra></extra>")
    fig.update_layout(barmode="stack")
    fig.update_xaxes(showgrid=True, gridcolor=GRID, dtick=1, title_text="Open tickets")
    fig.update_yaxes(showgrid=False)
    return _style(fig, height=260)


def issues_by_owner(table: pd.DataFrame) -> go.Figure:
    """Open issues per owner, stacked by area: who is carrying the load."""
    owners = table["owner"].drop_duplicates().tolist()  # already sorted, smallest first
    fig = go.Figure()
    for area in AREAS:
        rows = table[table["area"] == area]
        if rows.empty:
            continue
        fig.add_bar(
            y=rows["owner"], x=rows["issues"], name=area, orientation="h",
            marker=dict(color=AREA_COLORS[area], line=dict(color=PAGE_BG, width=2)),
            hovertemplate=f"%{{y}}: %{{x}} {area.lower()} issue(s)<extra></extra>")
    fig.update_layout(barmode="stack")
    fig.update_xaxes(showgrid=True, gridcolor=GRID, dtick=1, title_text="Open issues")
    fig.update_yaxes(showgrid=False, categoryorder="array", categoryarray=owners)
    return _style(fig, height=max(260, 28 * len(owners) + 80))


# --- Inventory ---------------------------------------------------------------

# Problems get status colours; healthy items stay neutral so problems stand out.
STOCK_STATUS_COLORS = {"Out of Stock": STATUS["critical"], "Critical": STATUS["serious"],
                       "Low": STATUS["warning"], "Healthy": NEUTRAL}


def stock_vs_minimum(products: pd.DataFrame) -> go.Figure:
    """Stock on hand per product (bars) against its minimum (black tick). Worst first."""
    data = products.assign(cover=products["stock_on_hand"] / products["min_stock"]) \
        .sort_values("cover", ascending=False)  # plotly draws the first row at the bottom
    fig = go.Figure()
    for status, color in STOCK_STATUS_COLORS.items():
        rows = data[data["stock_status"] == status]
        if rows.empty:
            continue
        fig.add_bar(
            y=rows["product_name"], x=rows["stock_on_hand"], name=status, orientation="h",
            marker=dict(color=color, line=dict(color=PAGE_BG, width=1)),
            customdata=rows[["min_stock", "sku"]],
            hovertemplate="%{customdata[1]} · %{x} in stock (minimum %{customdata[0]})<extra></extra>")
    fig.add_scatter(
        y=data["product_name"], x=data["min_stock"], name="Minimum stock", mode="markers",
        marker=dict(symbol="line-ns", size=14, line=dict(width=2, color=INK)),
        hovertemplate="Minimum %{x}<extra></extra>")
    out = data[data["stock_on_hand"] <= 0]
    if not out.empty:  # a zero-length bar is invisible, so say it in words
        fig.add_scatter(y=out["product_name"], x=[0] * len(out), mode="text", text=["OUT OF STOCK"] * len(out),
                        textposition="middle right", textfont=dict(color=STATUS["critical"], size=11),
                        showlegend=False, hoverinfo="skip")
    fig.update_layout(barmode="overlay", bargap=0.3)
    fig.update_xaxes(showgrid=True, gridcolor=GRID, title_text="Units")
    fig.update_yaxes(showgrid=False, categoryorder="array", categoryarray=list(data["product_name"]))
    return _style(fig, height=max(280, 24 * len(data) + 100))


# --- Shipping ----------------------------------------------------------------

def carrier_on_time(performance: pd.DataFrame) -> go.Figure:
    """On-time delivery rate per carrier against the target."""
    target = config.ON_TIME_DELIVERY_TARGET
    data = performance.dropna(subset=["on_time_rate"])
    fig = go.Figure()
    fig.add_bar(
        x=data["carrier"], y=data["on_time_rate"], marker_color=SERIES[0], name="On-time rate",
        text=data["on_time_rate"].map("{:.0%}".format), textposition="inside", insidetextanchor="end",
        textfont=dict(color="white", size=13),
        customdata=data[["on_time", "delivered"]],
        hovertemplate="%{x}: %{y:.0%} on time (%{customdata[0]} of %{customdata[1]} parcels)<extra></extra>")
    fig.add_hline(y=target, line=dict(color=MUTED, width=1, dash="dot"),
                  annotation_text=f"Target {target:.0%}", annotation_position="top left",
                  annotation_font_color=INK_SECONDARY)
    fig.update_layout(showlegend=False, bargap=0.5, uniformtext=dict(mode="show", minsize=11))
    fig.update_yaxes(range=[0, 1.05])
    return _style(fig, y_format=".0%")


LATENESS_COLORS = {"On schedule": STATUS["good"], "1-2 days late": STATUS["warning"],
                   "3+ days late": STATUS["critical"]}


def open_shipments_by_lateness(table: pd.DataFrame) -> go.Figure:
    """Parcels still travelling per carrier, split by how late they are."""
    fig = go.Figure()
    for bucket, color in LATENESS_COLORS.items():
        fig.add_bar(
            y=table["carrier"], x=table[bucket], name=bucket, orientation="h",
            marker=dict(color=color, line=dict(color=PAGE_BG, width=2)),
            hovertemplate=f"%{{y}}: %{{x}} {bucket.lower()}<extra></extra>")
    fig.update_layout(barmode="stack")
    fig.update_xaxes(showgrid=True, gridcolor=GRID, title_text="Open shipments")
    fig.update_yaxes(showgrid=False, autorange="reversed")
    return _style(fig)


# --- Payments ------------------------------------------------------------------

DIRECTION_COLORS = {"Incoming": SERIES[0], "Outgoing": SERIES[1]}
DIRECTION_LABELS = {"Incoming": "Owed to us", "Outgoing": "We owe"}


def payment_due_buckets(table: pd.DataFrame) -> go.Figure:
    """Outstanding amounts by when they fall due, money in vs money out side by side."""
    fig = go.Figure()
    for direction, color in DIRECTION_COLORS.items():
        rows = table[table["direction"] == direction]
        fig.add_bar(
            x=rows["bucket"], y=rows["amount_eur"], name=DIRECTION_LABELS[direction], marker_color=color,
            customdata=rows["count"],
            hovertemplate="%{x}: €%{y:,.0f} across %{customdata} item(s)<extra></extra>")
    fig.update_layout(barmode="group", bargap=0.35, bargroupgap=0.08)
    fig.update_yaxes(tickprefix="€", tickformat=",.0f")
    return _style(fig)


def outstanding_by_type(table: pd.DataFrame) -> go.Figure:
    """Outstanding amount per transaction type, coloured by direction."""
    fig = go.Figure()
    for direction, color in DIRECTION_COLORS.items():
        rows = table[table["direction"] == direction]
        if rows.empty:
            continue
        fig.add_bar(
            y=rows["type"], x=rows["amount_eur"], name=DIRECTION_LABELS[direction], orientation="h",
            marker_color=color, customdata=rows["count"],
            hovertemplate="%{y}: €%{x:,.0f} (%{customdata} item(s))<extra></extra>")
    fig.update_xaxes(showgrid=True, gridcolor=GRID, tickprefix="€", tickformat=",.0f")
    fig.update_yaxes(showgrid=False, categoryorder="array", categoryarray=list(table["type"]))
    return _style(fig)


# --- Support -------------------------------------------------------------------

def contacts_by_category(table: pd.DataFrame) -> go.Figure:
    """Tickets per contact reason; open ones highlighted, resolved ones in grey."""
    fig = go.Figure()
    for column, color in (("Open", SERIES[0]), ("Resolved", NEUTRAL)):
        fig.add_bar(
            y=table["category"], x=table[column], name=column, orientation="h",
            marker=dict(color=color, line=dict(color=PAGE_BG, width=2)),
            hovertemplate=f"%{{y}}: %{{x}} {column.lower()}<extra></extra>")
    fig.update_layout(barmode="stack")
    fig.update_xaxes(showgrid=True, gridcolor=GRID, title_text="Tickets")
    fig.update_yaxes(showgrid=False, categoryorder="array", categoryarray=list(table["category"]))
    return _style(fig)


def delivery_contacts_by_carrier(table: pd.DataFrame) -> go.Figure:
    """Delivery complaints per 100 parcels shipped, per carrier."""
    data = table.dropna(subset=["per_100_shipments"])
    fig = go.Figure()
    fig.add_bar(
        x=data["carrier"], y=data["per_100_shipments"], marker_color=SERIES[0],
        text=data["per_100_shipments"].map("{:.0f}".format), textposition="inside", insidetextanchor="end",
        textfont=dict(color="white", size=13), customdata=data[["tickets", "shipped"]],
        hovertemplate="%{x}: %{y:.1f} per 100 parcels (%{customdata[0]} tickets, "
                      "%{customdata[1]} parcels)<extra></extra>")
    fig.update_layout(showlegend=False, bargap=0.5)
    fig.update_yaxes(title_text="Tickets per 100 parcels")
    return _style(fig)


def resolution_vs_sla(table: pd.DataFrame) -> go.Figure:
    """Average hours to resolve (bars) against the SLA allowed (black tick), per priority."""
    fig = go.Figure()
    fig.add_bar(
        x=table["priority"], y=table["avg_hours"], name="Average time to resolve", marker_color=SERIES[0],
        customdata=table[["resolved", "met_sla"]],
        hovertemplate="%{x}: %{y:.1f}h on average · %{customdata[1]:.0%} within SLA "
                      "(%{customdata[0]} tickets)<extra></extra>")
    fig.add_scatter(
        x=table["priority"], y=table["sla_hours"], name="SLA allowed", mode="markers",
        marker=dict(symbol="line-ew", size=40, line=dict(width=2, color=INK)),
        hovertemplate="SLA %{y}h<extra></extra>")
    fig.update_layout(bargap=0.5)
    fig.update_yaxes(title_text="Hours", ticksuffix="h")
    return _style(fig)
