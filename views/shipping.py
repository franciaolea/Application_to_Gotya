"""Shipping Management: shipment status, overdue alerts, carrier performance.

Questions this page answers:
- Which parcels are late, by how much, and who is chasing each one?
- Is the problem one carrier, one destination, or everywhere?
"""
import streamlit as st

from src import charts, config, metrics, ui

bundle = ui.get_data()
as_of = bundle.as_of
shipments = metrics.enrich_shipments(bundle.frames["shipments"], as_of)

st.title("Shipping Management")
st.caption(f"Shipments as of {as_of:%d %B %Y, %H:%M}. A shipment is overdue when it has not arrived by its "
           f"expected delivery date; {config.SHIPMENT_ESCALATION_DAYS}+ days overdue is escalated.")

# --- Filters (can be pre-set by the dashboard's drill-down buttons) ---------------
focus = ui.apply_navigation("views/shipping.py")
f1, f2, f3, f4, f5 = st.columns([1, 1, 1, 1, 0.8])
with f1:
    carriers = st.multiselect("Carrier", sorted(shipments["carrier"].unique()), placeholder="All", key="ship_carrier")
with f2:
    destinations = st.multiselect("Destination", sorted(shipments["destination"].unique()), placeholder="All",
                                  key="ship_destination")
with f3:
    statuses = st.multiselect("Status", ["In Transit", "Out for Delivery", "Exception", "Delivered"],
                              placeholder="All", key="ship_status")
with f4:
    owners = st.multiselect("Owner", sorted(shipments["owner"].unique()), placeholder="All", key="ship_owner")
with f5:
    st.write("")  # aligns the toggle with the dropdowns
    only_overdue = st.toggle("Only overdue", key="ship_only_overdue")
view = metrics.filter_frame(shipments, {"carrier": carriers, "destination": destinations,
                                        "status": statuses, "owner": owners})
if only_overdue:
    view = view[view["is_delayed"]]
if focus:
    ui.focus_panel("views/shipping.py", focus, shipments[shipments["shipment_id"] == focus],
                   ["shipment_id", "order_id", "carrier", "destination", "status", "expected_delivery_date",
                    "days_late", "owner", "tracking_number"],
                   {"expected_delivery_date": st.column_config.DateColumn("Expected", format=ui.DATE_FORMAT)})
if view.empty:
    st.info("No shipments match these filters.")
    st.stop()

issues = metrics.area_issues("Shipping", view)
performance = metrics.carrier_performance(view, as_of)
window = metrics.reporting_window(as_of, 28)
open_now = view[view["is_open"]]
delivered_recently = performance["delivered"].sum()
on_time_rate = performance["on_time"].sum() / delivered_recently if delivered_recently else None

# --- KPIs ------------------------------------------------------------------
cards = st.columns(5)
with cards[0]:
    ui.kpi_card("Open shipments", f"{len(open_now)}",
                f"{int((open_now['status'] == 'Out for Delivery').sum())} out for delivery today", period="Now")
with cards[1]:
    ui.kpi_card("Overdue", f"{int(view['is_delayed'].sum())}",
                f"{int(view['needs_escalation'].sum())} late by {config.SHIPMENT_ESCALATION_DAYS}+ days "
                "(escalate)", metrics.status_of(issues), period="Now")
with cards[2]:
    ui.kpi_card("Carrier exceptions", f"{int((open_now['has_exception']).sum())}",
                "Delivery problem reported by the carrier (address, damage, lost)", period="Now")
with cards[3]:
    ui.kpi_card("On-time delivery", ui.fmt_pct(on_time_rate),
                f"Target {config.ON_TIME_DELIVERY_TARGET:.0%} · {int(delivered_recently)} parcels delivered<br>"
                "28 days so every carrier is judged on enough parcels", period=ui.period_text(window))
with cards[4]:
    worst = performance.dropna(subset=["on_time_rate"]).sort_values("on_time_rate").head(1)
    if worst.empty:
        ui.kpi_card("Weakest carrier", "n/a", "No deliveries in the last 28 days", period=ui.period_text(window))
    else:
        w = worst.iloc[0]
        ui.kpi_card("Weakest carrier", f"{w['on_time_rate']:.0%}",
                    f"{w['carrier']}<br>{int(w['delayed'])} overdue · {int(w['exceptions'])} exceptions open",
                    period=ui.period_text(window))

# --- Overdue shipment alerts -----------------------------------------------------
st.subheader("Overdue shipment alerts")
problems = view[view["is_delayed"] | (view["has_exception"] & view["is_open"])]
if problems.empty:
    st.success("No overdue shipments or carrier exceptions in this view.")
else:
    escalate = int(problems["needs_escalation"].sum())
    message = f"**{len(problems)} shipment(s) need follow-up**"
    message += f", of which **{escalate} must be escalated** to the carrier account manager." if escalate else "."
    (st.error if escalate else st.warning)(message)
    severity = issues.set_index("reference")["severity"]  # ordered: Critical < High < Medium
    alert_table = problems.assign(severity=problems["shipment_id"].map(severity)) \
        .sort_values(["severity", "days_late"], ascending=[True, False])
    alert_table["severity"] = alert_table["severity"].astype(str).map(ui.SEVERITY_LABELS)
    st.dataframe(
        alert_table[["severity", "shipment_id", "order_id", "carrier", "destination", "status",
                     "ship_date", "expected_delivery_date", "days_late", "owner", "tracking_number"]],
        hide_index=True, width="stretch",
        column_config={
            "severity": "Severity", "shipment_id": "Shipment", "order_id": "Order", "carrier": "Carrier",
            "destination": "Destination", "status": "Status",
            "ship_date": st.column_config.DateColumn("Shipped", format=ui.DATE_FORMAT),
            "expected_delivery_date": st.column_config.DateColumn("Expected", format=ui.DATE_FORMAT),
            "days_late": st.column_config.NumberColumn("Days late"),
            "owner": "Owner", "tracking_number": "Tracking",
        })

# --- Carrier performance -----------------------------------------------------------
st.subheader("Carrier performance")
left, right = st.columns(2)
with left, st.container(border=True):
    st.markdown(f"**On-time delivery by carrier** · {ui.period_text(window)}")
    st.caption("Share of delivered parcels that arrived by the promised date.")
    st.plotly_chart(charts.carrier_on_time(performance), theme=None, config={"displayModeBar": False})
with right, st.container(border=True):
    lateness = metrics.open_shipments_by_lateness(view)
    st.markdown("**Parcels in transit by carrier** · now")
    st.caption("Everything still travelling, split by how late it is against the promised date.")
    st.plotly_chart(charts.open_shipments_by_lateness(lateness), theme=None, config={"displayModeBar": False})

st.dataframe(
    performance.drop(columns=["below_target"]).assign(on_time_rate=performance["on_time_rate"] * 100),
    hide_index=True, width="stretch",
    column_config={
        "carrier": "Carrier", "delivered": f"Delivered ({window.label})", "on_time": "On time",
        "on_time_rate": st.column_config.NumberColumn("On-time rate", format="%.0f%%"),
        "avg_days_late": st.column_config.NumberColumn("Avg days late (late parcels)", format="%.1f"),
        "open": "In transit", "delayed": "Overdue now", "exceptions": "Exceptions now",
    })

# --- All shipments -------------------------------------------------------------
with st.expander(f"All shipments in view ({len(view)})"):
    st.dataframe(
        view.sort_values("ship_date", ascending=False)[[
            "shipment_id", "order_id", "carrier", "destination", "status", "ship_date",
            "expected_delivery_date", "actual_delivery_date", "days_late", "owner", "tracking_number"]],
        hide_index=True, width="stretch",
        column_config={
            "ship_date": st.column_config.DateColumn("Shipped", format=ui.DATE_FORMAT),
            "expected_delivery_date": st.column_config.DateColumn("Expected", format=ui.DATE_FORMAT),
            "actual_delivery_date": st.column_config.DateColumn("Delivered", format=ui.DATE_FORMAT),
        })
    st.download_button("Download shipments (CSV)", view.to_csv(index=False),
                       file_name=f"shipments_{as_of:%Y-%m-%d}.csv", mime="text/csv")
