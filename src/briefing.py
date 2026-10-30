"""Daily Operations Briefing: the morning report, built from the same rules as every page.

build_briefing() answers five questions in plain sentences:
1. What is running well?          -> running_well
2. What is delayed?               -> delayed
3. What needs immediate attention? -> attention (Critical + High issues)
4. Who is responsible?            -> owner on every issue, plus by_owner
5. What should be escalated?      -> escalations (Critical issues + who to escalate to)

to_markdown() turns it into text that can be pasted into an email or chat.
No Streamlit code here, so the whole briefing is unit tested.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from src import config, metrics
from src.metrics import Frames


@dataclass
class Briefing:
    as_of: datetime
    company: str
    headline: str
    area_status: Dict[str, str]
    running_well: List[str]
    delayed: List[str]
    insight: Optional[str]
    attention: pd.DataFrame      # Critical and High issues
    escalations: pd.DataFrame    # Critical issues, with who to escalate to
    by_owner: pd.DataFrame       # open issues per owner
    follow_ups: pd.DataFrame     # open actions from the Action Tracker that are due


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _names(values) -> str:
    """['A'] -> 'A'; ['A', 'B', 'C'] -> 'A, B and C'."""
    values = list(values)
    return values[0] if len(values) == 1 else ", ".join(values[:-1]) + " and " + values[-1]


def _plural(n: int, word: str, plural: Optional[str] = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _running_well(e: Frames, products: pd.DataFrame, kpi: dict, carriers: pd.DataFrame,
                  pay: dict, support: dict) -> List[str]:
    lines = []
    if kpi["orders_change"] is not None and kpi["orders_change"] > 0.05:
        lines.append(f"Demand is growing: {kpi['orders_processed']} orders in the last 7 days, "
                     f"up {kpi['orders_change']:.0%} on the previous week.")
    if kpi["stuck_orders"] == 0:
        lines.append(f"Fulfilment is on schedule: every order older than {config.ORDER_FULFILMENT_HOURS}h "
                     f"has shipped ({kpi['pending_orders']} orders in the normal pipeline).")
    good = carriers[carriers["on_time_rate"] >= config.ON_TIME_ACCEPTABLE]
    if len(good):
        rates = good["on_time_rate"].map("{:.0%}".format)
        if len(good) == 1:
            rate_text = f"{rates.iloc[0]} on time"
        elif rates.nunique() == 1:
            rate_text = f"{rates.iloc[0]} on time each"
        else:
            rate_text = f"{_names(rates)} on time respectively"
        lines.append(f"{_names(good['carrier'])} delivered {rate_text} over the last 28 days.")
    healthy = int((~products["needs_replenishment"]).sum())
    if healthy:
        lines.append(f"{healthy} of {len(products)} products are above their minimum stock.")
    if not e["payments"]["needs_escalation"].any():
        lines.append(f"No payments are failed or {config.PAYMENT_ESCALATION_DAYS}+ days overdue; "
                     f"{_plural(pay['paid_30d_count'], 'payment')} completed in the last 30 days.")
    if support["sla_compliance"] is not None and support["sla_compliance"] >= config.SLA_COMPLIANCE_TARGET:
        lines.append(f"Support resolved {_pct(support['sla_compliance'])} of tickets within SLA in the last "
                     f"7 days (target {config.SLA_COMPLIANCE_TARGET:.0%}).")
    return lines


def _delayed(e: Frames, products: pd.DataFrame, kpi: dict, pay: dict, support: dict) -> List[str]:
    lines = []
    orders = e["orders"]
    if kpi["stuck_orders"]:
        oldest = orders.loc[orders["is_stuck"], "age_hours"].max()
        lines.append(f"{_plural(kpi['stuck_orders'], 'order')} not shipped after "
                     f"{config.ORDER_FULFILMENT_HOURS}h (oldest: {oldest:.0f}h).")
    s = e["shipments"]
    delayed = s[s["is_delayed"]]
    if len(delayed):
        by_carrier = delayed["carrier"].value_counts()
        split = _names([f"{n} {c}" for c, n in by_carrier.items()])
        lines.append(f"{_plural(len(delayed), 'shipment')} past the promised delivery date "
                     f"({int(s['needs_escalation'].sum())} by {config.SHIPMENT_ESCALATION_DAYS}+ days): {split}.")
    exceptions = s[s["has_exception"] & s["is_open"]]
    if len(exceptions):
        lines.append(f"{_plural(len(exceptions), 'parcel')} with a carrier exception "
                     f"({_names([f'{n} {c}' for c, n in exceptions['carrier'].value_counts().items()])}).")
    out = products[products["stock_status"] == "Out of Stock"]
    if len(out):
        lines.append(f"Out of stock, losing sales until restocked: {_names(out['product_name'])}.")
    if pay["overdue_count"]:
        lines.append(f"{_plural(pay['overdue_count'], 'payment')} past due date "
                     f"(€{pay['overdue_eur']:,.0f}).")
    if pay["blocked_count"]:
        lines.append(f"{_plural(pay['blocked_count'], 'payment')} on hold or failed "
                     f"(€{pay['blocked_eur']:,.0f}) waiting for a decision.")
    if support["breached"]:
        lines.append(f"{_plural(support['breached'], 'support ticket')} past SLA "
                     f"({support['urgent_breached']} Critical/High priority).")
    if support["sla_compliance"] is not None and support["sla_compliance"] < config.SLA_COMPLIANCE_TARGET:
        lines.append(f"SLA compliance is {_pct(support['sla_compliance'])} against a "
                     f"{config.SLA_COMPLIANCE_TARGET:.0%} target.")
    return lines


def _carrier_insight(carriers: pd.DataFrame, complaints: pd.DataFrame) -> Optional[str]:
    """Name the weakest carrier when the gap to the others is too big to be noise."""
    rated = carriers.dropna(subset=["on_time_rate"]).sort_values("on_time_rate")
    if len(rated) < 2:
        return None
    worst, best = rated.iloc[0], rated.iloc[-1]
    if best["on_time_rate"] - worst["on_time_rate"] < 0.15:
        return None
    text = (f"{worst['carrier']} is the main delivery bottleneck: {worst['on_time_rate']:.0%} on time vs "
            f"{best['on_time_rate']:.0%} for {best['carrier']}")
    rates = complaints.set_index("carrier")["per_100_shipments"].dropna()
    if worst["carrier"] in rates.index and len(rates) > 1:
        others = rates.drop(worst["carrier"])
        text += (f", and it generates {rates[worst['carrier']]:.0f} delivery complaints per 100 parcels "
                 f"vs {others.min():.0f}-{others.max():.0f} for the other carriers")
    return text + ". Recommend a performance review with the carrier and shifting volume where possible."


def _follow_ups(actions: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
    """Open actions from the Action Tracker that are due today or overdue."""
    columns = ["action_id", "module", "issue", "owner", "status", "due_date", "days_overdue"]
    if actions.empty:
        return pd.DataFrame(columns=columns)
    today = pd.Timestamp(as_of).normalize()
    due = actions[(actions["status"] != "Resolved") & (actions["due_date"] <= today)].copy()
    due["days_overdue"] = (today - due["due_date"]).dt.days
    return due.sort_values("due_date")[columns].reset_index(drop=True)


def _by_owner(items: pd.DataFrame) -> pd.DataFrame:
    columns = ["owner", "critical", "high", "medium", "total", "areas"]
    if items.empty:
        return pd.DataFrame(columns=columns)
    counts = pd.crosstab(items["owner"], items["severity"].astype(str)) \
        .reindex(columns=metrics.SEVERITIES, fill_value=0)
    counts.columns = [c.lower() for c in counts.columns]
    counts["total"] = counts.sum(axis=1)
    counts["areas"] = items.groupby("owner")["area"].apply(lambda a: ", ".join(sorted(set(map(str, a)))))
    return counts.sort_values(["critical", "high", "total"], ascending=False) \
        .rename_axis("owner").reset_index()[columns]


def build_briefing(frames: Frames, as_of: datetime, company: str = "Operations") -> Briefing:
    e = metrics.enrich_all(frames, as_of)
    products = metrics.add_demand(e["products"], frames["orders"], as_of)
    kpi = metrics.executive_kpis(e, as_of, period_days=7)
    pay = metrics.payment_kpis(e["payments"], as_of)
    support = metrics.support_kpis(e["tickets"], as_of)
    carriers = metrics.carrier_performance(e["shipments"], as_of)
    complaints = metrics.delivery_contacts_by_carrier(e["tickets"], e["shipments"], as_of)

    items = metrics.attention_items(e)
    status = metrics.area_health(items).set_index("area")["status"].to_dict()
    attention = items[items["severity"].isin(["Critical", "High"])].reset_index(drop=True)
    escalations = items[items["escalate"]].reset_index(drop=True)
    escalations["escalate_to"] = escalations["area"].astype(str).map(config.ESCALATION_PATHS)

    n_escalate, n_high = len(escalations), int((items["severity"] == "High").sum())
    if n_escalate:
        headline = (f"{_plural(n_escalate, 'issue')} to escalate today and "
                    f"{_plural(n_high, 'high-priority issue')} to resolve before end of day.")
    elif n_high:
        headline = f"No escalations needed. {_plural(n_high, 'high-priority issue')} to resolve today."
    else:
        headline = "No critical or high-priority issues. Operations are running to plan."

    return Briefing(
        as_of=as_of, company=company, headline=headline, area_status=status,
        running_well=_running_well(e, products, kpi, carriers, pay, support),
        delayed=_delayed(e, products, kpi, pay, support),
        insight=_carrier_insight(carriers, complaints),
        attention=attention, escalations=escalations, by_owner=_by_owner(items),
        follow_ups=_follow_ups(frames["actions"], as_of),
    )


def to_markdown(b: Briefing) -> str:
    """Plain-text version for email or chat."""
    lines = [f"# Daily Operations Briefing - {b.company}",
             f"{b.as_of:%A %d %B %Y, %H:%M}", "", f"**{b.headline}**", "",
             "Status: " + " | ".join(f"{area}: {state}" for area, state in b.area_status.items()), ""]

    lines += ["## Escalate today"]
    if b.escalations.empty:
        lines.append("- Nothing to escalate.")
    for _, r in b.escalations.iterrows():
        lines.append(f"- [{r['area']}] {r['issue']} ({r['reference']}, {r['detail']}). "
                     f"Owner: {r['owner']}. Escalate to: {r['escalate_to']}.")

    lines += ["", "## Requires immediate attention"]
    high = b.attention[b.attention["severity"] == "High"]
    if high.empty:
        lines.append("- No other high-priority issues.")
    for _, r in high.iterrows():
        lines.append(f"- [{r['area']}] {r['issue']} ({r['reference']}, {r['detail']}). Owner: {r['owner']}.")

    lines += ["", "## Delayed"] + ([f"- {line}" for line in b.delayed] or ["- Nothing delayed."])
    lines += ["", "## Running well"] + ([f"- {line}" for line in b.running_well] or ["- (none)"])
    if b.insight:
        lines += ["", "## Key insight", b.insight]
    if not b.follow_ups.empty:
        lines += ["", "## Action follow-ups due"]
        for _, r in b.follow_ups.iterrows():
            late = f", {_plural(int(r['days_overdue']), 'day')} overdue" if r["days_overdue"] > 0 else ", due today"
            lines.append(f"- {r['action_id']} {r['issue']}. Owner: {r['owner']} ({r['status']}{late}).")
    lines += ["", "## Open issues by owner"]
    for _, r in b.by_owner.iterrows():
        lines.append(f"- {r['owner']}: {r['total']} ({r['critical']} critical, {r['high']} high) - {r['areas']}")
    return "\n".join(lines) + "\n"
