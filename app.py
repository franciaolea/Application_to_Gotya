"""Operations Control Tower: application entry point.

Run with:  streamlit run app.py

This file only sets up the page, the shared banner and the navigation menu.
Each screen lives in its own file under views/.
"""
import streamlit as st

from src import ui

st.set_page_config(page_title="Operations Control Tower", page_icon="🛰️", layout="wide")
ui.inject_css()

bundle = ui.get_data()

with st.sidebar:
    st.markdown("### 🛰️ Operations Control Tower")
    st.caption("Demo data" if bundle.is_demo else "Live data")
    if st.button("Reload data", width="stretch"):
        ui.reload_data()
        st.rerun()

ui.data_banner(bundle)

pages = {
    "System": [
        st.Page("views/data_health.py", title="Data Health", icon="🩺", default=True),
    ],
}
st.navigation(pages).run()
