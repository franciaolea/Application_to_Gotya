"""AI Operations Assistant: finds the right problems, proves its causes, never invents figures."""
from dataclasses import replace
from datetime import datetime

import pandas as pd
import pytest

from src import assistant, config, metrics

AS_OF = datetime(2026, 10, 30, 8, 0)
TODAY = pd.Timestamp("2026-10-30")


@pytest.fixture
def report(demo_bundle):
    return assistant.analyse(demo_bundle.frames, demo_bundle.as_of)


# --- On the demo scenario ----------------------------------------------------------

def test_top_three_are_the_planted_problems_in_impact_order(report):
    assert [f.kind for f in report.top] == ["carrier_performance", "stock_out", "support_sla"]
    assert report.findings[3].kind == "payments_blocked"      # detected, but lower impact
    scores = [f.impact_score for f in report.findings]
    assert scores == sorted(scores, reverse=True)
    assert report.areas_without_findings == ["Orders"]        # fulfilment is on schedule


def test_no_finding_contains_a_number_that_is_not_in_its_evidence(report):
    for finding in report.findings:
        assert assistant.unsupported_numbers(finding) == [], finding.title


def test_every_finding_is_actionable_and_owned(report):
    for f in report.findings:
        assert f.actions, f.title
        assert all("(" in a.owner_role for a in f.actions), "every action names a role and department"
        assert f.evidence and f.data_gaps
        assert f.escalate == (f.escalation_reason is not None)
        assert f.escalate_to


def test_carrier_finding_agrees_with_the_shipping_page(demo_bundle, report):
    e = metrics.enrich_all(demo_bundle.frames, demo_bundle.as_of)
    perf = metrics.carrier_performance(e["shipments"], demo_bundle.as_of).set_index("carrier")
    carrier = report.top[0]
    assert carrier.title.startswith("Parcelyn")
    assert f"{perf.loc['Parcelyn', 'on_time_rate']:.0%}" in carrier.problem
    very_late = e["shipments"][(e["shipments"]["carrier"] == "Parcelyn") & e["shipments"]["needs_escalation"]]
    assert carrier.escalate == (len(very_late) > 0)
    assert carrier.confidence == "High"  # both root-cause checks passed


def test_unproven_causes_are_reported_as_not_determined(report):
    payments = next(f for f in report.findings if f.kind == "payments_blocked")
    assert payments.root_cause is None and payments.confidence == assistant.NOT_DETERMINED
    assert "cannot be determined" in payments.data_gaps[0]


def test_a_supplier_is_not_blamed_on_too_little_evidence(report):
    stock = next(f for f in report.findings if f.kind == "stock_out")
    assert "supplier" not in (stock.root_cause or "").lower()  # only 2 of 5 affected items share a supplier


def test_quiet_day_produces_no_findings(quiet_frames, demo_bundle):
    report = assistant.analyse(quiet_frames, demo_bundle.as_of)
    assert report.findings == []
    assert report.areas_without_findings == metrics.AREAS


def test_analysis_is_deterministic(demo_bundle):
    first = assistant.analyse(demo_bundle.frames, demo_bundle.as_of)
    second = assistant.analyse(demo_bundle.frames, demo_bundle.as_of)
    assert [(f.title, f.problem, f.impact_score) for f in first.findings] == \
        [(f.title, f.problem, f.impact_score) for f in second.findings]


# --- The guardrail itself ----------------------------------------------------------------

def test_guardrail_catches_an_invented_number(report):
    finding = report.top[0]
    invented = replace(finding, problem=finding.problem + " Costs rose by 37% last month.")
    assert assistant.unsupported_numbers(invented) == ["37"]


# --- Detector rules on small, hand-made data ------------------------------------------------

def shipments(rows):
    """rows: (carrier, destination, delivered_on_time: bool, count)."""
    out = []
    for carrier, destination, on_time, count in rows:
        for _ in range(count):
            out.append({"shipment_id": f"S{len(out)}", "order_id": f"O{len(out)}", "carrier": carrier,
                        "destination": destination, "status": "Delivered",
                        "ship_date": TODAY - pd.Timedelta(days=6),
                        "expected_delivery_date": TODAY - pd.Timedelta(days=3),
                        "actual_delivery_date": TODAY - pd.Timedelta(days=3 if on_time else 1),
                        "owner": "Amara Okafor"})
    return metrics.enrich_shipments(pd.DataFrame(out), AS_OF)


NO_TICKETS = pd.DataFrame(columns=["order_id", "category", "created_at"])


def test_carriers_performing_alike_are_not_flagged():
    s = shipments([("A", "Spain", True, 27), ("A", "Spain", False, 3),
                   ("B", "Spain", True, 26), ("B", "Spain", False, 4)])
    assert assistant._carrier_finding({"shipments": s, "tickets": NO_TICKETS}, AS_OF) is None


def test_an_outlier_carrier_is_flagged_with_peer_evidence():
    s = shipments([("Good", "Spain", True, 28), ("Good", "Spain", False, 2),
                   ("Good", "France", True, 29), ("Good", "France", False, 1),
                   ("Slow", "Spain", True, 15), ("Slow", "Spain", False, 10),
                   ("Slow", "France", True, 14), ("Slow", "France", False, 11)])
    f = assistant._carrier_finding({"shipments": s, "tickets": NO_TICKETS}, AS_OF)
    assert f.title == "Slow is the main delivery bottleneck"
    assert "2 of 2 countries" in f.root_cause
    assert assistant.unsupported_numbers(f) == []


def test_small_carriers_are_not_judged():
    s = shipments([("Big", "Spain", True, 40), ("Tiny", "Spain", False, config.ASSISTANT_MIN_DELIVERIES - 1)])
    assert assistant._carrier_finding({"shipments": s, "tickets": NO_TICKETS}, AS_OF) is None
