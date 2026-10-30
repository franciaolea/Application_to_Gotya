"""Executive Dashboard: the one-screen view of operational performance.

Reading order, top to bottom, matches how leadership consumes it:
1. Headline KPIs with status and period     -> "how are we doing?"
2. Requires immediate attention (top 5)      -> "what needs a decision now, who owns it, is it handled?"
3. AI Operations Assistant                   -> "why is it happening and what should we do?"
4. Yesterday at a glance                     -> "what happened?"
5. Trends                                    -> "is it getting better or worse?"
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
st.markdown('<div class="section-note">"Last N days" means the N complete days before the snapshot '
            '(this morning is not included). The period affects orders, revenue, on-time delivery and SLA '
            'compliance; cards marked "Now" show the current situation. Region and channel filter orders and '
            'shipments; inventory, payments and support are company-wide.</div>', unsafe_allow_html=True)

# --- Business logic (all rules live in src/metrics.py) -------------------------
frames = metrics.apply_order_filters(bundle.frames, regions, channels)
e = metrics.enrich_all(frames, as_of)
kpi = metrics.executive_kpis(e, as_of, period)
items = metrics.attention_items(e)
health = metrics.area_health(items).set_index("area")["status"]

# --- 1. Headline KPIs ------------------------------------------------------
window = kpi["window"]
period_label = ui.period_text(window)
# Stock-out risk needs sales from all orders, not just the filtered region/channel.
demand = metrics.add_demand(e["products"], bundle.frames["orders"], as_of)
sell_out_soon = int((demand["stockout_risk"] & (demand["stock_on_hand"] > 0)).sum())

cards = st.columns(6)
with cards[0]:
    previous = kpi["orders_previous"]
    previous_label = metrics.previous_window(window).label
    if previous is not None and previous < 10:  # % changes on tiny numbers mislead ("+400%")
        comparison = f"{kpi['orders_processed']} vs {previous} in {previous_label}"
    else:
        comparison = f"{ui.fmt_change(kpi['orders_change'])} vs {previous_label}"
    ui.kpi_card("Orders processed", f"{kpi['orders_processed']:,}",
                f"Orders placed, excl. cancelled<br>{comparison} · {ui.fmt_eur(kpi['revenue'])} revenue",
                period=period_label)
with cards[1]:
    ui.kpi_card("Pending orders", f"{kpi['pending_orders']}",
                f"Placed but not yet shipped<br>{kpi['stuck_orders']} waiting longer than 48h",
                health["Orders"], period="Now")
with cards[2]:
    ui.kpi_card("Delayed shipments", f"{kpi['delayed_shipments']}",
                f"Past the promised date · {kpi['shipment_exceptions']} carrier exceptions<br>"
                f"{ui.fmt_pct(kpi['on_time_rate'])} delivered on time in the last {window.days} days",
                health["Shipping"], period="Now")
with cards[3]:
    ui.kpi_card("Low inventory items", f"{kpi['low_stock_items']}",
                f"At or below minimum · {kpi['out_of_stock_items']} out of stock<br>"
                f"{sell_out_soon} will sell out before a restock arrives", health["Inventory"], period="Now")
with cards[4]:
    ui.kpi_card("Pending payouts", ui.fmt_eur(kpi["pending_payouts_eur"]),
                f"{kpi['pending_payouts_count']} incoming payments not yet received<br>"
                f"Payables {ui.fmt_eur(kpi['payables_eur'])} · {kpi['overdue_payments']} past due date",
                health["Payments"], period="Now")
with cards[5]:
    ui.kpi_card("Open support tickets", f"{kpi['open_tickets']}",
                f"{kpi['breached_tickets']} past SLA · {kpi['at_risk_tickets']} at risk<br>"
                f"{ui.fmt_pct(kpi['sla_compliance'])} resolved within SLA in the last {window.days} days",
                health["Support"], period="Now")

# Drill-down buttons: open the detail page already filtered to what the card is about.
drill_downs = [
    None, None,
    ("views/shipping.py", "Overdue shipments →", {"ship_only_overdue": True, "ship_destination": list(regions)}),
    ("views/inventory.py", "Below minimum →", {"inv_status": ["Out of Stock", "Critical", "Low"]}),
    ("views/payments.py", "Unpaid payments →", {"pay_unpaid": True}),
    ("views/support.py", "Tickets past SLA →", {"sup_past_sla": True}),
]
for col, link in zip(st.columns(6), drill_downs):
    if link:
        with col:
            if st.button(link[1], key=f"drill_{link[0]}", type="tertiary"):
                ui.open_page(link[0], filters=link[2])

# --- 2. Requires immediate attention -------------------------------------------
AREA_PAGES = {"Shipping": "views/shipping.py", "Inventory": "views/inventory.py",
              "Payments": "views/payments.py", "Support": "views/support.py"}
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

actions_table = bundle.frames["actions"]
linked = act.link_issues_to_actions(items, actions_table)
urgent = linked[linked["severity"].isin(["Critical", "High"])]
if len(urgent):
    link = act.issue_action_summary(urgent, actions_table)
    note = (f"Action status of these {link['total']} issues: **{link['with_action']} have an action** "
            f"({link['Open']} open, {link['In Progress']} in progress, {link['Blocked']} blocked) and "
            f"**{link['No action']} have none yet**")
    if link["Action resolved, issue still open"]:
        note += f"; {link['Action resolved, issue still open']} have a resolved action but the issue is still open"
    st.markdown(note + f". {link['resolved_issues']} issue(s) have been resolved through the Action Tracker.")

    top_n = config.DASHBOARD_TOP_ISSUES
    st.caption(f"The {min(top_n, len(urgent))} most urgent: Critical before High, then overdue before not yet "
               "due, longest overdue first, then nearest deadline.")
    widths = [0.9, 2.6, 0.8, 1.1, 1.0, 1.2, 0.6]
    for col, title in zip(st.columns(widths), ["Severity", "Issue", "Area", "Owner", "Urgency", "Action", ""]):
        col.markdown(f'<div class="kpi-label">{title}</div>', unsafe_allow_html=True)
    for _, issue in urgent.head(top_n).iterrows():
        cols = st.columns(widths, vertical_alignment="center")
        cols[0].markdown(ui.severity_badge(str(issue["severity"])), unsafe_allow_html=True)
        cols[1].markdown(f"{issue['issue']}  \n<span class='kpi-period'>{issue['reference']}</span>",
                         unsafe_allow_html=True)
        cols[2].write(str(issue["area"]))
        cols[3].write(issue["owner"])
        cols[4].write(issue["urgency"])
        state = ui.ACTION_STATE_LABELS[issue["action_status"]]
        cols[5].write(f"{issue['action_id']} · {state}" if issue["action_id"] else state)
        page = AREA_PAGES.get(str(issue["area"]))
        if page and cols[6].button("View →", key=f"open_{issue['reference']}", type="tertiary"):
            ui.open_page(page, focus=issue["reference"])

with st.expander(f"View all {len(items)} open issues, including medium priority"):
    a1, a2 = st.columns([1, 3])
    with a1:
        severities = st.multiselect("Severity", metrics.SEVERITIES, default=metrics.SEVERITIES)
    with a2:
        areas = st.multiselect("Area", metrics.AREAS, placeholder="All areas")
    shown = linked[linked["severity"].isin(severities)]
    if areas:
        shown = shown[shown["area"].isin(areas)]
    shown = shown.assign(severity=shown["severity"].map(ui.SEVERITY_LABELS),
                         action_status=shown["action_status"].map(ui.ACTION_STATE_LABELS))
    st.dataframe(
        shown[["severity", "issue", "reference", "area", "owner", "urgency", "action_status", "action_id"]],
        hide_index=True, width="stretch", height=min(36 * (len(shown) + 1) + 4, 600),
        column_config={
            "severity": st.column_config.TextColumn("Severity", width="small"),
            "issue": st.column_config.TextColumn("Issue", width="large"), "reference": "Reference",
            "area": "Area", "owner": "Owner", "urgency": "Urgency", "action_status": "Action",
            "action_id": "Action ID",
        })
    st.caption(f"Showing {len(shown)} of {len(items)} open issues, most urgent first. "
               "Severity rules are explained in the README.")

# --- 3. AI Operations Assistant -------------------------------------------------
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

# --- 4. Yesterday at a glance ---------------------------------------------
day = metrics.yesterday_overview(e, as_of)
st.subheader(f"Yesterday at a glance · {day['date']:%a %d %b}")
for col, (label, key) in zip(st.columns(6), [
        ("orders received", "orders_received"), ("shipments dispatched", "shipments_dispatched"),
        ("deliveries completed", "deliveries"), ("tickets opened", "tickets_opened"),
        ("tickets resolved", "tickets_resolved"), ("payments completed", "payments_completed")]):
    with col:
        ui.mini_stat(label, day[key])

# --- 5. Trends -------------------------------------------------------------
st.subheader("Performance trends")
left, right = st.columns(2)

with left, st.container(border=True):
    trend = metrics.order_trend(e["orders"], as_of, period)
    st.markdown(f"**Orders per day** · {period_label}")
    st.caption("Daily orders excluding cancellations; the bars add up to the Orders processed card. "
               "The line is the 7-day average.")
    st.plotly_chart(charts.orders_trend(trend), theme=None, config={"displayModeBar": False})
    with st.expander("View data"):
        st.dataframe(trend, hide_index=True, width="stretch")

with right, st.container(border=True):
    delivery = metrics.on_time_trend(e["shipments"], as_of, period)
    st.markdown(f"**On-time delivery rate** · rolling 7 days, {window.label}")
    st.caption("Each point: share of parcels delivered by the promised date in the 7 days ending that day. "
               "With the 7-day period selected, the last point equals the Delayed shipments card.")
    st.plotly_chart(charts.on_time_trend(delivery), theme=None, config={"displayModeBar": False})
    with st.expander("View data"):
        st.dataframe(delivery, hide_index=True, width="stretch")

left, right = st.columns(2)
with left, st.container(border=True):
    backlog = metrics.support_backlog(e["tickets"])
    st.markdown("**Support backlog by priority** · now")
    st.caption("Open tickets and where each stands against its SLA deadline. "
               "'Paused' means waiting on the customer.")
    st.plotly_chart(charts.support_backlog(backlog), theme=None, config={"displayModeBar": False})
    with st.expander("View data"):
        st.dataframe(backlog, hide_index=True, width="stretch")

with right, st.container(border=True):
    load = metrics.issues_by_owner(items)
    st.markdown("**Open issues by owner** · now")
    st.caption("Who is carrying the operational load. Helps spot overloaded team members.")
    if load.empty:
        st.info("No open issues.")
    else:
        st.plotly_chart(charts.issues_by_owner(load), theme=None, config={"displayModeBar": False})
        with st.expander("View data"):
            st.dataframe(load, hide_index=True, width="stretch")
