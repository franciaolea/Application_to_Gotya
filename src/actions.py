"""Action Tracker: the business rules for creating, updating and saving actions.

An action is a task someone owns to resolve an operational issue, e.g.
"Expedite PO for out-of-stock Jute Area Rug" (owner Priya, due Monday).

Rules enforced here (so the page cannot bypass them):
- Marking an action Resolved records today's date as the resolution date;
  reopening it clears that date.
- An action that is In Progress, Blocked or Resolved must have an owner.
- Moving an action to Blocked requires a comment explaining why.
- The due date cannot be before the date the action was created.
- Comments are never overwritten: each one is added to the history with its date.
- Before saving, the whole table is checked with the same validation used on load,
  then written atomically (temporary file, then swapped in), so a crash mid-save
  cannot leave a half-written CSV.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd

from src import config
from src.validation import ACTION_MODULES, ACTION_STATUSES, ERROR, PRIORITIES, SCHEMAS, validate_dataset

NEEDS_OWNER = ["In Progress", "Blocked", "Resolved"]
COMMENT_SEPARATOR = " | "
MAX_ISSUE_LENGTH = 200


class ActionError(ValueError):
    """A change that breaks a business rule. The message is shown to the user."""


def _day(value) -> pd.Timestamp:
    return pd.Timestamp(value).normalize()


def _blank(value) -> bool:
    return value is None or (isinstance(value, float) and pd.isna(value)) or str(value).strip() == ""


def _typed(actions: pd.DataFrame) -> pd.DataFrame:
    """Keep date columns as real dates after rows are added or changed."""
    out = actions.copy()
    for col in SCHEMAS["actions"].date_columns:
        out[col] = pd.to_datetime(out[col])
    return out


def next_action_id(actions: pd.DataFrame) -> str:
    numbers = actions["action_id"].str.extract(r"(\d+)$")[0].dropna().astype(int)
    return f"ACT-{(numbers.max() + 1 if len(numbers) else 1):03d}"


def add_comment(history: str, comment: str, today) -> str:
    """Append "2026-10-30: comment" to the existing history."""
    entry = f"{_day(today):%Y-%m-%d}: {comment.strip()}"
    return entry if _blank(history) else f"{history}{COMMENT_SEPARATOR}{entry}"


def _check(action: pd.Series, changed_to_blocked: bool, new_comment: Optional[str]) -> None:
    if _blank(action["issue"]):
        raise ActionError("Describe the issue the action addresses.")
    if len(str(action["issue"])) > MAX_ISSUE_LENGTH:
        raise ActionError(f"Keep the issue description under {MAX_ISSUE_LENGTH} characters.")
    if action["status"] not in ACTION_STATUSES:
        raise ActionError(f"Unknown status '{action['status']}'.")
    if action["priority"] not in PRIORITIES:
        raise ActionError(f"Unknown priority '{action['priority']}'.")
    if action["module"] not in ACTION_MODULES:
        raise ActionError(f"Unknown area '{action['module']}'.")
    if action["status"] in NEEDS_OWNER and _blank(action["owner"]):
        raise ActionError(f"An action that is {action['status']} needs an owner.")
    if changed_to_blocked and _blank(new_comment):
        raise ActionError("Add a comment explaining what the action is blocked by.")
    if pd.notna(action["due_date"]) and _day(action["due_date"]) < _day(action["created_date"]):
        raise ActionError("The due date cannot be before the action was created.")


def create_action(actions: pd.DataFrame, *, issue: str, module: str, priority: str, today,
                  owner: str = "", due_date=None, reference_id: str = "",
                  comment: str = "") -> Tuple[pd.DataFrame, str]:
    """Return (updated table, new action id). New actions start as Open."""
    action_id = next_action_id(actions)
    row = pd.Series({
        "action_id": action_id, "created_date": _day(today), "module": module,
        "reference_id": reference_id.strip(), "issue": issue.strip(), "owner": (owner or "").strip(),
        "priority": priority, "status": "Open",
        "due_date": _day(due_date) if due_date is not None else pd.NaT,
        "resolution_date": pd.NaT, "comments": add_comment("", comment, today) if comment.strip() else "",
    })
    _check(row, changed_to_blocked=False, new_comment=comment)
    rows = actions.to_dict("records") + [row.to_dict()]
    return _typed(pd.DataFrame(rows, columns=list(actions.columns))), action_id


def update_action(actions: pd.DataFrame, action_id: str, *, today, status: Optional[str] = None,
                  owner: Optional[str] = None, priority: Optional[str] = None, due_date=None,
                  comment: Optional[str] = None) -> pd.DataFrame:
    """Apply a change to one action and return the updated table. Only the given fields change."""
    matches = actions.index[actions["action_id"] == action_id]
    if len(matches) != 1:
        raise ActionError(f"Action {action_id} not found.")
    out = actions.copy()
    idx = matches[0]
    action = out.loc[idx].copy()
    previous_status = action["status"]

    if owner is not None:
        action["owner"] = owner.strip()
    if priority is not None:
        action["priority"] = priority
    if due_date is not None:
        action["due_date"] = _day(due_date)
    if status is not None:
        action["status"] = status
        if status == "Resolved" and previous_status != "Resolved":
            action["resolution_date"] = _day(today)      # record when it was resolved
        elif status != "Resolved":
            action["resolution_date"] = pd.NaT            # reopened: no longer resolved

    changed_to_blocked = status == "Blocked" and previous_status != "Blocked"
    _check(action, changed_to_blocked, comment)
    if comment is not None and comment.strip():
        action["comments"] = add_comment(action["comments"], comment, today)
    out.loc[idx] = action
    return _typed(out)


def enrich_actions(actions: pd.DataFrame, today) -> pd.DataFrame:
    df = actions.copy()
    today = _day(today)
    df["is_open"] = df["status"] != "Resolved"
    df["days_overdue"] = ((today - df["due_date"]).dt.days.clip(lower=0).fillna(0).astype(int)
                          .where(df["is_open"], 0))
    df["is_overdue"] = df["days_overdue"] > 0
    df["due_today"] = df["is_open"] & (df["due_date"] == today)
    return df


def summary(actions: pd.DataFrame, today) -> dict:
    a = enrich_actions(actions, today)
    # A live work log, so today counts: the last 7 days including today (unlike period KPIs).
    week_ago = _day(today) - pd.Timedelta(days=6)
    counts = a["status"].value_counts()
    return {
        "open": int(counts.get("Open", 0)),
        "in_progress": int(counts.get("In Progress", 0)),
        "blocked": int(counts.get("Blocked", 0)),
        "overdue": int(a["is_overdue"].sum()),
        "due_today": int(a["due_today"].sum()),
        "resolved_7d": int((a["resolution_date"] >= week_ago).sum()),
        "unassigned": int((a["is_open"] & a["owner"].map(_blank)).sum()),
    }


# --- Linking open issues to actions ---------------------------------------------------------

NO_ACTION = "No action"
RESOLVED_BUT_OPEN = "Action resolved, issue still open"
LINK_STATES = [NO_ACTION, "Open", "In Progress", "Blocked", RESOLVED_BUT_OPEN]
WORKING_STATES = ["Open", "In Progress", "Blocked"]   # an action exists and is not finished
# When several actions cover one issue, show the one that most needs attention.
_LINK_PRECEDENCE = {"Blocked": 0, "In Progress": 1, "Open": 2, "Resolved": 3}


def _key(values: pd.Series) -> pd.Series:
    return values.fillna("").astype(str).str.strip().str.upper()


def link_issues_to_actions(items: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    """Add `action_id` and `action_status` (one of LINK_STATES) to each open issue.

    An issue and an action are linked when the action's reference_id is the issue's
    reference (e.g. SHP-5634). Every action status counts: Open, In Progress and
    Blocked mean the issue is being handled; a Resolved action whose issue still
    appears in the data is shown as "Action resolved, issue still open", not as
    "No action", because someone believed it was fixed and it needs re-checking.
    """
    out = items.copy()
    linked = actions[_key(actions["reference_id"]) != ""].assign(_ref=lambda a: _key(a["reference_id"]))
    linked = linked.assign(_rank=linked["status"].map(_LINK_PRECEDENCE)) \
        .sort_values(["_rank", "created_date"], ascending=[True, False]).drop_duplicates("_ref")
    by_ref = linked.set_index("_ref")
    refs = _key(out["reference"])
    out["action_id"] = refs.map(by_ref["action_id"]).fillna("")
    status = refs.map(by_ref["status"])
    out["action_status"] = status.replace({"Resolved": RESOLVED_BUT_OPEN}).fillna(NO_ACTION)
    return out


def issue_action_summary(items: pd.DataFrame, actions: pd.DataFrame) -> dict:
    """How many of the given open issues are in each link state, plus issues resolved through actions.

    'resolved_issues' = resolved actions whose issue no longer appears among open issues.
    """
    linked = link_issues_to_actions(items, actions)
    counts = {state: int((linked["action_status"] == state).sum()) for state in LINK_STATES}
    open_refs = set(_key(items["reference"]))
    resolved = actions[(actions["status"] == "Resolved") & (_key(actions["reference_id"]) != "")]
    counts["with_action"] = sum(counts[s] for s in WORKING_STATES)
    counts["total"] = len(items)
    counts["resolved_issues"] = int((~_key(resolved["reference_id"]).isin(open_refs)).sum())
    return counts


def untracked_issues(items: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    """Open issues that no unfinished action covers: 'No action' or 'Action resolved, issue still open'."""
    linked = link_issues_to_actions(items, actions)
    return items[~linked["action_status"].isin(WORKING_STATES).values]


def to_text(actions: pd.DataFrame) -> pd.DataFrame:
    """The table as CSV text: dates as YYYY-MM-DD, blanks as empty strings."""
    text = actions.copy()
    for col in SCHEMAS["actions"].date_columns:
        text[col] = pd.to_datetime(text[col]).dt.strftime("%Y-%m-%d").fillna("")
    return text.fillna("").astype(str)


def save_actions(actions: pd.DataFrame, directory: Path) -> Path:
    """Validate, then write atomically. Raises ActionError instead of saving bad data."""
    text = to_text(actions)[list(SCHEMAS["actions"].columns)]
    errors = [i.message for i in validate_dataset("actions", text) if i.severity == ERROR]
    if errors:
        raise ActionError("Not saved: " + " ".join(errors))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / config.DATASET_FILES["actions"]
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".actions-", suffix=".csv")
    try:
        with os.fdopen(fd, "w", newline="") as handle:
            text.to_csv(handle, index=False)
        os.replace(tmp, target)  # atomic on the same file system
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return target


def reset_demo_actions(directory: Path, metadata: dict) -> Path:
    """Demo mode only: restore the original demo actions (same snapshot date and seed)."""
    from src import demo_data
    from src.data_loader import to_typed

    if metadata.get("data_classification") != "FICTIONAL DEMO DATA":
        raise ActionError("Reset is only available for demo data.")
    as_of = pd.Timestamp(metadata["as_of_date"]).date()
    fresh = demo_data.generate_all(as_of, metadata.get("seed", demo_data.SEED))["actions"]
    return save_actions(to_typed("actions", fresh.astype(str)), directory)


EDITABLE_FIELDS = ["owner", "priority", "status", "due_date"]


def changes_between(original: pd.DataFrame, edited: pd.DataFrame) -> list:
    """Compare the table before and after in-place editing.

    Returns [(action_id, {field: new_value}), ...] for rows that really changed,
    so each change goes through update_action() and its rules.
    """
    def same(a, b) -> bool:
        if _blank(a) and _blank(b):
            return True
        if isinstance(a, (pd.Timestamp, )) or isinstance(b, (pd.Timestamp, )) or hasattr(b, "isoformat"):
            try:
                return _day(a) == _day(b)
            except (TypeError, ValueError):
                return False
        return str(a).strip() == str(b).strip()

    before = original.set_index("action_id")
    changes = []
    for _, row in edited.iterrows():
        old = before.loc[row["action_id"]]
        fields = {f: row[f] for f in EDITABLE_FIELDS if not same(old[f], row[f])}
        if fields:
            changes.append((row["action_id"], fields))
    return changes


def known_owners(frames: dict) -> list:
    """Everyone who owns something anywhere in the data: the choices for assigning actions."""
    names = set()
    for df in frames.values():
        if "owner" in df.columns:
            names |= {str(n).strip() for n in df["owner"].dropna() if str(n).strip()}
    return sorted(names)
