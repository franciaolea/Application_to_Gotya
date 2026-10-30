"""Daily Operations Briefing: the morning report an Operations Manager reads (or sends) at 08:00.

All content comes from src/briefing.py; this page only lays it out and offers a text export.
"""
import streamlit as st

from src import briefing, ui

bundle = ui.get_data()
company = bundle.metadata.get("company", "Operations")
b = briefing.build_briefing(bundle.frames, bundle.as_of, company)
report = briefing.to_markdown(b)

st.title("Daily Operations Briefing")
st.caption(f"{company} · {b.as_of:%A %d %B %Y, %H:%M}. Generated automatically from today's data, "
           "using the same rules as every other page.")

# --- Headline and status --------------------------------------------------------
if not b.escalations.empty:
    st.error(f"**{b.headline}**")
elif not b.attention.empty:
    st.warning(f"**{b.headline}**")
else:
    st.success(f"**{b.headline}**")

for col, (area, state) in zip(st.columns(len(b.area_status)), b.area_status.items()):
    with col:
        st.markdown(f"**{area}**<br>{ui.status_badge(state)}", unsafe_allow_html=True)

# --- Running well / delayed ------------------------------------------------------
left, right = st.columns(2)
with left, st.container(border=True):
    st.markdown("#### ✓ Running well")
    for line in b.running_well or ["Nothing to highlight today."]:
        st.markdown(f"- {line}")
with right, st.container(border=True):
    st.markdown("#### ⏱ Delayed")
    for line in b.delayed or ["Nothing is delayed."]:
        st.markdown(f"- {line}")

if b.insight:
    st.info(f"**Key insight:** {b.insight}", icon="💡")

# --- Escalations ------------------------------------------------------------------
st.subheader("Escalate today")
if b.escalations.empty:
    st.success("Nothing needs escalating today.")
else:
    st.caption("Critical issues. The owner keeps working the issue; the escalation contact is informed today.")
    st.dataframe(
        b.escalations[["area", "reference", "issue", "detail", "owner", "escalate_to"]],
        hide_index=True, width="stretch",
        column_config={"area": "Area", "reference": "Reference", "issue": st.column_config.TextColumn(
            "Issue", width="large"), "detail": "Detail", "owner": "Owner", "escalate_to": "Escalate to"})

# --- Immediate attention ------------------------------------------------------------
st.subheader("Requires immediate attention")
high = b.attention[b.attention["severity"] == "High"]
if high.empty:
    st.success("No other high-priority issues.")
else:
    st.caption("High-priority issues to resolve before end of day, by area.")
    st.dataframe(
        high[["area", "reference", "issue", "detail", "owner"]], hide_index=True, width="stretch",
        height=min(36 * (len(high) + 1) + 4, 600),
        column_config={"area": "Area", "reference": "Reference", "issue": st.column_config.TextColumn(
            "Issue", width="large"), "detail": "Detail", "owner": "Owner"})

# --- Responsibility -------------------------------------------------------------------
st.subheader("Who is responsible")
st.caption("Every open issue (including medium priority), per owner.")
st.dataframe(
    b.by_owner, hide_index=True, width="stretch",
    column_config={"owner": "Owner", "critical": "Critical", "high": "High", "medium": "Medium",
                   "total": "Total", "areas": "Areas"})

st.subheader("Action follow-ups due")
st.caption("Open actions from the Action Tracker that are due today or overdue.")
if b.follow_ups.empty:
    st.success("No action follow-ups due.")
else:
    st.dataframe(
        b.follow_ups, hide_index=True, width="stretch",
        column_config={"action_id": "ID", "module": "Area", "issue": st.column_config.TextColumn(
            "Action", width="large"), "owner": "Owner", "status": "Status",
            "due_date": st.column_config.DateColumn("Due", format=ui.DATE_FORMAT), "days_overdue": "Days overdue"})

# --- Export --------------------------------------------------------------------------------
st.subheader("Share the briefing")
st.download_button("Download briefing (Markdown)", report, file_name=f"daily_briefing_{b.as_of:%Y-%m-%d}.md",
                   mime="text/markdown")
with st.expander("Copy as text (for email or chat)"):
    st.caption("Use the copy icon in the top-right corner of the box.")
    st.code(report, language="markdown")
