"""Shared Streamlit helpers: cached data loading, styling and the data-mode banner.

Pages call these instead of repeating code, so every page looks consistent and
always shows clearly whether it is displaying demo or real data.
"""
from __future__ import annotations

import streamlit as st

from src import config
from src.data_loader import DataBundle, load_all

CSS = """
<style>
.block-container {padding-top: 1.5rem;}
.data-banner {padding: 0.6rem 1rem; border-radius: 8px; font-size: 0.9rem; margin-bottom: 1rem;}
.data-banner.demo {background: #FEF3C7; border: 1px solid #F59E0B; color: #78350F;}
.data-banner.live {background: #DCFCE7; border: 1px solid #16A34A; color: #14532D;}
div[data-testid="stMetric"] {background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 10px;
                             padding: 0.8rem 1rem;}
</style>
"""


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
