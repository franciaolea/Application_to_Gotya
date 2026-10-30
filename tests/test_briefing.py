"""The Daily Briefing must answer all five questions from the brief, consistently with every page."""
from datetime import datetime

import pandas as pd

from src import briefing, config, metrics

AS_OF = datetime(2026, 10, 30, 8, 0)


def test_briefing_covers_all_five_questions(demo_bundle):
    b = briefing.build_briefing(demo_bundle.frames, demo_bundle.as_of, "Test Co")
    assert b.running_well                       # 1. what is running well
    assert b.delayed                            # 2. what is delayed
    assert not b.attention.empty                # 3. what needs immediate attention
    assert b.attention["owner"].notna().all()   # 4. who is responsible for each issue
    assert not b.escalations.empty              # 5. what should be escalated ...
    assert b.escalations["escalate_to"].notna().all()  # ... and to whom


def test_briefing_agrees_with_the_dashboard(demo_bundle):
    """Same rules everywhere: the briefing escalates exactly the dashboard's Critical issues."""
    e = metrics.enrich_all(demo_bundle.frames, demo_bundle.as_of)
    items = metrics.attention_items(e)
    b = briefing.build_briefing(demo_bundle.frames, demo_bundle.as_of)
    assert list(b.escalations["reference"]) == list(items.loc[items["escalate"], "reference"])
    assert b.area_status == metrics.area_health(items).set_index("area")["status"].to_dict()
    assert f"{len(b.escalations)} issues to escalate" in b.headline


def test_markdown_export_has_every_section(demo_bundle):
    text = briefing.to_markdown(briefing.build_briefing(demo_bundle.frames, demo_bundle.as_of, "Test Co"))
    for heading in ["# Daily Operations Briefing - Test Co", "## Escalate today", "## Requires immediate attention",
                    "## Delayed", "## Running well", "## Open issues by owner"]:
        assert heading in text


def carriers(rates):
    return pd.DataFrame({"carrier": list(rates), "on_time_rate": list(rates.values())})


def test_bottleneck_is_named_only_when_the_gap_is_real():
    no_complaints = pd.DataFrame(columns=["carrier", "per_100_shipments"])
    assert briefing._carrier_insight(carriers({"A": 0.91, "B": 0.85}), no_complaints) is None
    insight = briefing._carrier_insight(carriers({"A": 0.92, "Slow": 0.60}), pd.DataFrame(
        {"carrier": ["A", "Slow"], "per_100_shipments": [5.0, 20.0]}))
    assert insight.startswith("Slow is the main delivery bottleneck: 60% on time vs 92% for A")
    assert "20 delivery complaints per 100 parcels" in insight


def test_follow_ups_are_open_actions_due_today_or_earlier():
    today = pd.Timestamp("2026-10-30")
    actions = pd.DataFrame({
        "action_id": ["late", "today", "future", "done"], "module": "General", "issue": "x", "owner": "y",
        "status": ["Open", "In Progress", "Open", "Resolved"],
        "due_date": [today - pd.Timedelta(days=2), today, today + pd.Timedelta(days=3),
                     today - pd.Timedelta(days=5)],
    })
    due = briefing._follow_ups(actions, AS_OF)
    assert list(due["action_id"]) == ["late", "today"]
    assert list(due["days_overdue"]) == [2, 0]


def test_quiet_day_has_no_escalations(demo_bundle):
    """Remove every problem from the demo data: the briefing should say all is well."""
    f = {name: df.copy() for name, df in demo_bundle.frames.items()}
    as_of = demo_bundle.as_of
    f["orders"]["status"] = "Delivered"
    s = f["shipments"]
    s["status"] = "Delivered"
    s["actual_delivery_date"] = s["expected_delivery_date"].where(
        s["expected_delivery_date"] < pd.Timestamp(as_of), pd.Timestamp(as_of).normalize() - pd.Timedelta(days=1))
    f["products"]["stock_on_hand"] = f["products"]["min_stock"] * 3
    f["payments"]["status"] = "Paid"
    f["payments"]["paid_date"] = f["payments"]["due_date"]
    f["tickets"]["status"] = "Resolved"
    f["tickets"]["resolved_at"] = f["tickets"]["created_at"] + pd.Timedelta(hours=1)
    b = briefing.build_briefing(f, as_of)
    assert b.escalations.empty and b.attention.empty
    assert set(b.area_status.values()) == {"On track"}
    assert b.headline.startswith("No critical or high-priority issues")
    assert config.ESCALATION_PATHS  # escalation contacts are configured even on quiet days
