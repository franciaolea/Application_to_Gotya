"""Action Tracker rules: every change goes through these, so they must hold."""
from datetime import date

import pandas as pd
import pytest

from src import actions as act
from src.data_loader import load_all

TODAY = pd.Timestamp("2026-10-30")


@pytest.fixture
def actions(demo_bundle):
    return demo_bundle.frames["actions"]


def test_new_action_gets_next_id_starts_open_and_dates_its_comment(actions):
    updated, new_id = act.create_action(actions, issue="Call carrier", module="Shipping", priority="High",
                                        today=TODAY, owner="Amara Okafor", comment="Opened from briefing")
    row = updated.set_index("action_id").loc[new_id]
    assert new_id == "ACT-006" and len(updated) == len(actions) + 1
    assert row["status"] == "Open" and row["created_date"] == TODAY
    assert row["comments"] == "2026-10-30: Opened from briefing"


def test_resolving_records_the_date_and_reopening_clears_it(actions):
    resolved = act.update_action(actions, "ACT-001", today=TODAY, status="Resolved")
    assert resolved.set_index("action_id").loc["ACT-001", "resolution_date"] == TODAY
    reopened = act.update_action(resolved, "ACT-001", today=TODAY, status="In Progress")
    assert pd.isna(reopened.set_index("action_id").loc["ACT-001", "resolution_date"])


def test_comments_are_added_never_overwritten(actions):
    once = act.update_action(actions, "ACT-005", today=TODAY, comment="First")
    twice = act.update_action(once, "ACT-005", today=TODAY + pd.Timedelta(days=1), comment="Second")
    assert twice.set_index("action_id").loc["ACT-005", "comments"] == "2026-10-30: First | 2026-10-31: Second"


@pytest.mark.parametrize("change, message", [
    (dict(status="Blocked"), "blocked by"),                                  # needs a reason
    (dict(status="In Progress", owner=""), "needs an owner"),
    (dict(due_date="2026-01-01"), "cannot be before"),
    (dict(status="Done"), "Unknown status"),
])
def test_rule_breaking_changes_are_rejected(actions, change, message):
    with pytest.raises(act.ActionError, match=message):
        act.update_action(actions, "ACT-005", today=TODAY, **change)


def test_blocking_with_a_comment_is_allowed(actions):
    updated = act.update_action(actions, "ACT-005", today=TODAY, status="Blocked", comment="Waiting on HR")
    assert updated.set_index("action_id").loc["ACT-005", "status"] == "Blocked"


def test_an_empty_issue_cannot_be_created(actions):
    with pytest.raises(act.ActionError, match="Describe the issue"):
        act.create_action(actions, issue="  ", module="General", priority="Low", today=TODAY)


def test_changes_between_ignores_cosmetic_differences(actions):
    original = actions.copy()
    edited = actions.copy()
    edited["due_date"] = edited["due_date"].dt.date          # the table editor returns plain dates
    edited["owner"] = edited["owner"].fillna("")             # and empty strings instead of blanks
    assert act.changes_between(original, edited) == []
    edited.loc[edited["action_id"] == "ACT-002", "status"] = "In Progress"
    edited.loc[edited["action_id"] == "ACT-005", "due_date"] = date(2026, 11, 20)
    assert act.changes_between(original, edited) == [
        ("ACT-002", {"status": "In Progress"}), ("ACT-005", {"due_date": date(2026, 11, 20)})]


def test_save_round_trip_and_no_temp_files_left(actions, demo_dir):
    updated = act.update_action(actions, "ACT-002", today=TODAY, status="Resolved", comment="Address confirmed")
    act.save_actions(updated, demo_dir)
    reloaded = load_all(demo_dir, mode="demo")
    assert reloaded.issues == []
    row = reloaded.frames["actions"].set_index("action_id").loc["ACT-002"]
    assert row["status"] == "Resolved" and row["resolution_date"] == TODAY
    assert not list(demo_dir.glob(".actions-*"))


def test_invalid_table_is_not_saved(actions, demo_dir):
    before = (demo_dir / "actions.csv").read_text()
    broken = actions.copy()
    broken.loc[0, "status"] = "Maybe"                        # bypassing update_action
    with pytest.raises(act.ActionError, match="Not saved"):
        act.save_actions(broken, demo_dir)
    assert (demo_dir / "actions.csv").read_text() == before


def test_summary_and_overdue(actions):
    s = act.summary(actions, TODAY)
    enriched = act.enrich_actions(actions, TODAY).set_index("action_id")
    assert s["overdue"] == int(enriched["is_overdue"].sum()) >= 1
    assert not enriched.loc[enriched["status"] == "Resolved", "is_overdue"].any()


def test_issues_with_an_open_action_are_not_offered_again(actions):
    items = pd.DataFrame({"reference": ["LUM-019", "SHP-1", "TCK-3012"]})
    # LUM-019 has an open action (ACT-001); TCK-3012's action (ACT-004) is resolved, so it can be tracked again.
    assert list(act.untracked_issues(items, actions)["reference"]) == ["SHP-1", "TCK-3012"]


def test_reset_only_works_on_demo_data(tmp_path):
    with pytest.raises(act.ActionError, match="only available for demo"):
        act.reset_demo_actions(tmp_path, {"data_classification": "LIVE"})


# --- Linking open issues to actions -------------------------------------------------------

def issues(*refs):
    return pd.DataFrame({"reference": list(refs), "severity": "High"})


def action_rows(*rows):
    """rows: (action_id, reference_id, status, created_days_ago)."""
    return pd.DataFrame([{"action_id": a, "reference_id": r, "status": s,
                          "created_date": TODAY - pd.Timedelta(days=d)} for a, r, s, d in rows])


def test_every_action_status_is_recognised_as_a_link():
    linked = act.link_issues_to_actions(
        issues("NONE", "OPEN", "WIP", "BLOCKED", "REDONE"),
        action_rows(("A1", "OPEN", "Open", 1), ("A2", "WIP", "In Progress", 1),
                    ("A3", "BLOCKED", "Blocked", 1), ("A4", "REDONE", "Resolved", 1))).set_index("reference")
    assert linked.loc["NONE", "action_status"] == act.NO_ACTION and linked.loc["NONE", "action_id"] == ""
    assert linked.loc["OPEN", "action_status"] == "Open"
    assert linked.loc["WIP", "action_status"] == "In Progress"
    assert linked.loc["BLOCKED", "action_status"] == "Blocked"
    # Resolved action, but the issue is still in the data: not "no action", but flagged for re-check.
    assert linked.loc["REDONE", "action_status"] == act.RESOLVED_BUT_OPEN and linked.loc["REDONE", "action_id"] == "A4"


def test_with_several_actions_the_one_needing_attention_is_shown():
    linked = act.link_issues_to_actions(
        issues("X", "Y"),
        action_rows(("OLD", "X", "Resolved", 9), ("NEW", "X", "Open", 1),
                    ("A", "Y", "Open", 1), ("B", "Y", "Blocked", 3))).set_index("reference")
    assert linked.loc["X", "action_id"] == "NEW"      # an unfinished action beats an old resolved one
    assert linked.loc["Y", "action_id"] == "B"        # blocked is surfaced before open


def test_references_match_regardless_of_case_and_spaces():
    linked = act.link_issues_to_actions(issues("SHP-5634"), action_rows(("A1", "  shp-5634 ", "Open", 1)))
    assert linked.iloc[0]["action_id"] == "A1"


def test_summary_counts_each_state_and_resolved_issues():
    summary = act.issue_action_summary(
        issues("NONE", "OPEN", "BLOCKED", "REDONE"),
        action_rows(("A1", "OPEN", "Open", 1), ("A2", "BLOCKED", "Blocked", 1),
                    ("A3", "REDONE", "Resolved", 1), ("A4", "GONE", "Resolved", 2), ("A5", "", "Open", 1)))
    assert summary["total"] == 4
    assert (summary["No action"], summary["Open"], summary["Blocked"]) == (1, 1, 1)
    assert summary[act.RESOLVED_BUT_OPEN] == 1
    assert summary["with_action"] == 2
    assert summary["resolved_issues"] == 1          # A4: its issue no longer appears; A5 has no reference


def test_untracked_means_no_unfinished_action():
    items = issues("NONE", "OPEN", "REDONE")
    untracked = act.untracked_issues(items, action_rows(("A1", "OPEN", "In Progress", 1), ("A2", "REDONE", "Resolved", 1)))
    assert list(untracked["reference"]) == ["NONE", "REDONE"]


def test_demo_scenario_keeps_its_realistic_gaps(demo_bundle):
    """17 Critical/High issues; only 2 are being handled. The logic must report that, not hide it."""
    from src import metrics
    items = metrics.attention_items(metrics.enrich_all(demo_bundle.frames, demo_bundle.as_of))
    urgent = items[items["severity"].isin(["Critical", "High"])]
    summary = act.issue_action_summary(urgent, demo_bundle.frames["actions"])
    assert (summary["total"], summary["No action"], summary["Open"], summary["In Progress"]) == (17, 15, 1, 1)
    everything = act.issue_action_summary(items, demo_bundle.frames["actions"])
    assert everything["Blocked"] == 1                # ACT-003 covers a Medium issue (PAY-7021)
    assert everything["resolved_issues"] == 1        # ACT-004: its ticket is no longer an open issue
