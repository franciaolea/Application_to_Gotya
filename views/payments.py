"""Payments: pending payouts, outstanding transactions, status and responsible person.

Questions this page answers:
- How much money is owed to us, and how much do we owe?
- What is overdue or blocked, and who is chasing it?
- What falls due this week?
"""
import streamlit as st

from src import charts, config, metrics, ui

bundle = ui.get_data()
as_of = bundle.as_of
payments = metrics.enrich_payments(bundle.frames["payments"], as_of)

st.title("Payments")
st.caption(f"Transactions as of {as_of:%d %B %Y, %H:%M}. Incoming = money owed to us (marketplace payouts, "
           "card settlements). Outgoing = money we owe (supplier and carrier invoices, customer refunds). "
           f"Unpaid items {config.PAYMENT_ESCALATION_DAYS}+ days past due, or failed, are escalated.")

# --- Filters (can be pre-set by the dashboard's drill-down buttons) ---------------
focus = ui.apply_navigation("views/payments.py")
f1, f2, f3, f4, f5 = st.columns([1.2, 1, 1, 1, 0.9])
with f1:
    types = st.multiselect("Type", sorted(payments["type"].unique()), placeholder="All", key="pay_type")
with f2:
    directions = st.multiselect("Direction", ["Incoming", "Outgoing"], placeholder="Both", key="pay_direction")
with f3:
    statuses = st.multiselect("Status", ["Pending", "Processing", "On Hold", "Failed", "Paid"], placeholder="All",
                              key="pay_status")
with f4:
    owners = st.multiselect("Owner", sorted(payments["owner"].unique()), placeholder="All", key="pay_owner")
with f5:
    st.write("")  # aligns the toggle with the dropdowns
    only_outstanding = st.toggle("Only unpaid", key="pay_unpaid")
view = metrics.filter_frame(payments, {"type": types, "direction": directions,
                                       "status": statuses, "owner": owners})
if only_outstanding:
    view = view[view["is_outstanding"]]
if focus:
    ui.focus_panel("views/payments.py", focus, payments[payments["payment_id"] == focus],
                   ["payment_id", "type", "direction", "counterparty", "amount_eur", "status", "due_date",
                    "days_overdue", "owner"],
                   {"amount_eur": st.column_config.NumberColumn("Amount", format="euro"),
                    "due_date": st.column_config.DateColumn("Due", format=ui.DATE_FORMAT)})
if view.empty:
    st.info("No transactions match these filters.")
    st.stop()

issues = metrics.area_issues("Payments", view)
kpi = metrics.payment_kpis(view, as_of)

# --- KPIs ------------------------------------------------------------------
cards = st.columns(5)
with cards[0]:
    ui.kpi_card("Pending payouts", ui.fmt_eur(kpi["incoming_eur"]),
                f"{kpi['incoming_count']} incoming payments not yet received", period="Now")
with cards[1]:
    ui.kpi_card("Outstanding payables", ui.fmt_eur(kpi["outgoing_eur"]),
                f"{kpi['outgoing_count']} invoices and refunds not yet paid", period="Now")
overdue_ids = view.loc[view["is_overdue"], "payment_id"]
blocked_ids = view.loc[view["is_outstanding"] & view["status"].isin(["Failed", "On Hold"]), "payment_id"]
with cards[2]:  # each card's badge reflects only the items it counts
    ui.kpi_card("Past due date", f"{kpi['overdue_count']}",
                f"{ui.fmt_eur(kpi['overdue_eur'])} unpaid after its due date",
                metrics.status_of(issues[issues["reference"].isin(overdue_ids)]), period="Now")
with cards[3]:
    ui.kpi_card("On hold or failed", f"{kpi['blocked_count']}",
                f"{ui.fmt_eur(kpi['blocked_eur'])} blocked until someone decides",
                metrics.status_of(issues[issues["reference"].isin(blocked_ids)]), period="Now")
with cards[4]:
    ui.kpi_card("Due in the next 7 days", f"{kpi['due_soon_count']}",
                f"{ui.fmt_eur(kpi['due_soon_eur'])} to pay or receive", period="Today + 7 days")

# --- Items that need action ------------------------------------------------------
st.subheader("Requires follow-up")
needs_action = view[view["is_outstanding"] & (view["is_overdue"] | view["status"].isin(["Failed", "On Hold"]))]
if needs_action.empty:
    st.success("Nothing overdue, failed or on hold in this view.")
else:
    escalate = int(needs_action["needs_escalation"].sum())
    message = f"**{len(needs_action)} transaction(s) need follow-up**"
    message += f", **{escalate} to escalate** to the finance lead." if escalate else "."
    (st.error if escalate else st.warning)(message)
    severity = issues.set_index("reference")["severity"]
    table = needs_action.assign(severity=needs_action["payment_id"].map(severity)) \
        .sort_values(["severity", "days_overdue"], ascending=[True, False])
    table["severity"] = table["severity"].astype(str).map(ui.SEVERITY_LABELS)
    st.dataframe(
        table[["severity", "payment_id", "type", "direction", "counterparty", "amount_eur", "status",
               "due_date", "days_overdue", "owner"]],
        hide_index=True, width="stretch",
        column_config={
            "severity": "Severity", "payment_id": "Payment", "type": "Type", "direction": "Direction",
            "counterparty": "Counterparty", "status": "Status", "owner": "Owner",
            "amount_eur": st.column_config.NumberColumn("Amount", format="euro"),
            "due_date": st.column_config.DateColumn("Due", format=ui.DATE_FORMAT),
            "days_overdue": st.column_config.NumberColumn("Days overdue"),
        })

# --- Charts ----------------------------------------------------------------
st.subheader("Outstanding balances")
left, right = st.columns(2)
with left, st.container(border=True):
    buckets = metrics.payment_due_buckets(view, as_of)
    st.markdown("**When outstanding amounts fall due**")
    st.caption("A simple ageing view: anything to the left of 'Due within 7 days' is already late.")
    st.plotly_chart(charts.payment_due_buckets(buckets), theme=None, config={"displayModeBar": False})
    with st.expander("View data"):
        st.dataframe(buckets, hide_index=True, width="stretch")
with right, st.container(border=True):
    by_type = metrics.outstanding_by_type(view)
    st.markdown("**Outstanding by transaction type**")
    st.caption("Where the open money sits. Blue is owed to us, orange is owed by us.")
    if by_type.empty:
        st.info("Nothing outstanding in this view.")
    else:
        st.plotly_chart(charts.outstanding_by_type(by_type), theme=None, config={"displayModeBar": False})
        with st.expander("View data"):
            st.dataframe(by_type, hide_index=True, width="stretch")

# --- All transactions ----------------------------------------------------------
st.subheader("Transactions")
st.caption(f"{kpi['paid_30d_count']} payments ({ui.fmt_eur(kpi['paid_30d_eur'])}) completed in the "
           f"{ui.period_text(metrics.reporting_window(as_of, 30)).lower()}. "
           "Unpaid items first, earliest due date first.")
st.dataframe(
    view.sort_values(["is_outstanding", "due_date"], ascending=[False, True])[[
        "payment_id", "type", "direction", "counterparty", "reference", "amount_eur", "status",
        "due_date", "paid_date", "days_overdue", "owner"]],
    hide_index=True, width="stretch",
    column_config={
        "payment_id": "Payment", "type": "Type", "direction": "Direction", "counterparty": "Counterparty",
        "reference": "Order ref.", "status": "Status", "owner": "Owner",
        "amount_eur": st.column_config.NumberColumn("Amount", format="euro"),
        "due_date": st.column_config.DateColumn("Due", format=ui.DATE_FORMAT),
        "paid_date": st.column_config.DateColumn("Paid", format=ui.DATE_FORMAT),
        "days_overdue": st.column_config.NumberColumn("Days overdue"),
    })
st.download_button("Download transactions (CSV)", view.to_csv(index=False),
                   file_name=f"payments_{as_of:%Y-%m-%d}.csv", mime="text/csv")
