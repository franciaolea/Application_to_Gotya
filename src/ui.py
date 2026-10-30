"""Shared Streamlit helpers: cached data loading, styling and the data-mode banner.

Pages call these instead of repeating code, so every page looks consistent and
always shows clearly whether it is displaying demo or real data.
"""
from __future__ import annotations

from typing import Optional

import streamlit as st

from src import config
from src.data_loader import DataBundle, load_all

CSS = """
<style>
.block-container {padding-top: 3.5rem;}
.data-banner {padding: 0.6rem 1rem; border-radius: 8px; font-size: 0.9rem; margin-bottom: 1rem;}
.data-banner.demo {background: #FEF3C7; border: 1px solid #F59E0B; color: #78350F;}
.data-banner.live {background: #DCFCE7; border: 1px solid #16A34A; color: #14532D;}
div[data-testid="stMetric"] {background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 10px;
                             padding: 0.8rem 1rem;}
.kpi-card {background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 10px; padding: 0.9rem 1rem;
           height: 100%; min-height: 168px;}
.kpi-label {font-size: 0.8rem; color: #52514e; font-weight: 600; text-transform: uppercase;
            letter-spacing: 0.03em;}
.kpi-value {font-size: 2rem; font-weight: 700; color: #0b0b0b; line-height: 1.2; margin: 0.25rem 0;}
.kpi-caption {font-size: 0.82rem; color: #52514e; line-height: 1.4;}
.badge {display: inline-block; font-size: 0.72rem; font-weight: 600; padding: 0.1rem 0.5rem;
        border-radius: 999px; margin-bottom: 0.35rem; border: 1px solid;}
.mini-stat {font-size: 0.8rem; color: #52514e;}
.mini-stat b {display: block; font-size: 1.3rem; color: #0b0b0b;}
.section-note {font-size: 0.85rem; color: #52514e; margin-top: -0.5rem; margin-bottom: 0.75rem;}
</style>
"""

# Area status -> (icon, text colour, background, border). Always icon + label, never colour alone.
STATUS_STYLES = {
    "On track": ("✓", "#14532D", "#DCFCE7", "#0ca30c"),
    "Watch": ("●", "#713F12", "#FEF9C3", "#fab219"),
    "At risk": ("▲", "#7C2D12", "#FFEDD5", "#ec835a"),
    "Critical": ("✕", "#7F1D1D", "#FEE2E2", "#d03b3b"),
}
SEVERITY_LABELS = {"Critical": "🔴 Critical", "High": "🟠 High", "Medium": "🟡 Medium"}


@st.cache_data(show_spinner="Loading operational data...")
def _cached_load(mode: str) -> DataBundle:
    return load_all(mode=mode)


def get_data() -> DataBundle:
    """Load all datasets once and reuse them across pages and reruns.

    Streamlit reruns the whole script on every click; caching avoids re-reading
    and re-validating the CSV files each time.
    """
    return _cached_load(config.DATA_MODE)


def reload_data() -> None:
    _cached_load.clear()


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def data_banner(bundle: DataBundle) -> None:
    as_of = bundle.as_of.strftime("%d %b %Y, %H:%M")
    if bundle.is_demo:
        company = bundle.metadata.get("company", "a fictional company")
        html = (f'<div class="data-banner demo"><b>DEMO MODE: fictional sample data.</b> '
                f'{company} is not a real business. Snapshot as of {as_of}.</div>')
    else:
        html = f'<div class="data-banner live"><b>LIVE DATA</b> from {bundle.directory}. As of {as_of}.</div>'
    st.markdown(html, unsafe_allow_html=True)


def status_badge(status: str) -> str:
    icon, text, background, border = STATUS_STYLES[status]
    return (f'<span class="badge" style="color:{text};background:{background};border-color:{border}">'
            f'{icon} {status}</span>')


def kpi_card(label: str, value: str, caption: str = "", status: Optional[str] = None) -> None:
    """A KPI tile: label, big number, explanation and (optionally) the area's status."""
    badge = status_badge(status) if status else '<span class="badge" style="visibility:hidden">-</span>'
    st.markdown(
        f'<div class="kpi-card">{badge}<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value}</div><div class="kpi-caption">{caption}</div></div>',
        unsafe_allow_html=True)


def mini_stat(label: str, value) -> None:
    st.markdown(f'<div class="mini-stat"><b>{value}</b>{label}</div>', unsafe_allow_html=True)


def fmt_eur(value: float) -> str:
    """€950, €12.4k, €1.2M: short enough for a KPI tile."""
    if abs(value) >= 1_000_000:
        return f"€{value / 1_000_000:.1f}M"
    if abs(value) >= 1_000:
        return f"€{value / 1_000:.1f}k"
    return f"€{value:,.0f}"


def fmt_pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def fmt_change(value: Optional[float]) -> str:
    if value is None:
        return "no earlier data to compare"
    arrow = "▲" if value > 0 else "▼" if value < 0 else "■"
    return f"{arrow} {abs(value):.0%}"
