"""Action Tracker: update status, assign owners, add comments, record resolution dates.

All rules live in src/actions.py. This page collects input, calls those functions,
and shows their error messages if a change breaks a rule. Nothing is saved
unless it passes validation.
"""
import pandas as pd
import streamlit as st

from src import actions as act
from src import metrics, ui
from src.validation import ACTION_MODULES, ACTION_STATUSES, PRIORITIES

bundle = ui.get_data()
today = pd.Timestamp(bundle.as_of).normalize()
actions = bundle.frames["actions"]
owners = act.known_owners(bundle.frames)

st.title("Action Tracker")
st.caption(f"Who is doing what to resolve each issue. Today = {today:%d %B %Y}"
           f"{' (demo snapshot date)' if bundle.is_demo else ''}. Changes are saved to "
           f"`{bundle.directory.name}/actions.csv` after validation.")

if "action_flash" in st.session_state:
    st.success(st.session_state.pop("action_flash"))


def save(updated: pd.DataFrame, message: str) -> None:
    try:
        act.save_actions(updated, bundle.directory)
    except act.ActionError as error:
        st.error(str(error))
        return
    ui.reload_data()
    st.session_state["action_flash"] = message
    st.rerun()


# --- Summary --------------------------------------------------------------------
s = act.summary(actions, today)
cards = st.columns(5)
with cards[0]:
    ui.kpi_card("Open", f"{s['open']}", f"{s['unassigned']} without an owner",
                "Watch" if s["unassigned"] else None)
with cards[1]:
    ui.kpi_card("In progress", f"{s['in_progress']}", "Being worked on")
with cards[2]:
    ui.kpi_card("Blocked", f"{s['blocked']}", "Waiting on someone else, see comments",
                "At risk" if s["blocked"] else "On track")
with cards[3]:
    ui.kpi_card("Overdue", f"{s['overdue']}", f"{s['due_today']} more due today",
                "Critical" if s["overdue"] else "On track")
with cards[4]:
    ui.kpi_card("Resolved", f"{s['resolved_7d']}", "In the last 7 days")

# --- Action list (editable) -----------------------------------------------------------
st.subheader("Actions")
f1, f2, f3, f4 = st.columns(4)
with f1:
    statuses = st.multiselect("Status", ACTION_STATUSES, default=["Open", "In Progress", "Blocked"])
with f2:
    owner_filter = st.multiselect("Owner", owners, placeholder="All")
with f3:
    modules = st.multiselect("Area", ACTION_MODULES, placeholder="All")
with f4:
    priorities = st.multiselect("Priority", PRIORITIES, placeholder="All")

view = metrics.filter_frame(act.enrich_actions(actions, today),
                            {"status": statuses, "owner": owner_filter, "module": modules,
                             "priority": priorities})
view = view.sort_values(["is_overdue", "due_date"], ascending=[False, True])
columns = ["action_id", "module", "reference_id", "issue", "owner", "priority", "status", "due_date",
           "days_overdue", "created_date", "resolution_date", "comments"]

if view.empty:
    st.info("No actions match these filters.")
else:
    st.caption("Edit owner, priority, status or due date directly in the table, then click **Save changes**. "
               "Resolution dates are recorded automatically. To add a comment (required when blocking an "
               "action), use the form below.")
    edited = st.data_editor(
        view[columns], hide_index=True, width="stretch", key="action_editor",
        disabled=[c for c in columns if c not in act.EDITABLE_FIELDS],
        column_config={
            "action_id": "ID", "module": "Area", "reference_id": "Reference",
            "issue": st.column_config.TextColumn("Issue", width="medium"),
            "owner": st.column_config.SelectboxColumn("Owner", options=owners),
            "priority": st.column_config.SelectboxColumn("Priority", options=PRIORITIES, required=True),
            "status": st.column_config.SelectboxColumn("Status", options=ACTION_STATUSES, required=True),
            "due_date": st.column_config.DateColumn("Due", format="DD MMM YYYY"),
            "days_overdue": st.column_config.NumberColumn("Days overdue"),
            "created_date": st.column_config.DateColumn("Created", format="DD MMM"),
            "resolution_date": st.column_config.DateColumn("Resolved on", format="DD MMM"),
            "comments": st.column_config.TextColumn("Comments", width="medium"),
        })
    changes = act.changes_between(view[columns], edited)
    if st.button(f"Save changes ({len(changes)})", type="primary", disabled=not changes):
        updated = actions
        try:
            for action_id, fields in changes:
                updated = act.update_action(updated, action_id, today=today, **fields)
        except act.ActionError as error:
            st.error(f"{action_id}: {error} Nothing was saved.")
        else:
            save(updated, f"Saved changes to {len(changes)} action(s).")

# --- Update one action with a comment -------------------------------------------------
left, right = st.columns(2)
with left, st.container(border=True):
    st.markdown("**Update an action**")
    with st.form("update_action", clear_on_submit=True):
        options = actions.sort_values("action_id")["action_id"].tolist()
        labels = actions.set_index("action_id")["issue"].to_dict()
        action_id = st.selectbox("Action", options, format_func=lambda a: f"{a} · {labels[a]}")
        new_status = st.selectbox("Status", ["(no change)"] + ACTION_STATUSES)
        new_owner = st.selectbox("Owner", ["(no change)"] + owners)
        comment = st.text_area("Comment", placeholder="What happened, what's next, what it's blocked by…")
        if st.form_submit_button("Update action", type="primary"):
            try:
                updated = act.update_action(
                    actions, action_id, today=today,
                    status=None if new_status == "(no change)" else new_status,
                    owner=None if new_owner == "(no change)" else new_owner,
                    comment=comment)
            except act.ActionError as error:
                st.error(str(error))
            else:
                save(updated, f"{action_id} updated.")

# --- Create an action, optionally from an open issue ------------------------------------
with right, st.container(border=True):
    st.markdown("**New action**")
    items = metrics.attention_items(metrics.enrich_all(bundle.frames, bundle.as_of))
    untracked = act.untracked_issues(items[items["severity"].isin(["Critical", "High"])], actions)
    issue_options = ["(none - describe it below)"] + untracked.index.tolist()

    def describe(i):
        if isinstance(i, str):
            return i
        r = untracked.loc[i]
        return f"[{r['severity']}] {r['area']} {r['reference']}: {r['issue']} ({r['owner']})"

    with st.form("new_action", clear_on_submit=True):
        linked = st.selectbox(f"Link to an open issue ({len(untracked)} Critical/High issues have no action yet)",
                              issue_options, format_func=describe)
        issue_text = st.text_input("Action", placeholder="e.g. Call carrier to locate parcel")
        c1, c2 = st.columns(2)
        module = c1.selectbox("Area", ACTION_MODULES, help="Ignored when linked to an issue")
        priority = c2.selectbox("Priority", PRIORITIES, index=2, help="Linked issues use their severity")
        owner = c1.selectbox("Owner", ["(issue owner / unassigned)"] + owners)
        due = c2.date_input("Due date", value=(today + pd.Timedelta(days=2)).date(), min_value=today.date())
        first_comment = st.text_input("Comment (optional)")
        if st.form_submit_button("Create action", type="primary"):
            issue = untracked.loc[linked] if not isinstance(linked, str) else None
            chosen_owner = "" if owner.startswith("(") else owner
            try:
                updated, new_id = act.create_action(
                    actions,
                    issue=issue_text or (f"{issue['issue']}" if issue is not None else ""),
                    module=str(issue["area"]) if issue is not None else module,
                    priority=str(issue["severity"]) if issue is not None else priority,
                    owner=chosen_owner or (issue["owner"] if issue is not None else ""),
                    reference_id=issue["reference"] if issue is not None else "",
                    due_date=due, today=today, comment=first_comment)
            except act.ActionError as error:
                st.error(str(error))
            else:
                save(updated, f"{new_id} created.")

# --- Export and demo controls --------------------------------------------------------------
st.download_button("Download actions (CSV)", act.to_text(actions).to_csv(index=False),
                   file_name=f"actions_{today:%Y-%m-%d}.csv", mime="text/csv")
if bundle.is_demo:
    with st.expander("Demo controls"):
        st.caption("Restore the original demo actions, e.g. before presenting. Your demo changes are discarded.")
        if st.button("Reset demo actions"):
            act.reset_demo_actions(bundle.directory, bundle.metadata)
            ui.reload_data()
            st.session_state["action_flash"] = "Demo actions restored."
            st.rerun()
