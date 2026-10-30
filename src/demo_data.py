"""Generate the FICTIONAL demo dataset for the Operations Control Tower.

All companies, people, products and transactions below are invented.

Usage:
    python -m src.demo_data                 # default snapshot date (reproducible)
    python -m src.demo_data --as-of today   # dates relative to today

Design notes:
- A fixed random seed makes the output identical on every run, so tests and
  demos always show the same numbers.
- Every date is calculated relative to an "as-of" date (the moment the snapshot
  represents). That way the data always contains a realistic mix of on-time,
  delayed and overdue items, whenever it was generated.
- Problems (low stock, late shipments, SLA breaches...) are planted on purpose
  in realistic proportions so the dashboard has a story to tell.
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from src import config

SEED = 10  # chosen because it produces a balanced, realistic scenario for demos
DEFAULT_AS_OF = date(2026, 10, 30)
COMPANY = "Lumora Home & Living"  # fictional company

DATE_FMT = "%Y-%m-%d"
DATETIME_FMT = "%Y-%m-%d %H:%M"

TEAM = {
    "inventory": ["Priya Raman", "Tomás Herrera"],
    "fulfilment": ["Jonas Vermeulen", "Mei Tanaka"],
    "logistics": ["Amara Okafor", "Lukas Brandt", "Sofia Marchetti"],
    "finance": ["Hannah Lindqvist", "Daniel Mensah"],
    "support": ["Chloé Martin", "Ravi Patel", "Noor Haddad", "Ben Carter"],
}

# (name, category, supplier, unit_cost, unit_price)
PRODUCTS = [
    ("Linen Duvet Cover - Double", "Bedding", "Harbor Textiles", 38.0, 89.0),
    ("Linen Duvet Cover - King", "Bedding", "Harbor Textiles", 44.0, 109.0),
    ("Waffle Bath Towel Set", "Bath", "Harbor Textiles", 18.0, 45.0),
    ("Organic Cotton Pillowcases (2)", "Bedding", "Harbor Textiles", 9.5, 24.0),
    ("Stoneware Dinner Plate Set (4)", "Kitchen", "Atlas Ceramics", 21.0, 54.0),
    ("Speckled Mug Set (4)", "Kitchen", "Atlas Ceramics", 12.0, 32.0),
    ("Glazed Serving Bowl", "Kitchen", "Atlas Ceramics", 11.0, 29.0),
    ("Ceramic Table Lamp", "Lighting", "Brightline Lighting", 34.0, 79.0),
    ("Rattan Pendant Shade", "Lighting", "Brightline Lighting", 26.0, 65.0),
    ("Brass Floor Lamp", "Lighting", "Brightline Lighting", 72.0, 169.0),
    ("LED Candle Set (3)", "Lighting", "Brightline Lighting", 8.0, 22.0),
    ("Oak Wall Shelf", "Storage", "Cedar & Stone Supply", 19.0, 49.0),
    ("Woven Storage Basket - Large", "Storage", "Cedar & Stone Supply", 14.0, 36.0),
    ("Bamboo Drawer Organiser", "Storage", "Cedar & Stone Supply", 7.0, 19.0),
    ("Marble Bathroom Tray", "Bath", "Cedar & Stone Supply", 13.0, 34.0),
    ("Teak Bath Mat", "Bath", "Cedar & Stone Supply", 16.0, 42.0),
    ("Chunky Knit Throw", "Decor", "Nordic Loom Co.", 29.0, 75.0),
    ("Wool Cushion Cover", "Decor", "Nordic Loom Co.", 10.0, 28.0),
    ("Jute Area Rug 160x230", "Decor", "Nordic Loom Co.", 58.0, 149.0),
    ("Round Wall Mirror", "Decor", "Cedar & Stone Supply", 41.0, 99.0),
    ("Scented Soy Candle", "Decor", "Nordic Loom Co.", 6.5, 18.0),
    ("Glass Storage Jars (3)", "Kitchen", "Atlas Ceramics", 9.0, 26.0),
    ("Cast Iron Casserole Dish", "Kitchen", "Atlas Ceramics", 39.0, 95.0),
    ("Bedside Table Lamp", "Lighting", "Brightline Lighting", 22.0, 55.0),
]

FIRST_NAMES = ["Emma", "Lucas", "Mila", "Noah", "Freya", "Jonas", "Elena", "Mateo",
               "Ines", "Oscar", "Lena", "Hugo", "Maya", "Felix", "Zara", "Theo",
               "Nadia", "Leon", "Aisha", "Finn", "Clara", "Omar", "Julia", "Sami"]
LAST_NAMES = ["Vermeer", "Schulz", "Dubois", "Kowalski", "Rossi", "Jansen", "Moreau",
              "Fischer", "Novak", "Lambert", "Silva", "Berg", "Costa", "Weber",
              "Laurent", "Andersen", "Romero", "Bakker", "Keller", "Petit"]

REGIONS = {"Netherlands": 2, "Germany": 3, "France": 3, "Belgium": 2, "Spain": 4}
CHANNELS = ["Website", "Mobile App", "Marketplace"]
CARRIERS = ["Veloxa Courier", "Northlane Freight", "Parcelyn"]
# Planted bottleneck: Parcelyn delivers late more often, loses more parcels
# ("stuck") and raises more exceptions than the other two carriers.
CARRIER_PROFILES = {
    "Veloxa Courier": {"share": 0.4, "stuck": 0.005, "exception": 0.03,
                       "delay_days": {"-1": 0.15, "0": 0.75, "1": 0.07, "2": 0.03}},
    "Northlane Freight": {"share": 0.35, "stuck": 0.005, "exception": 0.03,
                          "delay_days": {"-1": 0.1, "0": 0.78, "1": 0.09, "2": 0.03}},
    "Parcelyn": {"share": 0.25, "stuck": 0.05, "exception": 0.25,
                 "delay_days": {"-1": 0.05, "0": 0.5, "1": 0.25, "2": 0.2}},
}
WAREHOUSES = ["Rotterdam DC", "Leipzig DC"]

# Order volume grows from ~9 to ~21 orders a day over the 8 weeks of history.
ORDERS_PER_DAY_START, ORDERS_PER_DAY_END = 9, 21
HISTORY_DAYS = 57  # 8 full weeks + today, so 28-day periods can be compared
QUANTITY_WEIGHTS = {"1": 0.6, "2": 0.25, "3": 0.1, "4": 0.05}


def _pick(rng: random.Random, weighted: Dict[str, float]) -> str:
    """Pick a key from {value: weight}."""
    return rng.choices(list(weighted), weights=list(weighted.values()), k=1)[0]


def _d(value: Optional[date]) -> str:
    return value.strftime(DATE_FMT) if value else ""


def _dt(value: Optional[datetime]) -> str:
    return value.strftime(DATETIME_FMT) if value else ""


# --- Individual datasets ---------------------------------------------------

def make_products(rng: random.Random, as_of: date):
    """Products with stock levels set the way an inventory planner would.

    Each product gets a popularity (share of sales). Its minimum stock - the
    reorder point - is then: expected daily demand x (supplier lead time +
    safety stock days). Returns (products, popularity weights for ordering).
    """
    # Planted stock situations: 2 out of stock, 3 critical, 3 low, the rest healthy.
    states = ["out"] * 2 + ["critical"] * 3 + ["low"] * 3
    states += ["healthy"] * (len(PRODUCTS) - len(states))
    rng.shuffle(states)

    weights = [rng.uniform(0.4, 2.0) for _ in PRODUCTS]
    avg_qty = sum(int(q) * w for q, w in QUANTITY_WEIGHTS.items())
    # Demand forecast = average over the last 4 weeks of the growth curve.
    recent_orders_per_day = ORDERS_PER_DAY_START + (ORDERS_PER_DAY_END - ORDERS_PER_DAY_START) * 0.75

    rows = []
    for i, ((name, category, supplier, cost, price), state, weight) in enumerate(
            zip(PRODUCTS, states, weights), 1):
        lead_time = rng.choice([3, 5, 7, 10, 14, 21])
        daily_demand = recent_orders_per_day * avg_qty * weight / sum(weights)
        min_stock = max(5, round(daily_demand * (lead_time + config.SAFETY_STOCK_DAYS)))
        if state == "out":
            stock = 0
        elif state == "critical":
            stock = rng.randint(1, int(min_stock * config.CRITICAL_STOCK_RATIO))
        elif state == "low":
            stock = rng.randint(int(min_stock * config.CRITICAL_STOCK_RATIO) + 1, min_stock - 1)
        else:
            stock = rng.randint(min_stock + 5, min_stock * 4)
        rows.append({
            "sku": f"LUM-{i:03d}",
            "product_name": name,
            "category": category,
            "supplier": supplier,
            "warehouse": rng.choice(WAREHOUSES),
            "stock_on_hand": stock,
            "min_stock": min_stock,
            "reorder_qty": min_stock * 2,
            "lead_time_days": lead_time,
            "unit_cost": cost,
            "unit_price": price,
            "last_restock_date": _d(as_of - timedelta(days=rng.randint(5, 60))),
            "owner": rng.choice(TEAM["inventory"]),
        })
    return pd.DataFrame(rows), weights


def _order_status(rng: random.Random, age_days: int) -> str:
    """Older orders are further along the fulfilment pipeline.

    "Dispatched" is temporary: make_shipments() turns it into Shipped or
    Delivered depending on what happened to the parcel. A few 2-3 day old
    orders are still unshipped on purpose: the warehouse bottleneck to surface.
    """
    if age_days <= 1:
        return _pick(rng, {"Pending": 0.5, "Processing": 0.45, "Cancelled": 0.05})
    if age_days <= 3:
        return _pick(rng, {"Pending": 0.02, "Processing": 0.05, "Dispatched": 0.9, "Cancelled": 0.03})
    return _pick(rng, {"Dispatched": 0.96, "Cancelled": 0.04})


def _daily_order_counts(rng: random.Random, days: int) -> List[int]:
    """Orders per day, oldest first: a steady growth trend with day-to-day noise."""
    growth = ORDERS_PER_DAY_END - ORDERS_PER_DAY_START
    return [max(1, round(ORDERS_PER_DAY_START + growth * i / (days - 1) + rng.uniform(-3, 3)))
            for i in range(days)]


def make_orders(rng: random.Random, as_of: date, products: pd.DataFrame, weights: List[float],
                days: int = HISTORY_DAYS) -> pd.DataFrame:
    rows = []
    order_times = []
    for i, count in enumerate(_daily_order_counts(rng, days)):
        age_days = days - 1 - i
        for _ in range(count):
            # Today's orders can only exist before the morning briefing.
            hour = rng.randint(0, config.BRIEFING_HOUR - 1) if age_days == 0 else rng.randint(7, 22)
            order_times.append((age_days, datetime.combine(as_of - timedelta(days=age_days),
                                                           time(hour, rng.randint(0, 59)))))
    for age_days, order_dt in order_times:
        product = products.iloc[rng.choices(range(len(products)), weights=weights, k=1)[0]]
        qty = _pick(rng, QUANTITY_WEIGHTS)
        rows.append({
            "order_date": order_dt,
            "customer_name": f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
            "channel": _pick(rng, {"Website": 0.55, "Mobile App": 0.3, "Marketplace": 0.15}),
            "region": rng.choice(list(REGIONS)),
            "sku": product["sku"],
            "quantity": int(qty),
            "order_value": round(int(qty) * float(product["unit_price"]), 2),
            "status": _order_status(rng, age_days),
            "owner": rng.choice(TEAM["fulfilment"]),
        })
    df = pd.DataFrame(rows).sort_values("order_date").reset_index(drop=True)
    df.insert(0, "order_id", [f"ORD-{10001 + i}" for i in range(len(df))])
    df["order_date"] = df["order_date"].map(_dt)
    return df


def make_shipments(rng: random.Random, as_of: date, orders: pd.DataFrame):
    """Simulate each dispatched parcel's journey.

    Returns (orders, shipments): an order becomes "Delivered" if its parcel
    arrived before the snapshot day, otherwise "Shipped".
    """
    orders = orders.copy()
    rows = []
    shares = {name: profile["share"] for name, profile in CARRIER_PROFILES.items()}
    for idx, order in orders[orders["status"] == "Dispatched"].iterrows():
        carrier = _pick(rng, shares)
        profile = CARRIER_PROFILES[carrier]
        order_day = datetime.strptime(order["order_date"], DATETIME_FMT).date()
        age = (as_of - order_day).days
        ship_date = order_day + timedelta(days=min(rng.choice([1, 1, 2]), age))
        expected = ship_date + timedelta(days=REGIONS[order["region"]] + rng.choice([0, 1]))

        # Parcels lost in the network stay open; only recent ones are still being chased.
        stuck = age <= 10 and rng.random() < profile["stuck"]
        actual = None if stuck else max(expected + timedelta(days=int(_pick(rng, profile["delay_days"]))),
                                         ship_date + timedelta(days=1))
        if actual is not None and actual < as_of:
            status = "Delivered"
        else:
            actual = None
            if stuck or rng.random() < profile["exception"]:
                status = "Exception" if rng.random() < profile["exception"] * 2 else "In Transit"
            else:
                status = _pick(rng, {"In Transit": 0.8, "Out for Delivery": 0.2})
        orders.at[idx, "status"] = "Delivered" if status == "Delivered" else "Shipped"

        rows.append({
            "shipment_id": f"SHP-{len(rows) + 5001}",
            "order_id": order["order_id"],
            "carrier": carrier,
            "tracking_number": f"TRK{rng.randint(10**9, 10**10 - 1)}",
            "destination": order["region"],
            "status": status,
            "ship_date": _d(ship_date),
            "expected_delivery_date": _d(expected),
            "actual_delivery_date": _d(actual),
            "owner": rng.choice(TEAM["logistics"]),
        })
    return orders, pd.DataFrame(rows)


def make_payments(rng: random.Random, as_of: date, orders: pd.DataFrame, n: int = 40) -> pd.DataFrame:
    # type -> (direction, possible counterparties, amount range)
    kinds = {
        "Marketplace Payout": ("Incoming", ["MarketHub Marketplace"], (900, 6500)),
        "Payment Processor Settlement": ("Incoming", ["PayFlow Payments", "CardBridge"], (1500, 9000)),
        "Supplier Invoice": ("Outgoing", ["Harbor Textiles", "Atlas Ceramics", "Brightline Lighting",
                                          "Cedar & Stone Supply", "Nordic Loom Co."], (800, 12000)),
        "Carrier Invoice": ("Outgoing", CARRIERS, (400, 3500)),
        "Customer Refund": ("Outgoing", [], (18, 220)),
    }
    weights = {"Marketplace Payout": 0.2, "Payment Processor Settlement": 0.2,
               "Supplier Invoice": 0.25, "Carrier Invoice": 0.15, "Customer Refund": 0.2}
    rows = []
    for i in range(n):
        kind = _pick(rng, weights)
        direction, parties, (low, high) = kinds[kind]
        reference = ""
        if kind == "Customer Refund":
            order = orders.iloc[rng.randrange(len(orders))]
            counterparty, reference = order["customer_name"], order["order_id"]
        else:
            counterparty = rng.choice(parties)

        due = as_of + timedelta(days=rng.randint(-20, 14))
        days_past_due = (as_of - due).days
        # Finance is in decent shape: old items are settled, a few recent ones slipped.
        if days_past_due >= config.PAYMENT_ESCALATION_DAYS:
            status = "Paid"
        elif days_past_due > 0:
            status = _pick(rng, {"Paid": 0.6, "Pending": 0.4})
        else:
            status = _pick(rng, {"Pending": 0.55, "Processing": 0.35, "Paid": 0.1})
        paid = None
        if status == "Paid":
            paid = min(due + timedelta(days=rng.randint(-3, 0)), as_of - timedelta(days=1))

        rows.append({
            "payment_id": f"PAY-{7001 + i}",
            "type": kind,
            "direction": direction,
            "counterparty": counterparty,
            "reference": reference,
            "amount_eur": round(rng.uniform(low, high), 2),
            "status": status,
            "due_date": _d(due),
            "paid_date": _d(paid),
            "owner": rng.choice(TEAM["finance"]),
        })
    df = pd.DataFrame(rows)
    # Planted scenario: the largest unpaid supplier invoice is on hold (e.g. a disputed delivery),
    # a decision the finance owner has to unblock.
    unpaid_supplier = df[(df["type"] == "Supplier Invoice") & (df["status"] != "Paid")]
    if len(unpaid_supplier):
        df.loc[unpaid_supplier["amount_eur"].idxmax(), "status"] = "On Hold"
    return df


TICKET_SUBJECTS = {
    "Where is my order": ["Parcel not arrived yet", "Tracking hasn't updated in days",
                          "Delivery date keeps changing"],
    "Damaged item": ["Plates arrived cracked", "Lamp shade dented on arrival",
                     "Mirror frame scratched"],
    "Wrong item": ["Received king instead of double", "Wrong colour delivered"],
    "Refund request": ["Refund not received after return", "Want to cancel and refund"],
    "Payment issue": ["Charged twice at checkout", "Discount code not applied"],
    "Product question": ["Is the throw machine washable?", "Bulb type for the floor lamp?"],
}


def _ticket_order_pools(as_of: date, orders: pd.DataFrame, shipments: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """Which orders a customer could plausibly contact us about, per kind of problem."""
    today = _d(as_of)
    late = shipments[(shipments["actual_delivery_date"] == "")
                     & ((shipments["expected_delivery_date"] < today) | (shipments["status"] == "Exception"))]
    return {
        "late": orders[orders["order_id"].isin(late["order_id"])],
        "shipped": orders[orders["order_id"].isin(shipments["order_id"])],
        "delivered": orders[orders["order_id"].isin(
            shipments.loc[shipments["status"] == "Delivered", "order_id"])],
        "any": orders,
    }


def make_tickets(rng: random.Random, as_of: date, orders: pd.DataFrame, shipments: pd.DataFrame,
                 n: int = 60, days: int = 10) -> pd.DataFrame:
    """Support tickets over the last `days` days (~6 a day, about 4% of orders).

    Tickets follow real problems: most "Where is my order" contacts come from
    parcels that are actually late, and damage/wrong-item complaints come from
    delivered orders. That is what lets the Support page link contact volume
    back to carrier performance.
    """
    as_of_ts = datetime.combine(as_of, time(config.BRIEFING_HOUR))
    pools = _ticket_order_pools(as_of, orders, shipments)
    # Planted priority mix (10% Critical, 25% High, 40% Medium, rest Low), shuffled.
    priorities = (["Critical"] * round(n * 0.1) + ["High"] * round(n * 0.25)
                  + ["Medium"] * round(n * 0.4))
    priorities += ["Low"] * (n - len(priorities))
    rng.shuffle(priorities)

    rows = []
    for priority in priorities:
        created = as_of_ts - timedelta(hours=rng.randint(1, 24 * days))
        sla = created + timedelta(hours=config.TICKET_SLA_HOURS[priority])
        category = _pick(rng, {"Where is my order": 0.3, "Damaged item": 0.15, "Wrong item": 0.1,
                               "Refund request": 0.2, "Payment issue": 0.1, "Product question": 0.15})
        sla_hours = config.TICKET_SLA_HOURS[priority]
        age_hours = (as_of_ts - created).total_seconds() / 3600
        # Tickets older than their SLA are usually done; a few slipped through (breaches).
        if age_hours > sla_hours:
            status = _pick(rng, {"Resolved": 0.6, "Closed": 0.32, "Open": 0.03,
                                 "In Progress": 0.02, "Waiting on Customer": 0.03})
        else:
            status = _pick(rng, {"Open": 0.4, "In Progress": 0.35, "Waiting on Customer": 0.1,
                                 "Resolved": 0.15})
        resolved = None
        if status in ("Resolved", "Closed"):
            # Most are solved within the SLA, some take up to 25% longer.
            took = timedelta(hours=sla_hours * rng.uniform(0.2, 1.25))
            resolved = min(created + took, as_of_ts - timedelta(minutes=5))
        if category == "Where is my order":
            pool = pools["late"] if len(pools["late"]) and rng.random() < 0.7 else pools["shipped"]
        elif category in ("Damaged item", "Wrong item"):
            pool = pools["delivered"]
        else:
            pool = pools["any"]
        order = pool.iloc[rng.randrange(len(pool))]
        rows.append({
            "ticket_id": "",  # numbered after sorting below
            "created_at": created,
            "customer_name": order["customer_name"],
            "order_id": "" if category == "Product question" else order["order_id"],
            "channel": _pick(rng, {"Email": 0.5, "Chat": 0.35, "Phone": 0.15}),
            "category": category,
            "subject": rng.choice(TICKET_SUBJECTS[category]),
            "priority": priority,
            "status": status,
            "sla_deadline": sla,
            "resolved_at": resolved,
            "owner": rng.choice(TEAM["support"]),
        })
    rows.sort(key=lambda r: r["created_at"])  # ticket numbers follow creation order
    for i, row in enumerate(rows):
        row["ticket_id"] = f"TCK-{3001 + i}"
        for col in ("created_at", "sla_deadline", "resolved_at"):
            row[col] = _dt(row[col])
    return pd.DataFrame(rows)


def make_actions(as_of: date, products: pd.DataFrame, shipments: pd.DataFrame,
                 payments: pd.DataFrame, tickets: pd.DataFrame) -> pd.DataFrame:
    """A few existing actions so the Action Tracker isn't empty on first launch."""
    out_of_stock = products[products["stock_on_hand"] == 0].iloc[0]
    exception = shipments[shipments["status"] == "Exception"]
    overdue = payments[(payments["status"] != "Paid") & (payments["due_date"] < _d(as_of))]
    critical = tickets[tickets["priority"] == "Critical"]

    rows = [
        ("Inventory", out_of_stock["sku"], f"Expedite PO for out-of-stock '{out_of_stock['product_name']}'",
         out_of_stock["owner"], "High", "In Progress", -2, 1, "", "Supplier confirmed dispatch, awaiting ETA."),
        ("Shipping", exception.iloc[0]["shipment_id"] if len(exception) else "",
         "Carrier exception - confirm address with customer", "Amara Okafor", "High", "Open", -1, 0, "", ""),
        ("Payments", overdue.iloc[0]["payment_id"] if len(overdue) else "",
         "Chase overdue payment with counterparty", "Hannah Lindqvist", "Medium", "Blocked", -4, -1, "",
         "Waiting on bank details from counterparty."),
        ("Support", critical.iloc[0]["ticket_id"] if len(critical) else "",
         "Call back customer on critical ticket", "Chloé Martin", "Critical", "Resolved", -3, -2, "-2",
         "Customer refunded, ticket closed."),
        ("General", "", "Review weekend staffing for peak season", "Lukas Brandt", "Low", "Open", -5, 6, "", ""),
    ]
    records = []
    for i, (module, ref, issue, owner, prio, status, created, due, resolved, comment) in enumerate(rows, 1):
        records.append({
            "action_id": f"ACT-{i:03d}",
            "created_date": _d(as_of + timedelta(days=created)),
            "module": module,
            "reference_id": ref,
            "issue": issue,
            "owner": owner,
            "priority": prio,
            "status": status,
            "due_date": _d(as_of + timedelta(days=due)),
            "resolution_date": _d(as_of + timedelta(days=int(resolved))) if resolved else "",
            "comments": comment,
        })
    return pd.DataFrame(records)


# --- Orchestration ---------------------------------------------------------

def generate_all(as_of: date = DEFAULT_AS_OF, seed: int = SEED) -> Dict[str, pd.DataFrame]:
    """Build every dataset in memory. Same inputs always give the same output."""
    rng = random.Random(seed)
    products, popularity = make_products(rng, as_of)
    orders = make_orders(rng, as_of, products, popularity)
    orders, shipments = make_shipments(rng, as_of, orders)
    payments = make_payments(rng, as_of, orders)
    tickets = make_tickets(rng, as_of, orders, shipments)
    actions = make_actions(as_of, products, shipments, payments, tickets)
    return {"products": products, "orders": orders, "shipments": shipments,
            "payments": payments, "tickets": tickets, "actions": actions}


def write_all(frames: Dict[str, pd.DataFrame], as_of: date, directory: Path, seed: int = SEED) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, df in frames.items():
        df.to_csv(directory / config.DATASET_FILES[name], index=False)
    metadata = {
        "data_classification": "FICTIONAL DEMO DATA",
        "company": COMPANY,
        "as_of_date": as_of.strftime(DATE_FMT),
        "generated_at": datetime.now().strftime(DATETIME_FMT),
        "seed": seed,
        "notice": "All people, companies, products and transactions are invented for demonstration.",
    }
    (directory / config.METADATA_FILE).write_text(json.dumps(metadata, indent=2, ensure_ascii=False))


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Generate fictional demo data.")
    parser.add_argument("--as-of", default=DEFAULT_AS_OF.strftime(DATE_FMT),
                        help="Snapshot date YYYY-MM-DD, or 'today' (default: %(default)s)")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)

    as_of = date.today() if args.as_of == "today" else datetime.strptime(args.as_of, DATE_FMT).date()
    frames = generate_all(as_of, args.seed)
    target = config.data_dir("demo")
    write_all(frames, as_of, target, args.seed)
    print(f"Demo data written to {target} (as of {as_of}):")
    for name, df in frames.items():
        print(f"  {name:<10} {len(df):>4} rows")


if __name__ == "__main__":
    main()
