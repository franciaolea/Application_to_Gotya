"""Data Health: shows what was loaded and whether it passed validation."""
import streamlit as st

from src import config, ui
from src.validation import ERROR, SCHEMAS, issues_to_frame

bundle = ui.get_data()

st.title("Data Health")
st.caption("Every CSV is checked when it loads: required columns, blank values, valid dates and numbers, "
           "allowed status values, unique IDs, and links between files (e.g. every shipment belongs to a "
           "known order). Problems are listed here instead of being silently ignored.")

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
    found = [i for i in bundle.issues if i.dataset == name]
    status = "Critical" if any(i.severity == ERROR for i in found) else "Watch" if found else "On track"
    with col:
        ui.kpi_card(name.capitalize(), f"{len(df):,}",
                    f"rows in {config.DATASET_FILES[name]}<br>{len(found)} issue(s)", status)

if bundle.issues:
    st.subheader("Validation issues")
    st.dataframe(issues_to_frame(bundle.issues), width="stretch", hide_index=True)

st.subheader("Data preview")
tabs = st.tabs([name.capitalize() for name in bundle.frames])
for tab, (name, df) in zip(tabs, bundle.frames.items()):
    with tab:
        date_format = {col: st.column_config.DatetimeColumn(format=ui.DATETIME_FORMAT)
                       if df[col].dt.time.astype(str).ne("00:00:00").any()
                       else st.column_config.DateColumn(format=ui.DATE_FORMAT)
                       for col in SCHEMAS[name].date_columns if col in df.columns}
        st.dataframe(df, width="stretch", hide_index=True, column_config=date_format)

with st.expander("Dataset metadata"):
    st.write(f"Folder: `{bundle.directory.relative_to(config.PROJECT_ROOT)}`")
    st.json(bundle.metadata or {"info": "No metadata file found."})
