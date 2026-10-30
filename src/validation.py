"""Data validation: check every CSV before the app trusts it.

Two levels of checks:
1. Per dataset (validate_dataset): required columns, blanks, dates, numbers,
   allowed values and unique IDs. Runs on the *raw text* exactly as read from
   the CSV, so we can report precisely which values are malformed.
2. Across datasets (validate_relationships): e.g. every shipment must point to
   an order that exists.

Issues are reported, never silently fixed. "error" means values are unusable
(they will show as blank in the app); "warning" means suspicious but usable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

import pandas as pd

# Dates must be ISO 8601: "2026-10-30" or "2026-10-30 08:00".
DATE_FORMAT = "ISO8601"

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class DatasetSchema:
    id_column: str
    columns: Sequence[str]                      # all required columns
    date_columns: Sequence[str] = ()
    numeric_columns: Sequence[str] = ()
    nullable: Sequence[str] = ()                # columns allowed to be blank
    allowed_values: Dict[str, Sequence[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationIssue:
    dataset: str
    severity: str
    column: str
    message: str
    rows_affected: int = 0


ORDER_STATUSES = ["Pending", "Processing", "Shipped", "Delivered", "Cancelled"]
SHIPMENT_STATUSES = ["In Transit", "Out for Delivery", "Delivered", "Exception"]
PAYMENT_STATUSES = ["Pending", "Processing", "Paid", "Failed", "On Hold"]
TICKET_STATUSES = ["Open", "In Progress", "Waiting on Customer", "Resolved", "Closed"]
PRIORITIES = ["Critical", "High", "Medium", "Low"]
ACTION_STATUSES = ["Open", "In Progress", "Blocked", "Resolved"]
ACTION_MODULES = ["Inventory", "Shipping", "Payments", "Support", "General"]

SCHEMAS: Dict[str, DatasetSchema] = {
    "products": DatasetSchema(
        id_column="sku",
        columns=["sku", "product_name", "category", "supplier", "warehouse", "stock_on_hand",
                 "min_stock", "reorder_qty", "lead_time_days", "unit_cost", "unit_price",
                 "last_restock_date", "owner"],
        date_columns=["last_restock_date"],
        numeric_columns=["stock_on_hand", "min_stock", "reorder_qty", "lead_time_days",
                         "unit_cost", "unit_price"],
    ),
    "orders": DatasetSchema(
        id_column="order_id",
        columns=["order_id", "order_date", "customer_name", "channel", "region", "sku",
                 "quantity", "order_value", "status"],
        date_columns=["order_date"],
        numeric_columns=["quantity", "order_value"],
        allowed_values={"status": ORDER_STATUSES},
    ),
    "shipments": DatasetSchema(
        id_column="shipment_id",
        columns=["shipment_id", "order_id", "carrier", "tracking_number", "destination", "status",
                 "ship_date", "expected_delivery_date", "actual_delivery_date", "owner"],
        date_columns=["ship_date", "expected_delivery_date", "actual_delivery_date"],
        nullable=["actual_delivery_date"],
        allowed_values={"status": SHIPMENT_STATUSES},
    ),
    "payments": DatasetSchema(
        id_column="payment_id",
        columns=["payment_id", "type", "direction", "counterparty", "reference", "amount_eur",
                 "status", "due_date", "paid_date", "owner"],
        date_columns=["due_date", "paid_date"],
        numeric_columns=["amount_eur"],
        nullable=["reference", "paid_date"],
        allowed_values={"status": PAYMENT_STATUSES, "direction": ["Incoming", "Outgoing"]},
    ),
    "tickets": DatasetSchema(
        id_column="ticket_id",
        columns=["ticket_id", "created_at", "customer_name", "order_id", "channel", "category",
                 "subject", "priority", "status", "sla_deadline", "resolved_at", "owner"],
        date_columns=["created_at", "sla_deadline", "resolved_at"],
        nullable=["order_id", "resolved_at"],
        allowed_values={"status": TICKET_STATUSES, "priority": PRIORITIES},
    ),
    "actions": DatasetSchema(
        id_column="action_id",
        columns=["action_id", "created_date", "module", "reference_id", "issue", "owner",
                 "priority", "status", "due_date", "resolution_date", "comments"],
        date_columns=["created_date", "due_date", "resolution_date"],
        nullable=["reference_id", "owner", "due_date", "resolution_date", "comments"],
        allowed_values={"status": ACTION_STATUSES, "priority": PRIORITIES, "module": ACTION_MODULES},
    ),
}


def validate_dataset(name: str, raw: pd.DataFrame) -> List[ValidationIssue]:
    """Check one dataset. `raw` must hold the CSV values as text."""
    schema = SCHEMAS[name]
    issues: List[ValidationIssue] = []

    def add(severity: str, column: str, message: str, rows: int = 0) -> None:
        issues.append(ValidationIssue(name, severity, column, message, rows))

    missing = [c for c in schema.columns if c not in raw.columns]
    for col in missing:
        add(ERROR, col, f"Required column '{col}' is missing.")
    if missing and schema.id_column in missing:
        return issues  # without an ID column the remaining checks are meaningless

    if raw.empty:
        add(WARNING, "", "Dataset has no rows.")
        return issues

    text = raw.fillna("").astype(str).apply(lambda s: s.str.strip())

    for col in schema.columns:
        if col in missing or col in schema.nullable:
            continue
        blanks = int((text[col] == "").sum())
        if blanks:
            add(ERROR, col, f"{blanks} row(s) have a blank '{col}'.", blanks)

    for col in schema.date_columns:
        if col in missing:
            continue
        filled = text[col] != ""
        parsed = pd.to_datetime(text.loc[filled, col], format=DATE_FORMAT, errors="coerce")
        bad = int(parsed.isna().sum())
        if bad:
            add(ERROR, col, f"{bad} value(s) in '{col}' are not valid dates (expected YYYY-MM-DD).", bad)

    for col in schema.numeric_columns:
        if col in missing:
            continue
        filled = text[col] != ""
        numbers = pd.to_numeric(text.loc[filled, col], errors="coerce")
        bad = int(numbers.isna().sum())
        if bad:
            add(ERROR, col, f"{bad} value(s) in '{col}' are not numbers.", bad)
        negative = int((numbers < 0).sum())
        if negative:
            add(ERROR, col, f"{negative} value(s) in '{col}' are negative.", negative)

    for col, allowed in schema.allowed_values.items():
        if col in missing:
            continue
        unexpected = text[col][(text[col] != "") & ~text[col].isin(allowed)]
        if len(unexpected):
            examples = ", ".join(sorted(unexpected.unique())[:3])
            add(ERROR, col, f"{len(unexpected)} value(s) in '{col}' are not allowed "
                            f"(e.g. {examples}). Allowed: {', '.join(allowed)}.", len(unexpected))

    ids = text[schema.id_column]
    duplicates = ids[(ids != "") & ids.duplicated(keep=False)]
    if len(duplicates):
        add(ERROR, schema.id_column,
            f"Duplicate IDs found: {', '.join(sorted(duplicates.unique())[:5])}.", len(duplicates))

    return issues


def _missing_refs(child: pd.DataFrame, column: str, parent: pd.DataFrame, parent_column: str) -> pd.Series:
    if column not in child.columns or parent_column not in parent.columns:
        return pd.Series([], dtype=object)
    values = child[column].dropna().astype(str)
    values = values[values != ""]
    return values[~values.isin(parent[parent_column].astype(str))]


def validate_relationships(frames: Dict[str, pd.DataFrame]) -> List[ValidationIssue]:
    """Check that IDs referenced in one dataset exist in another."""
    checks = [
        ("orders", "sku", "products", "sku"),
        ("shipments", "order_id", "orders", "order_id"),
        ("tickets", "order_id", "orders", "order_id"),
    ]
    issues = []
    for child, column, parent, parent_column in checks:
        if child not in frames or parent not in frames:
            continue
        missing = _missing_refs(frames[child], column, frames[parent], parent_column)
        if len(missing):
            examples = ", ".join(sorted(missing.unique())[:3])
            issues.append(ValidationIssue(
                child, WARNING, column,
                f"{len(missing)} row(s) reference a {column} not found in {parent} (e.g. {examples}).",
                len(missing)))
    return issues


def issues_to_frame(issues: List[ValidationIssue]) -> pd.DataFrame:
    columns = ["dataset", "severity", "column", "message", "rows_affected"]
    return pd.DataFrame([issue.__dict__ for issue in issues], columns=columns)
