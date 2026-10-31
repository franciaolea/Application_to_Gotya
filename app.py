"""Operations Control Tower: application entry point.

Run with:  streamlit run app.py

This file only sets up the page, the shared banner and the navigation menu.
Each screen lives in its own file under views/.
"""
import streamlit as st

from src import ui

st.set_page_config(page_title="Operations Control Tower", page_icon="assets/logo_icon.png", layout="wide")
# Logo slot and browser tab: the emblem only (Streamlit shows logos at ~32px, too small for text).
st.logo("assets/logo_icon.png", size="large")
ui.inject_css()

bundle = ui.get_data()

with st.sidebar:
    st.image("assets/logo.png", width=180)  # the full logo, readable, under the menu
    st.caption("Showing fictional demo data" if bundle.is_demo else "Showing live data")
    if st.button("Reload data", width="stretch"):
        ui.reload_data()
        st.rerun()

ui.data_banner(bundle)

pages = {
    "Overview": [
        st.Page("views/executive_dashboard.py", title="Executive Dashboard", icon="📊", default=True),
        st.Page("views/daily_briefing.py", title="Daily Briefing", icon="☀️"),
        st.Page("views/action_tracker.py", title="Action Tracker", icon="✅"),
    ],
    "Operations": [
        st.Page("views/inventory.py", title="Inventory", icon="📦"),
        st.Page("views/shipping.py", title="Shipping", icon="🚚"),
        st.Page("views/payments.py", title="Payments", icon="💳"),
        st.Page("views/support.py", title="Support", icon="🎧"),
    ],
    "System": [
        st.Page("views/data_health.py", title="Data Health", icon="🩺"),
    ],
}
st.navigation(pages).run()
