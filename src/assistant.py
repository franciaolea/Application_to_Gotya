"""AI Operations Assistant: a rule-based analysis engine (no external AI service).

It goes one step beyond the KPIs: for each operational problem it finds, it
explains the root cause it can prove from the data, recommends specific
actions, names the role that should own each one, and flags what to escalate.

How it stays honest ("never invent data"):
- Every figure a finding mentions is first registered as Evidence (value,
  label and the data it came from). Sentences are built only from those
  registered figures, and a test checks every number in every sentence.
- A root cause is only stated when a check passes (concentration, peer
  comparison, cross-module link, demand change). Otherwise the finding says
  "not determined" and names the data that would be needed.

Pipeline: detectors -> root-cause checks -> impact score -> top 3.
All thresholds are in config.py. No Streamlit code here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from src import config, metrics
from src.metrics import Frames

NOT_DETERMINED = "Not determined"


# --- Data structures -------------------------------------------------------

@dataclass(frozen=True)
class Evidence:
    label: str      # what was measured, e.g. "Parcelyn on-time rate, last 28 days"
    display: str    # the value as shown, e.g. "66%"
    source: str     # where it comes from, e.g. "shipments.csv: 119 parcels delivered"


@dataclass(frozen=True)
class Action:
    text: str
    owner_role: str   # role and department, e.g. "Carrier account manager (Logistics)"


@dataclass
class Finding:
    kind: str
    area: str
    title: str
    problem: str
    root_cause: Optional[str]
    confidence: str                 # "High", "Medium" or "Not determined"
    actions: List[Action]
    severity: str                   # "Critical", "High" or "Medium"
    escalate: bool
    escalation_reason: Optional[str]
    escalate_to: str
    impact_score: float
    score_parts: Dict[str, float]
    evidence: List[Evidence]
    data_gaps: List[str]
    references: List[str] = field(default_factory=list)

    def text(self) -> str:
        """Every sentence the user reads (used by the no-invention check)."""
        parts = [self.title, self.problem, self.root_cause or "", self.escalation_reason or ""]
        return " ".join(parts + [a.text for a in self.actions])


@dataclass
class AssistantReport:
    as_of: datetime
    findings: List[Finding]          # all findings, ranked by impact
    areas_without_findings: List[str]

    @property
    def top(self) -> List[Finding]:
        return self.findings[:config.ASSISTANT_TOP_N]


class _Evidence:
    """Collects evidence and hands back the display string, so text can only use registered figures."""

    def __init__(self) -> None:
        self.items: List[Evidence] = []

    def __call__(self, label: str, display: str, source: str) -> str:
        self.items.append(Evidence(label, display, source))
        return display


# --- Formatting and scoring helpers ------------------------------------------

def _pct(value: float) -> str:
    return f"{value:.0%}"


def _eur(value: float) -> str:
    return f"€{value:,.0f}"


def _names(values) -> str:
    values = [str(v) for v in values]
    return values[0] if len(values) == 1 else ", ".join(values[:-1]) + " and " + values[-1]


def _scale_level(kind: str, amount: float) -> int:
    level_2, level_3 = config.ASSISTANT_SCALE_LEVELS[kind]
    return 3 if amount >= level_3 else 2 if amount >= level_2 else 1


def _score(severity: str, scale_kind: str, amount: float, urgency: float):
    parts = {"severity": float(config.ASSISTANT_SEVERITY_WEIGHT[severity]),
             "scale": float(_scale_level(scale_kind, amount)), "urgency": urgency}
    return round(parts["severity"] * parts["scale"] * parts["urgency"], 2), parts


def numbers_in(text: str) -> List[str]:
    """Numbers as written in a sentence, e.g. '66%' -> '66', '€9,608' -> '9608'."""
    return [n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]


# --- Detector 1: carrier performance --------------------------------------------

def _carrier_finding(e: Frames, as_of: datetime) -> Optional[Finding]:
    s, tickets = e["shipments"], e["tickets"]
    perf = metrics.carrier_performance(s, as_of).dropna(subset=["on_time_rate"])
    perf = perf[perf["delivered"] >= config.ASSISTANT_MIN_DELIVERIES]
    if len(perf) < 2:
        return None
    worst = perf.sort_values("on_time_rate").iloc[0]
    others = perf[perf["carrier"] != worst["carrier"]]
    others_rate = others["on_time"].sum() / others["delivered"].sum()
    target = config.ON_TIME_DELIVERY_TARGET
    if worst["on_time_rate"] >= target or others_rate - worst["on_time_rate"] < config.ASSISTANT_CARRIER_GAP:
        return None

    name = worst["carrier"]
    ev = _Evidence()
    rate = ev(f"{name} on-time rate, last 28 days", _pct(worst["on_time_rate"]),
              f"shipments.csv: {int(worst['delivered'])} {name} parcels delivered")
    others_txt = ev("Other carriers' on-time rate, last 28 days", _pct(others_rate),
                    f"shipments.csv: {int(others['delivered'].sum())} parcels delivered")
    target_txt = ev("On-time delivery target", _pct(target), "config.py")
    best = others.sort_values("on_time_rate").iloc[-1]
    best_rate = ev(f"{best['carrier']} on-time rate, last 28 days (best carrier)", _pct(best["on_time_rate"]),
                   "shipments.csv")

    # Check 1 - concentration: share of open problems vs share of volume.
    volume_share = worst["delivered"] / perf["delivered"].sum()
    open_problems = s[s["is_delayed"] | (s["has_exception"] & s["is_open"])]
    mine = open_problems[open_problems["carrier"] == name]
    problem_share = len(mine) / len(open_problems) if len(open_problems) else 0.0
    lift = problem_share / volume_share if volume_share else 0.0
    volume_txt = ev(f"{name} share of parcels delivered, last 28 days", _pct(volume_share), "shipments.csv")
    problems_txt = ev(f"{name} share of open late or exception parcels",
                      f"{len(mine)} of {len(open_problems)}", "shipments.csv: open parcels")
    concentrated = lift >= config.ASSISTANT_CONCENTRATION_LIFT

    # Check 2 - peer comparison per destination (rules out "it's just the hard countries").
    since = pd.Timestamp(as_of).normalize() - pd.Timedelta(days=28)
    delivered = s[~s["is_open"] & (s["actual_delivery_date"] >= since)]
    by_country = delivered.groupby([delivered["carrier"] == name, "destination"])["delivered_on_time"] \
        .mean().unstack(0).dropna()
    worse_in = int((by_country[True] < by_country[False]).sum()) if {True, False} <= set(by_country.columns) else 0
    shared = len(by_country) if {True, False} <= set(by_country.columns) else 0
    peer = shared > 0 and worse_in / shared >= 2 / 3
    if shared:
        countries_txt = ev(f"Countries where {name} is slower than the other carriers",
                           f"{worse_in} of {shared}", "shipments.csv: on-time rate per destination, last 28 days")

    # Cross-module: delivery complaints per 100 parcels.
    complaints = metrics.delivery_contacts_by_carrier(tickets, s, as_of).set_index("carrier")["per_100_shipments"]
    complaints = complaints.dropna()
    if name in complaints.index and len(complaints) > 1:
        rest = complaints.drop(name)
        ev("Delivery complaints per 100 parcels, last 14 days",
           f"{name} {complaints[name]:.0f}; others {rest.min():.0f}-{rest.max():.0f}", "tickets.csv + shipments.csv")

    causes = []
    if concentrated:
        causes.append(f"{name} delivers {volume_txt} of parcels but accounts for {problems_txt} open late or "
                      f"exception parcels")
    if peer:
        causes.append(f"it is slower than the other carriers in {countries_txt} countries they both serve, "
                      f"so the destination mix does not explain the gap")
    root_cause = ("Carrier-specific performance: " + "; ".join(causes) + ".") if causes else None
    confidence = "High" if concentrated and peer else "Medium" if causes else NOT_DETERMINED

    escalation_days = config.SHIPMENT_ESCALATION_DAYS
    threshold = ev("Escalation threshold", f"{escalation_days}+ days late", "config.py")
    very_late = mine[mine["needs_escalation"]]
    late_delivered = int(worst["delivered"] - worst["on_time"])
    affected = late_delivered + len(mine)
    ev("Customers affected (late deliveries in 28 days + open late parcels)", f"{affected}", "shipments.csv")
    if len(very_late):
        very_late_txt = ev(f"{name} parcels {threshold}", f"{len(very_late)}", "shipments.csv")

    actions = [Action(f"Hold a performance review with {name} this week: present the {rate} on-time rate "
                      f"against the {target_txt} target and agree a written recovery plan.",
                      "Carrier account manager (Logistics)")]
    if best["on_time_rate"] >= config.ON_TIME_ACCEPTABLE:
        actions.append(Action(f"Move part of new {name} volume to {best['carrier']} ({best_rate} on time) until "
                              f"{name} improves.",
                              "Logistics manager (Logistics)"))
    if len(mine):
        actions.append(Action(f"Send proactive updates to the customers of the {problems_txt.split(' of ')[0]} "
                              f"late or exception {name} parcels before they contact support.",
                              "Customer service lead (Customer Service)"))

    severity = "Critical" if len(very_late) else "High"
    score, parts = _score(severity, "customers", affected, 1.0 if len(mine) else 0.6)
    return Finding(
        kind="carrier_performance", area="Shipping", title=f"{name} is the main delivery bottleneck",
        problem=f"{name} delivered {rate} of parcels on time in the last 28 days, against {others_txt} "
                f"for the other carriers and a {target_txt} target.",
        root_cause=root_cause, confidence=confidence, actions=actions, severity=severity,
        escalate=bool(len(very_late)),
        escalation_reason=f"{very_late_txt} {name} parcels are {threshold}." if len(very_late) else None,
        escalate_to=config.ESCALATION_PATHS["Shipping"], impact_score=score, score_parts=parts,
        evidence=ev.items,
        data_gaps=["No carrier scan events: the data cannot show whether delays happen at collection, "
                   "in the hub network or in final delivery."],
        references=mine["shipment_id"].tolist(),
    )


# --- Detector 2: stock-outs ----------------------------------------------------------

def _stock_finding(e: Frames, as_of: datetime) -> Optional[Finding]:
    products = metrics.add_demand(e["products"], e["orders"], as_of)
    out = products[products["stock_status"] == "Out of Stock"]
    at_risk = products[products["stockout_risk"] & (products["stock_on_hand"] > 0)]
    affected = pd.concat([out, at_risk])
    if affected.empty:
        return None

    ev = _Evidence()
    out_sales = float((out["daily_demand"] * out["unit_price"]).sum())
    risk_sales = float((at_risk["daily_demand"] * at_risk["unit_price"]).sum())
    out_txt = ev("Products out of stock", f"{len(out)}", "products.csv")
    if len(out):
        ev("Out-of-stock products", _names(out["product_name"]), "products.csv")
        out_sales_txt = ev("Daily sales of the out-of-stock products (4-week average)", _eur(out_sales),
                           "orders.csv x products.csv unit price")
    risk_txt = ev("Products in stock that will sell out before a restock can arrive", f"{len(at_risk)}",
                  "products.csv + orders.csv: days of cover < supplier lead time")
    if len(at_risk):
        ev("Products at risk", _names(at_risk["product_name"]), "products.csv")
        tightest = at_risk.assign(ratio=at_risk["days_of_cover"] / at_risk["lead_time_days"]) \
            .sort_values("ratio").iloc[0]
        cover_txt = ev(f"{tightest['product_name']}: days of cover", f"{tightest['days_of_cover']:.0f} days",
                       "products.csv + orders.csv: stock / average daily sales")
        lead_txt = ev(f"{tightest['product_name']}: supplier lead time", f"{int(tightest['lead_time_days'])} days",
                      "products.csv")

    # Check 1 - demand spike: last 7 days vs the previous 3 weeks.
    orders = e["orders"][~e["orders"]["is_cancelled"]]
    now = pd.Timestamp(as_of)
    recent = orders[orders["order_date"] >= now - pd.Timedelta(days=7)].groupby("sku")["quantity"].sum() / 7
    earlier = orders[(orders["order_date"] >= now - pd.Timedelta(days=28))
                     & (orders["order_date"] < now - pd.Timedelta(days=7))].groupby("sku")["quantity"].sum() / 21
    spikes = []
    for _, p in affected.iterrows():
        before, after = earlier.get(p["sku"], 0), recent.get(p["sku"], 0)
        # Require real volume (5+ units in 7 days) so a jump from 1 to 3 units is not called a spike.
        if before > 0 and after * 7 >= 5 and after >= config.ASSISTANT_DEMAND_SPIKE * before:
            spikes.append(f"{p['product_name']} (+{after / before - 1:.0%})")
    # Check 2 - supplier concentration.
    by_supplier = affected["supplier"].value_counts()
    top_supplier, top_count = by_supplier.index[0], int(by_supplier.iloc[0])
    catalog_share = (products["supplier"] == top_supplier).mean()
    supplier_lift = (top_count / len(affected)) / catalog_share if catalog_share else 0

    causes = []
    if spikes:
        spike_txt = ev("Daily sales, last 7 days vs previous 3 weeks", _names(spikes), "orders.csv")
        causes.append(f"demand rose faster than the reorder point assumes: {spike_txt}")
    # Name a supplier only with at least 3 affected products: 2 can be coincidence.
    if top_count >= 3 and supplier_lift >= config.ASSISTANT_CONCENTRATION_LIFT:
        supplier_txt = ev(f"Affected products supplied by {top_supplier}", f"{top_count} of {len(affected)}",
                          "products.csv")
        range_txt = ev(f"{top_supplier} share of the product range", _pct(catalog_share), "products.csv")
        causes.append(f"{supplier_txt} affected products come from {top_supplier} ({range_txt} of the range), "
                      "which points to a supply problem with that supplier")
    root_cause = ("Likely cause: " + "; ".join(causes) + ".") if causes else None

    affected_txt = ev("Products affected (out of stock + at risk)", f"{len(affected)}", "products.csv")
    qty = int(affected["suggested_order_qty"].sum())
    cost = float(affected["reorder_cost"].sum())
    qty_txt = ev("Suggested order quantity for the affected products", f"{qty} units", "products.csv")
    cost_txt = ev("Estimated cost at unit cost", _eur(cost), "products.csv")
    actions = [Action(f"Check open purchase orders for the {affected_txt} affected products; where none "
                      f"exists, order {qty_txt} today (about {cost_txt} at cost).", "Purchasing lead (Purchasing)")]
    if len(at_risk):
        actions.append(Action(f"Ask {tightest['supplier']} to expedite {tightest['product_name']}, which has "
                              f"{cover_txt} of cover against a supplier lead time of {lead_txt}.",
                              "Purchasing lead (Purchasing)"))
    if len(out):
        actions.append(Action(f"Mark the {out_txt} out-of-stock products as back-order or pause their "
                              "advertising, so customers are not sold items that cannot ship.",
                              "E-commerce manager (Sales)"))

    severity = "Critical" if len(out) else "High"
    problem = (f"{out_txt} products are out of stock and {risk_txt} more will sell out before a new "
               "delivery can arrive.")
    if len(out):
        problem += f" The out-of-stock products normally sell about {out_sales_txt} a day."
    score, parts = _score(severity, "eur_per_day", out_sales + risk_sales, 1.0 if len(out) else 0.8)
    return Finding(
        kind="stock_out", area="Inventory", title="Stock-outs are costing sales",
        problem=problem, root_cause=root_cause, confidence="Medium" if causes else NOT_DETERMINED,
        actions=actions, severity=severity, escalate=bool(len(out)),
        escalation_reason=(f"{out_txt} products are out of stock and losing about {out_sales_txt} of sales a day."
                           if len(out) else None),
        escalate_to=config.ESCALATION_PATHS["Inventory"], impact_score=score, score_parts=parts,
        evidence=ev.items,
        data_gaps=["No purchase-order data: the data cannot show whether replenishment is already on order "
                   "or whether a supplier is late."],
        references=affected["sku"].tolist(),
    )


# --- Detector 3: support SLA pressure ------------------------------------------------------

def _support_finding(e: Frames, as_of: datetime) -> Optional[Finding]:
    t, s = e["tickets"], e["shipments"]
    k = metrics.support_kpis(t, as_of)
    target = config.SLA_COMPLIANCE_TARGET
    below_target = k["sla_compliance"] is not None and k["sla_compliance"] < target
    if not k["breached"] and not below_target:
        return None

    ev = _Evidence()
    target_txt = ev("SLA compliance target", _pct(target), "config.py")
    breached_txt = ev("Open tickets past their SLA", f"{k['breached']}", "tickets.csv")
    urgent_txt = ev("Of which Critical or High priority", f"{k['urgent_breached']}", "tickets.csv")
    problem = f"{breached_txt} open tickets are past their SLA, {urgent_txt} of them Critical or High priority."
    if k["sla_compliance"] is not None:
        compliance_txt = ev("Tickets resolved within SLA, last 7 days", _pct(k["sla_compliance"]),
                            f"tickets.csv: {k['resolved']} tickets resolved")
        problem = f"{compliance_txt} of tickets resolved in the last 7 days met their SLA (target {target_txt}). " + problem

    # Check 1 - cross-module link: are "Where is my order" contacts caused by late parcels?
    wismo = t[t["category"] == "Where is my order"]
    late_parcels = s[(s["days_late"] > 0) | s["has_exception"]][["order_id", "carrier"]]
    linked = wismo.merge(late_parcels, on="order_id", how="inner").drop_duplicates("ticket_id")
    causes = []
    if len(wismo) >= 5 and len(linked) / len(wismo) >= 0.5:
        wismo_txt = ev("'Where is my order' tickets (all tickets in the data)", f"{len(wismo)} of {len(t)}",
                       "tickets.csv")
        linked_txt = ev("'Where is my order' tickets about a parcel that was late or had an exception",
                        f"{len(linked)} of {len(wismo)}", "tickets.csv linked to shipments.csv")
        top_carrier = linked["carrier"].value_counts()
        carrier_count = ev(f"Of those, parcels shipped with {top_carrier.index[0]}", f"{int(top_carrier.iloc[0])}",
                           "shipments.csv")
        is_largest = metrics.contacts_by_category(t).iloc[-1]["category"] == "Where is my order"
        reason = "the largest contact reason" if is_largest else "a major contact reason"
        causes.append(f"late deliveries drive contacts: 'Where is my order' is {reason} ({wismo_txt} tickets), "
                      f"and {linked_txt} of these concern parcels that were late, {carrier_count} of them shipped "
                      f"with {top_carrier.index[0]}")

    # Check 2 - which priority misses SLA most often.
    resolution = metrics.resolution_vs_sla(t)
    judged = resolution[resolution["resolved"] >= 5].dropna(subset=["met_sla"])
    weakest = judged.sort_values("met_sla").iloc[0] if len(judged) else None
    if weakest is not None and weakest["met_sla"] < target:
        weak_txt = ev(f"{weakest['priority']}-priority tickets resolved within SLA", _pct(weakest["met_sla"]),
                      f"tickets.csv: {int(weakest['resolved'])} resolved tickets")
        causes.append(f"{weakest['priority']}-priority tickets miss their SLA most often ({weak_txt} within SLA)")
    root_cause = ("; ".join(c[0].upper() + c[1:] for c in causes) + ".") if causes else None

    breached = t[t["is_breached"]].assign(late=lambda d: -d["hours_to_sla"])
    breached = breached.assign(urgent=breached["priority"].isin(metrics.URGENT_PRIORITIES)) \
        .sort_values(["urgent", "late"], ascending=False)
    actions = []
    if len(breached):
        b = breached.iloc[0]
        oldest_txt = ev("Most urgent ticket past SLA",
                        f"{b['ticket_id']} ({b['priority']}, {b['late']:.0f}h past SLA, owner {b['owner']})",
                        "tickets.csv")
        actions.append(Action(f"Resolve {oldest_txt} today and update the customer.",
                              "Support team lead (Customer Service)"))
    if causes and len(wismo) >= 5 and len(linked) / len(wismo) >= 0.5:
        actions.append(Action(f"Send proactive delay notifications for late parcels: {linked_txt} 'Where is my "
                              "order' tickets were about late parcels.", "Customer service lead (Customer Service)"))
    if weakest is not None and weakest["met_sla"] < target:
        actions.append(Action(f"Review routing and staffing of the {weakest['priority']}-priority queue: "
                              f"{weak_txt} resolved within SLA.", "Support team lead (Customer Service)"))

    severity = "Critical" if k["urgent_breached"] else "High" if k["breached"] else "Medium"
    affected = k["breached"] + k["at_risk"]
    ev("Customers affected (tickets past SLA or at risk)", f"{affected}", "tickets.csv")
    score, parts = _score(severity, "customers", affected, 1.0 if k["urgent_breached"] else 0.7)
    return Finding(
        kind="support_sla", area="Support", title="Support is missing its SLA",
        problem=problem, root_cause=root_cause, confidence="Medium" if causes else NOT_DETERMINED,
        actions=actions, severity=severity, escalate=bool(k["urgent_breached"]),
        escalation_reason=(f"Critical/High-priority tickets past SLA: {urgent_txt}, most urgent {oldest_txt}."
                           if k["urgent_breached"] else None),
        escalate_to=config.ESCALATION_PATHS["Support"], impact_score=score, score_parts=parts,
        evidence=ev.items,
        data_gaps=["No staffing or shift data: the data cannot show whether SLA misses come from capacity "
                   "or from process."],
        references=breached["ticket_id"].tolist(),
    )


# --- Detector 4: fulfilment backlog --------------------------------------------------------------

def _fulfilment_finding(e: Frames, as_of: datetime) -> Optional[Finding]:
    stuck = e["orders"][e["orders"]["is_stuck"]]
    if stuck.empty:
        return None
    ev = _Evidence()
    hours = config.ORDER_FULFILMENT_HOURS
    count_txt = ev(f"Orders not shipped after {hours}h", f"{len(stuck)}", "orders.csv")
    oldest_txt = ev("Oldest unshipped order", f"{stuck['age_hours'].max():.0f}h", "orders.csv")
    by_owner = stuck["owner"].value_counts()
    root_cause = None
    if len(stuck) >= 3 and by_owner.iloc[0] / len(stuck) >= 0.7:
        owner_txt = ev(f"Stuck orders assigned to {by_owner.index[0]}", f"{int(by_owner.iloc[0])} of {len(stuck)}",
                       "orders.csv")
        root_cause = f"The backlog is concentrated: {owner_txt} stuck orders sit with {by_owner.index[0]}."
    very_old = stuck[stuck["age_hours"] > config.ORDER_ESCALATION_HOURS]
    severity = "Critical" if len(very_old) else "High"
    if len(very_old):
        very_old_txt = ev(f"Orders not shipped after {config.ORDER_ESCALATION_HOURS}h", f"{len(very_old)}",
                          "orders.csv")
    score, parts = _score(severity, "customers", len(stuck), 1.0)
    return Finding(
        kind="fulfilment_backlog", area="Orders", title="Orders are waiting too long to ship",
        problem=f"{count_txt} orders have not shipped {hours}h after being placed; the oldest has waited {oldest_txt}.",
        root_cause=root_cause, confidence="Medium" if root_cause else NOT_DETERMINED,
        actions=[Action(f"Put the {count_txt} orders waiting over {hours}h at the front of today's picking wave.",
                        "Warehouse manager (Fulfilment)"),
                 Action("Tell the affected customers the new dispatch date before they ask.",
                        "Customer service lead (Customer Service)")],
        severity=severity, escalate=bool(len(very_old)),
        escalation_reason=(f"{very_old_txt} orders have waited more than {config.ORDER_ESCALATION_HOURS}h."
                           if len(very_old) else None),
        escalate_to=config.ESCALATION_PATHS["Orders"], impact_score=score, score_parts=parts,
        evidence=ev.items,
        data_gaps=["No warehouse capacity or pick-pack data: the data cannot show why these orders stalled."],
        references=stuck["order_id"].tolist(),
    )


# --- Detector 5: blocked and overdue payments ------------------------------------------------------

def _payment_finding(e: Frames, as_of: datetime) -> Optional[Finding]:
    p = e["payments"]
    blocked = p[p["is_outstanding"] & p["status"].isin(["Failed", "On Hold"])]
    overdue = p[p["is_overdue"] & ~p.index.isin(blocked.index)]
    if blocked.empty and overdue.empty:
        return None
    ev = _Evidence()
    total = float(blocked["amount_eur"].sum() + overdue["amount_eur"].sum())
    total_txt = ev("Money blocked or overdue", _eur(total), "payments.csv")
    blocked_n = ev("Payments on hold or failed", f"{len(blocked)}", "payments.csv")
    blocked_eur = ev("Amount on hold or failed", _eur(blocked["amount_eur"].sum()), "payments.csv")
    overdue_n = ev("Payments past their due date", f"{len(overdue)}", "payments.csv")
    overdue_eur = ev("Amount past due date", _eur(overdue["amount_eur"].sum()), "payments.csv")

    actions = []
    for _, r in blocked.iterrows():
        item = ev(f"{r['payment_id']}", f"{r['counterparty']}, {_eur(r['amount_eur'])}, due {r['due_date']:%d %b}",
                  "payments.csv")
        actions.append(Action(f"Decide on {r['payment_id']} ({item}): record why it is {r['status'].lower()} and "
                              "agree release or dispute before the due date.", "Finance lead (Finance)"))
    for _, r in overdue.sort_values("days_overdue", ascending=False).iterrows():
        item = ev(f"{r['payment_id']}", f"{r['counterparty']}, {_eur(r['amount_eur'])}, "
                  f"{int(r['days_overdue'])} days overdue", "payments.csv")
        if r["direction"] == "Incoming":
            actions.append(Action(f"Chase {r['payment_id']} ({item}).", "Accounts receivable (Finance)"))
        else:
            actions.append(Action(f"Pay or reschedule {r['payment_id']} ({item}) to protect supplier terms.",
                                  "Accounts payable (Finance)"))
    actions = actions[:3]

    escalated = p[p["needs_escalation"]]
    severity = "Critical" if len(escalated) else "High" if len(blocked) else "Medium"
    if len(escalated):
        esc_txt = ev("Failed or 7+ days overdue", f"{len(escalated)}", "payments.csv")
    score, parts = _score(severity, "eur", total, 0.9 if len(overdue) else 0.6)
    return Finding(
        kind="payments_blocked", area="Payments", title="Cash is blocked or overdue",
        problem=f"{total_txt} is blocked or overdue: {blocked_n} payment(s) on hold or failed ({blocked_eur}) "
                f"and {overdue_n} past the due date ({overdue_eur}).",
        root_cause=None, confidence=NOT_DETERMINED, actions=actions, severity=severity,
        escalate=bool(len(escalated)),
        escalation_reason=f"{esc_txt} payment(s) failed or 7+ days overdue." if len(escalated) else None,
        escalate_to=config.ESCALATION_PATHS["Payments"], impact_score=score, score_parts=parts,
        evidence=ev.items,
        data_gaps=["No hold reason, dispute notes or payment terms in the data: the cause of each hold or delay "
                   "cannot be determined."],
        references=blocked["payment_id"].tolist() + overdue["payment_id"].tolist(),
    )


# --- Detector 6: workload concentration ------------------------------------------------------------

def _workload_finding(e: Frames, as_of: datetime) -> Optional[Finding]:
    items = metrics.attention_items(e)
    urgent = items[items["severity"].isin(["Critical", "High"])]
    if len(urgent) < 4:
        return None
    by_owner = urgent["owner"].value_counts()
    owner, count = by_owner.index[0], int(by_owner.iloc[0])
    if count < 4 or count / len(urgent) < config.ASSISTANT_OVERLOAD_SHARE:
        return None
    ev = _Evidence()
    share_txt = ev(f"Open Critical/High issues owned by {owner}", f"{count} of {len(urgent)}", "all modules")
    score, parts = _score("High", "customers", count, 0.7)
    return Finding(
        kind="workload", area="General", title=f"{owner} is carrying too many urgent issues",
        problem=f"{owner} owns {share_txt} open Critical or High issues across the operation.",
        root_cause=None, confidence=NOT_DETERMINED,
        actions=[Action(f"Reassign part of {owner}'s urgent issues in today's stand-up.", "Operations manager (Operations)")],
        severity="High", escalate=False, escalation_reason=None, escalate_to="Operations manager",
        impact_score=score, score_parts=parts, evidence=ev.items,
        data_gaps=["No capacity or absence data: the data cannot show whether this owner has time for these issues."],
        references=urgent.loc[urgent["owner"] == owner, "reference"].tolist(),
    )


DETECTORS = [_carrier_finding, _stock_finding, _support_finding, _fulfilment_finding, _payment_finding,
             _workload_finding]
AREA_ORDER = {area: i for i, area in enumerate(metrics.AREAS + ["General"])}


def analyse(frames: Frames, as_of: datetime) -> AssistantReport:
    """Run every detector on company-wide data and rank the findings by impact."""
    e = metrics.enrich_all(frames, as_of)
    findings = [f for f in (detect(e, as_of) for detect in DETECTORS) if f is not None]
    findings.sort(key=lambda f: (-f.impact_score, -config.ASSISTANT_SEVERITY_WEIGHT[f.severity],
                                 AREA_ORDER[f.area]))
    covered = {f.area for f in findings}
    return AssistantReport(as_of=as_of, findings=findings,
                           areas_without_findings=[a for a in metrics.AREAS if a not in covered])


def unsupported_numbers(finding: Finding) -> List[str]:
    """Numbers in the finding's sentences that do not appear in its evidence. Should always be empty."""
    haystack = " ".join(f"{ev.label} {ev.display} {ev.source}" for ev in finding.evidence)
    known = set(numbers_in(haystack))
    return [n for n in numbers_in(finding.text()) if n not in known]
