"""Executive Dashboard: the one-screen view of operational performance.

Reading order, top to bottom, matches how leadership consumes it:
1. Headline KPIs with each area's status   -> "how are we doing?"
2. Yesterday at a glance                    -> "what happened?"
3. Requires immediate attention             -> "what needs a decision now, and who owns it?"
4. Trends                                   -> "is it getting better or worse?"
"""
import pandas as pd
import streamlit as st

from src import actions as act
from src import assistant, charts, config, metrics, ui
from src.validation import ACTION_MODULES

bundle = ui.get_data()
as_of = bundle.as_of
company = bundle.metadata.get("company", "Operations")

st.title("Executive Dashboard")
st.caption(f"{company} · operational snapshot as of {as_of:%A %d %B %Y, %H:%M}")

# --- Filters ---------------------------------------------------------------
orders = bundle.frames["orders"]
f1, f2, f3 = st.columns([1.6, 2, 2])
with f1:
    period = st.radio("Period", [7, 14, 28], horizontal=True, format_func=lambda d: f"{d} days")
with f2:
    regions = st.multiselect("Region", sorted(orders["region"].dropna().unique()), placeholder="All regions")
with f3:
    channels = st.multiselect("Sales channel", sorted(orders["channel"].dropna().unique()),
                              placeholder="All channels")
st.markdown('<div class="section-note">The period affects orders, revenue and on-time delivery. '
            'Region and channel filter orders and shipments; inventory, payments and support are '
            'company-wide.</div>', unsafe_allow_html=True)

# --- Business logic (all rules live in src/metrics.py) -------------------------
frames = metrics.apply_order_filters(bundle.frames, regions, channels)
e = metrics.enrich_all(frames, as_of)
kpi = metrics.executive_kpis(e, as_of, period)
items = metrics.attention_items(e)
health = metrics.area_health(items).set_index("area")["status"]

# --- 1. Headline KPIs ------------------------------------------------------
cards = st.columns(6)
with cards[0]:
    previous = kpi["orders_previous"]
    if previous is not None and previous < 10:  # % changes on tiny numbers mislead ("+400%")
        comparison = f"{kpi['orders_processed']} vs {previous} in previous {period} days"
    else:
        comparison = f"{ui.fmt_change(kpi['orders_change'])} vs previous {period} days"
    ui.kpi_card("Orders processed", f"{kpi['orders_processed']:,}",
                f"{comparison}<br>{ui.fmt_eur(kpi['revenue'])} revenue")
with cards[1]:
    ui.kpi_card("Pending orders", f"{kpi['pending_orders']}",
                f"{kpi['stuck_orders']} waiting longer than 48h", health["Orders"])
with cards[2]:
    ui.kpi_card("Delayed shipments", f"{kpi['delayed_shipments']}",
                f"{kpi['shipment_exceptions']} carrier exceptions<br>"
                f"{ui.fmt_pct(kpi['on_time_rate'])} delivered on time", health["Shipping"])
with cards[3]:
    ui.kpi_card("Low inventory items", f"{kpi['low_stock_items']}",
                f"{kpi['out_of_stock_items']} out of stock", health["Inventory"])
with cards[4]:
    ui.kpi_card("Pending payouts", f"{kpi['pending_payouts']}",
                f"{ui.fmt_eur(kpi['receivable_eur'])} in · {ui.fmt_eur(kpi['payable_eur'])} out<br>"
                f"{kpi['overdue_payments']} past due date", health["Payments"])
with cards[5]:
    ui.kpi_card("Open support tickets", f"{kpi['open_tickets']}",
                f"{kpi['breached_tickets']} past SLA · {kpi['at_risk_tickets']} at risk<br>"
                f"{ui.fmt_pct(kpi['sla_compliance'])} resolved within SLA", health["Support"])

# Drill-down links under the cards that have a detail page: from "how many" to "which ones" in one click.
links = [None, None, ("views/shipping.py", "Shipping details"), ("views/inventory.py", "Inventory details"),
         ("views/payments.py", "Payment details"), ("views/support.py", "Support details")]
for col, link in zip(st.columns(6), links):
    if link:
        with col:
            st.page_link(link[0], label=f"{link[1]} →")

# --- AI Operations Assistant ----------------------------------------------------
st.subheader("AI Operations Assistant")
st.caption("Rule-based analysis of today's company-wide data (not affected by the filters above). "
           "No external AI service: every figure comes from the data and can be checked under Evidence.")
if "assistant_flash" in st.session_state:
    st.success(st.session_state.pop("assistant_flash"))

report = assistant.analyse(bundle.frames, as_of)
today = pd.Timestamp(as_of).normalize()
tracked = bundle.frames["actions"]
tracked = tracked[tracked["status"] != "Resolved"]

if not report.top:
    st.success("No significant operational problems detected in today's data.")
for rank, f in enumerate(report.top, 1):
    with st.container(border=True):
        badges = ui.severity_badge(f.severity) + (" " + ui.escalate_badge() if f.escalate else "")
        st.markdown(f"{badges}<br><b>#{rank} · {f.area} · {f.title}</b>", unsafe_allow_html=True)
        left, right = st.columns([1.1, 1])
        with left:
            st.markdown(f"**What is happening**  \n{f.problem}")
            if f.root_cause:
                st.markdown(f"**Root cause** · confidence: {f.confidence}  \n{f.root_cause}")
            else:
                st.markdown("**Root cause** · not determined  \nThe available data does not show why. "
                            + " ".join(f.data_gaps))
        with right:
            st.markdown("**Recommended actions**")
            st.markdown("\n".join(f"{i}. {a.text} *Owner: {a.owner_role}*" for i, a in enumerate(f.actions, 1)))
            if f.escalate:
                st.markdown(f":red[**Escalate today to {f.escalate_to}:**] {f.escalation_reason}")
        e1, e2 = st.columns([3, 1])
        with e1, st.expander(f"Evidence ({len(f.evidence)} data points), data gaps and score"):
            st.dataframe(pd.DataFrame([vars(ev) for ev in f.evidence]).rename(
                columns={"label": "Measure", "display": "Value", "source": "Source"}),
                hide_index=True, width="stretch")
            st.markdown("**Data gaps:** " + " ".join(f.data_gaps))
            p = f.score_parts
            st.caption(f"Impact score {f.impact_score:g} = severity {p['severity']:g} × scale {p['scale']:g} "
                       f"× urgency {p['urgency']:g}.")
        with e2:
            reference = f.references[0] if f.references else ""
            first = f.actions[0]
            existing = tracked[(tracked["reference_id"] == reference) & (tracked["issue"] == first.text[:200])]
            if len(existing):
                st.caption(f"Tracked as {existing.iloc[0]['action_id']}")
            elif st.button("Add to Action Tracker", key=f"track_{f.kind}"):
                updated, new_id = act.create_action(
                    bundle.frames["actions"], issue=first.text[:200], reference_id=reference,
                    module=f.area if f.area in ACTION_MODULES else "General", priority=f.severity,
                    due_date=today + pd.Timedelta(days=1 if f.escalate else 3), today=today,
                    comment=f"From the AI Operations Assistant. Suggested owner: {first.owner_role}.")
                act.save_actions(updated, bundle.directory)
                ui.reload_data()
                st.session_state["assistant_flash"] = f"{new_id} created in the Action Tracker."
                st.rerun()

more = report.findings[config.ASSISTANT_TOP_N:]
if more:
    st.caption("Also detected, lower impact: " + "; ".join(
        f"{f.title} (score {f.impact_score:g})" for f in more) + ".")
if report.areas_without_findings:
    st.caption("No significant problem detected in: " + ", ".join(report.areas_without_findings) + ".")
with st.expander("How the assistant works"):
    st.markdown(
        "1. **Detectors** check each area: carrier performance, stock-outs, support SLA, fulfilment backlog, "
        "blocked payments, workload.\n"
        "2. **Root-cause checks** run only on what the data can prove: *concentration* (a segment's share of "
        "problems vs its share of volume), *peer comparison* (e.g. the same countries served by other carriers), "
        "*cross-module links* (tickets → orders → parcels) and *demand change* (last 7 days vs previous 3 weeks). "
        "If no check passes, the cause is reported as not determined, with the data that would be needed.\n"
        "3. **Impact score** = severity (Critical 3, High 2, Medium 1) × scale (1-3, by customers or euros affected) "
        "× urgency (0.5-1.0). The top three are shown.\n"
        "4. **Recommendations** come from a fixed playbook per problem and cause, filled in with the evidence. "
        "Every number in the text is checked against the evidence by an automated test.")

# --- 2. Yesterday at a glance ---------------------------------------------
day = metrics.yesterday_overview(e, as_of)
st.subheader(f"Yesterday at a glance · {day['date']:%a %d %b}")
for col, (label, key) in zip(st.columns(6), [
        ("orders received", "orders_received"), ("shipments dispatched", "shipments_dispatched"),
        ("deliveries completed", "deliveries"), ("tickets opened", "tickets_opened"),
        ("tickets resolved", "tickets_resolved"), ("payments completed", "payments_completed")]):
    with col:
        ui.mini_stat(label, day[key])

# --- 3. Requires immediate attention -------------------------------------------
st.subheader("Requires immediate attention")
counts = items["severity"].value_counts()
critical, high = int(counts.get("Critical", 0)), int(counts.get("High", 0))
if critical:
    st.error(f"**{critical} critical issue(s) to escalate today** and {high} high-priority issue(s) "
             "to resolve before end of day.")
elif high:
    st.warning(f"No escalations needed. {high} high-priority issue(s) to resolve today.")
else:
    st.success("No critical or high-priority issues. Operations are running to plan.")

a1, a2 = st.columns([1, 3])
with a1:
    severities = st.multiselect("Severity", metrics.SEVERITIES, default=["Critical", "High"])
with a2:
    areas = st.multiselect("Area", metrics.AREAS, placeholder="All areas")
shown = items[items["severity"].isin(severities)]
if areas:
    shown = shown[shown["area"].isin(areas)]
shown = shown.assign(severity=shown["severity"].map(ui.SEVERITY_LABELS))
st.dataframe(
    shown, hide_index=True, width="stretch",
    height=min(36 * (len(shown) + 1) + 4, 560),  # show every row without an inner scrollbar (up to ~15)
    column_config={
        "severity": st.column_config.TextColumn("Severity", width="small"),
        "area": "Area", "reference": "Reference", "issue": st.column_config.TextColumn("Issue", width="large"),
        "owner": "Owner", "detail": "Detail",
        "escalate": st.column_config.CheckboxColumn("Escalate", help="Critical issues are escalated to leadership"),
    })
st.caption(f"Showing {len(shown)} of {len(items)} open issues. Severity rules are explained in the README.")

# --- 4. Trends -------------------------------------------------------------
st.subheader("Performance trends")
left, right = st.columns(2)

with left, st.container(border=True):
    trend = metrics.order_trend(e["orders"], as_of, period)
    st.markdown("**Orders per day**")
    st.caption("Bars show daily orders (excluding cancellations); the line smooths out daily noise.")
    st.plotly_chart(charts.orders_trend(trend), theme=None, config={"displayModeBar": False})
    with st.expander("View data"):
        st.dataframe(trend, hide_index=True, width="stretch")

with right, st.container(border=True):
    delivery = metrics.on_time_trend(e["shipments"], as_of, period)
    st.markdown("**On-time delivery rate**")
    st.caption("Share of parcels delivered by the promised date, over a rolling 7-day window.")
    st.plotly_chart(charts.on_time_trend(delivery), theme=None, config={"displayModeBar": False})
    with st.expander("View data"):
        st.dataframe(delivery, hide_index=True, width="stretch")

left, right = st.columns(2)
with left, st.container(border=True):
    backlog = metrics.support_backlog(e["tickets"])
    st.markdown("**Support backlog by priority**")
    st.caption("Open tickets and where each stands against its SLA deadline. "
               "'Paused' means waiting on the customer.")
    st.plotly_chart(charts.support_backlog(backlog), theme=None, config={"displayModeBar": False})
    with st.expander("View data"):
        st.dataframe(backlog, hide_index=True, width="stretch")

with right, st.container(border=True):
    load = metrics.issues_by_owner(items)
    st.markdown("**Open issues by owner**")
    st.caption("Who is carrying the operational load. Helps spot overloaded team members.")
    if load.empty:
        st.info("No open issues.")
    else:
        st.plotly_chart(charts.issues_by_owner(load), theme=None, config={"displayModeBar": False})
        with st.expander("View data"):
            st.dataframe(load, hide_index=True, width="stretch")
