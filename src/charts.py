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
