"""Data Health: shows what was loaded and whether it passed validation."""
import streamlit as st

from src import ui
from src.validation import ERROR, issues_to_frame

bundle = ui.get_data()

st.title("Data Health")
st.caption("Every CSV is validated on load. Problems are listed here instead of being silently ignored.")

errors = sum(issue.severity == ERROR for issue in bundle.issues)
warnings = len(bundle.issues) - errors
if errors:
    st.error(f"{errors} data error(s) found. Affected values appear blank in the dashboards.")
elif warnings:
    st.warning(f"{warnings} warning(s) found. The data is usable but worth checking.")
else:
    st.success("All datasets passed validation.")

cols = st.columns(len(bundle.frames))
for col, (name, df) in zip(cols, bundle.frames.items()):
    col.metric(name.capitalize(), f"{len(df):,} rows")

if bundle.issues:
    st.subheader("Validation issues")
    st.dataframe(issues_to_frame(bundle.issues), width="stretch", hide_index=True)

st.subheader("Raw data preview")
tabs = st.tabs([name.capitalize() for name in bundle.frames])
for tab, df in zip(tabs, bundle.frames.values()):
    with tab:
        st.dataframe(df, width="stretch", hide_index=True)

with st.expander("Dataset metadata"):
    st.write(f"Folder: `{bundle.directory}`")
    st.json(bundle.metadata or {"info": "No metadata file found."})
