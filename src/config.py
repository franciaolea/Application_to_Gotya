"""Central configuration: file locations, data mode and business rules.

Every number that encodes a business decision (thresholds, SLAs, escalation
windows) lives here, so it can be changed in one place and explained easily.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = PROJECT_ROOT / "data"

# --- Data mode -------------------------------------------------------------
# "demo" reads the fictional sample data in data/demo/.
# "live" reads real exports placed in data/live/ (never committed to git).
# Switch with an environment variable:  OPS_DATA_MODE=live streamlit run app.py
VALID_MODES = ("demo", "live")
DATA_MODE = os.getenv("OPS_DATA_MODE", "demo").strip().lower()

DATASET_FILES = {
    "products": "products.csv",
    "orders": "orders.csv",
    "shipments": "shipments.csv",
    "payments": "payments.csv",
    "tickets": "tickets.csv",
    "actions": "actions.csv",
}
METADATA_FILE = "_metadata.json"


def data_dir(mode: Optional[str] = None) -> Path:
    """Return the folder holding the CSV files for the given data mode."""
    mode = (mode or DATA_MODE).lower()
    if mode not in VALID_MODES:
        raise ValueError(f"Unknown data mode '{mode}'. Use one of {VALID_MODES}.")
    return DATA_ROOT / mode


# --- Business rules --------------------------------------------------------
# Orders: should leave the warehouse within this many hours of being placed.
# Past it an order is "stuck"; past the escalation limit it is critical.
ORDER_FULFILMENT_HOURS = 48
ORDER_ESCALATION_HOURS = 96

# Shipping: a shipment past its expected delivery date is "delayed";
# delayed by this many days or more it gets escalated.
SHIPMENT_ESCALATION_DAYS = 3
ON_TIME_DELIVERY_TARGET = 0.95
# A carrier at or above this rate is reported as performing well, even if below target.
ON_TIME_ACCEPTABLE = 0.90

# Inventory: minimum stock (reorder point) = daily demand x (lead time + safety days).
SAFETY_STOCK_DAYS = 7
# Stock at or below this share of the minimum is "critical".
CRITICAL_STOCK_RATIO = 0.5
# Sales over this many days set the demand rate used for "days of cover".
DEMAND_WINDOW_DAYS = 28

# Support: hours allowed to resolve a ticket, by priority.
TICKET_SLA_HOURS = {"Critical": 4, "High": 24, "Medium": 48, "Low": 72}
# An open ticket this close to its deadline is "at risk".
TICKET_AT_RISK_HOURS = 4
SLA_COMPLIANCE_TARGET = 0.90

# Payments: unpaid items this many days past due are escalated.
PAYMENT_ESCALATION_DAYS = 7

# Time of day the morning briefing is produced (used for SLA calculations).
BRIEFING_HOUR = 8

# Daily briefing: who each area's critical issues are escalated to.
ESCALATION_PATHS = {
    "Orders": "Warehouse manager",
    "Shipping": "Carrier account manager",
    "Inventory": "Purchasing lead",
    "Payments": "Finance lead",
    "Support": "Head of customer service",
}

# --- AI Operations Assistant (rule-based analysis engine) -----------------------
ASSISTANT_TOP_N = 3
# A carrier at least this far below the other carriers' on-time rate is an outlier.
ASSISTANT_CARRIER_GAP = 0.10
# Ignore carriers with fewer delivered parcels than this (too small to judge).
ASSISTANT_MIN_DELIVERIES = 20
# "Concentrated": a segment's share of problems / its share of volume is at least this.
ASSISTANT_CONCENTRATION_LIFT = 1.5
# "Demand spike": last 7 days' daily sales at least this multiple of the previous 3 weeks'.
ASSISTANT_DEMAND_SPIKE = 1.3
# Impact score = severity weight x scale level (1-3) x urgency (0.5-1.0).
ASSISTANT_SEVERITY_WEIGHT = {"Critical": 3, "High": 2, "Medium": 1}
# Scale level 2 from the first number, level 3 from the second.
ASSISTANT_SCALE_LEVELS = {
    "customers": (3, 10),        # customers or orders affected
    "eur": (1_000, 10_000),      # money blocked or overdue
    "eur_per_day": (200, 1_000),  # sales lost or at risk per day
}
# Workload: one owner holding this share (and at least 4) of open Critical/High issues.
ASSISTANT_OVERLOAD_SHARE = 0.4
