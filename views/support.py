"""Support: open tickets, priorities, SLA deadlines, overdue tickets and owners.

Questions this page answers:
- Which tickets are past (or close to) their SLA deadline, and who owns them?
- Are we resolving tickets within the time we promise?
- Why are customers contacting us, and is a carrier creating avoidable contacts?
"""
import streamlit as st

from src import charts, config, metrics, ui

bundle = ui.get_data()
as_of = bundle.as_of
tickets = metrics.enrich_tickets(bundle.frames["tickets"], as_of)
shipments = metrics.enrich_shipments(bundle.frames["shipments"], as_of)

sla_text = ", ".join(f"{p} {h}h" for p, h in config.TICKET_SLA_HOURS.items())
st.title("Customer Support")
st.caption(f"Tickets as of {as_of:%d %B %Y, %H:%M}. SLA = time allowed to resolve a ticket: {sla_text}. "
           "The SLA clock pauses while we wait on the customer.")

# --- Filters (can be pre-set by the dashboard's drill-down buttons) ---------------
focus = ui.apply_navigation("views/support.py")
f1, f2, f3, f4, f5 = st.columns([1, 1, 1.2, 1, 0.9])
with f1:
    priorities = st.multiselect("Priority", list(config.TICKET_SLA_HOURS), placeholder="All", key="sup_priority")
with f2:
    statuses = st.multiselect("Status", ["Open", "In Progress", "Waiting on Customer", "Resolved", "Closed"],
                              placeholder="All", key="sup_status")
with f3:
    categories = st.multiselect("Category", sorted(tickets["category"].unique()), placeholder="All",
                                key="sup_category")
with f4:
    owners = st.multiselect("Owner", sorted(tickets["owner"].unique()), placeholder="All", key="sup_owner")
with f5:
    st.write("")  # aligns the toggle with the dropdowns
    only_overdue = st.toggle("Only past SLA", key="sup_past_sla")
view = metrics.filter_frame(tickets, {"priority": priorities, "status": statuses,
                                      "category": categories, "owner": owners})
if only_overdue:
    view = view[view["is_breached"]]
if focus:
    ui.focus_panel("views/support.py", focus, tickets[tickets["ticket_id"] == focus],
                   ["ticket_id", "priority", "subject", "category", "status", "sla_deadline", "owner", "order_id"],
                   {"sla_deadline": st.column_config.DatetimeColumn("SLA deadline", format=ui.DATETIME_FORMAT)})
if view.empty:
    st.info("No tickets match these filters.")
    st.stop()

issues = metrics.area_issues("Support", view)
kpi = metrics.support_kpis(view, as_of)
window = metrics.reporting_window(as_of, 7)

# --- KPIs ------------------------------------------------------------------
cards = st.columns(5)
with cards[0]:
    ui.kpi_card("Open tickets", f"{kpi['open']}", f"{kpi['paused']} waiting on the customer (SLA paused)",
                period="Now")
with cards[1]:
    ui.kpi_card("Past SLA", f"{kpi['breached']}",
                f"{kpi['urgent_breached']} Critical/High priority (escalate)", metrics.status_of(issues),
                period="Now")
with cards[2]:
    ui.kpi_card("At risk", f"{kpi['at_risk']}",
                f"SLA deadline within the next {config.TICKET_AT_RISK_HOURS} hours", period="Now")
with cards[3]:
    ui.kpi_card("Resolved within SLA", ui.fmt_pct(kpi["sla_compliance"]),
                f"{kpi['resolved']} tickets resolved · target {config.SLA_COMPLIANCE_TARGET:.0%}",
                period=ui.period_text(window))
with cards[4]:
    avg = kpi["avg_resolution_hours"]
    oldest = kpi["oldest_open_hours"]
    ui.kpi_card("Avg. time to resolve", "n/a" if avg is None else f"{avg:.0f}h",
                "Tickets resolved in the period" + ("" if oldest is None else
                                                    f"<br>oldest open ticket: {oldest / 24:.1f} days"),
                period=ui.period_text(window))

# --- SLA deadlines -----------------------------------------------------------
st.subheader("SLA deadlines")
open_view = view[view["is_open"]].copy()
if open_view.empty:
    st.success("No open tickets in this view.")
else:
    if kpi["urgent_breached"]:
        st.error(f"**{kpi['urgent_breached']} urgent ticket(s) are past their SLA** and must be escalated "
                 "to the support lead today.")
    elif kpi["breached"] or kpi["at_risk"]:
        st.warning(f"{kpi['breached']} ticket(s) past SLA and {kpi['at_risk']} at risk. "
                   "No urgent breaches.")
    else:
        st.success("All open tickets are within their SLA.")

    def time_left(row) -> str:
        if row["status"] in metrics.SLA_PAUSED_STATUSES:
            return "Paused"
        hours = row["hours_to_sla"]
        return f"{-hours:.0f}h overdue" if hours < 0 else f"{hours:.0f}h left"

    severity = issues.set_index("reference")["severity"]  # ordered: Critical < High < Medium
    open_view["severity"] = open_view["ticket_id"].map(severity)
    open_view["time_left"] = open_view.apply(time_left, axis=1)
    open_view["priority_rank"] = open_view["priority"].map(
        {p: i for i, p in enumerate(config.TICKET_SLA_HOURS)})
    st.caption("Every open ticket: most severe first, then the most overdue or closest deadline.")
    open_view = open_view.sort_values(["severity", "hours_to_sla", "priority_rank"], na_position="last")
    open_view["severity"] = open_view["severity"].astype(str).map(ui.SEVERITY_LABELS).fillna("")
    open_view["priority"] = open_view["priority"].map(ui.SEVERITY_LABELS)
    st.dataframe(
        open_view[[
            "severity", "ticket_id", "priority", "subject", "category", "status", "created_at",
            "sla_deadline", "time_left", "owner", "order_id"]],
        hide_index=True, width="stretch",
        column_config={
            "severity": st.column_config.TextColumn("Issue severity", help="How urgent the problem is now "
                                                    "(e.g. a High-priority ticket past its SLA is Critical)"),
            "ticket_id": "Ticket", "priority": "Ticket priority", "subject": "Subject",
            "category": "Category", "status": "Status", "owner": "Owner", "order_id": "Order",
            "created_at": st.column_config.DatetimeColumn("Opened", format=ui.DATETIME_FORMAT),
            "sla_deadline": st.column_config.DatetimeColumn("SLA deadline", format=ui.DATETIME_FORMAT),
            "time_left": "Time left",
        })

# --- Performance and root causes ---------------------------------------------
st.subheader("Performance and root causes")
left, right = st.columns(2)
with left, st.container(border=True):
    resolution = metrics.resolution_vs_sla(view)
    st.markdown("**Time to resolve vs SLA**")
    st.caption("Average hours to resolve each priority; the black tick is the SLA allowed.")
    st.plotly_chart(charts.resolution_vs_sla(resolution), theme=None, config={"displayModeBar": False})
    with st.expander("View data"):
        st.dataframe(resolution, hide_index=True, width="stretch")
with right, st.container(border=True):
    backlog = metrics.support_backlog(view)
    st.markdown("**Open tickets by priority and SLA state** · now")
    st.caption("'Paused' means waiting on the customer.")
    st.plotly_chart(charts.support_backlog(backlog), theme=None, config={"displayModeBar": False})
    with st.expander("View data"):
        st.dataframe(backlog, hide_index=True, width="stretch")

left, right = st.columns(2)
with left, st.container(border=True):
    reasons = metrics.contacts_by_category(view)
    st.markdown("**Why customers contact us**")
    st.caption("Tickets by contact reason. Open tickets in blue.")
    st.plotly_chart(charts.contacts_by_category(reasons), theme=None, config={"displayModeBar": False})
    with st.expander("View data"):
        st.dataframe(reasons, hide_index=True, width="stretch")
with right, st.container(border=True):
    by_carrier = metrics.delivery_contacts_by_carrier(view, shipments, as_of)
    st.markdown(f"**Delivery complaints per 100 parcels** · "
                f"{ui.period_text(metrics.reporting_window(as_of, 14))}")
    st.caption("'Where is my order' and damage tickets, linked to the carrier that shipped the order. "
               "A rate, so carriers of different sizes compare fairly.")
    if by_carrier["tickets"].sum() == 0:
        st.info("No delivery-related tickets in this view.")
    else:
        st.plotly_chart(charts.delivery_contacts_by_carrier(by_carrier), theme=None,
                        config={"displayModeBar": False})
        with st.expander("View data"):
            st.dataframe(by_carrier, hide_index=True, width="stretch")

# --- All tickets ----------------------------------------------------------------
with st.expander(f"All tickets in view ({len(view)})"):
    st.dataframe(
        view.sort_values("created_at", ascending=False)[[
            "ticket_id", "created_at", "customer_name", "order_id", "channel", "category", "subject",
            "priority", "status", "sla_deadline", "resolved_at", "met_sla", "owner"]],
        hide_index=True, width="stretch",
        column_config={
            "created_at": st.column_config.DatetimeColumn("Opened", format=ui.DATETIME_FORMAT),
            "sla_deadline": st.column_config.DatetimeColumn("SLA deadline", format=ui.DATETIME_FORMAT),
            "resolved_at": st.column_config.DatetimeColumn("Resolved", format=ui.DATETIME_FORMAT),
            "met_sla": st.column_config.CheckboxColumn("Within SLA"),
        })
    st.download_button("Download tickets (CSV)", view.to_csv(index=False),
                       file_name=f"tickets_{as_of:%Y-%m-%d}.csv", mime="text/csv")
