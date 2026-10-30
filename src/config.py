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
