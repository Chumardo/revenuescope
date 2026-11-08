"""Independent Python ledger audit and deterministic logical snapshot digest."""

from collections import Counter, defaultdict
from datetime import date, timedelta
from hashlib import sha256
import json

import duckdb

from .db import metadata


TABLE_KEYS = {
    "dataset_meta": "coverage_start",
    "customers": "customer_id",
    "products": "product_id",
    "orders": "order_id",
    "order_lines": "line_id",
    "refunds": "refund_id",
}


def snapshot_inventory(conn: duckdb.DuckDBPyConnection) -> dict:
    """Hash sorted logical rows. DuckDB file bytes are not the reproducibility contract."""
    digest = sha256()
    counts = {}
    for table, key in TABLE_KEYS.items():
        digest.update(f"{table}\n".encode())
        cursor = conn.execute(f"SELECT * FROM {table} ORDER BY {key}")
        count = 0
        while batch := cursor.fetchmany(4096):
            for row in batch:
                digest.update(json.dumps(row, default=str, separators=(",", ":")).encode())
                digest.update(b"\n")
            count += len(batch)
        counts[table] = count
    return {"row_counts": counts, "logical_sha256": digest.hexdigest()}


def independent_ledger(conn: duckdb.DuckDBPyConnection) -> dict:
    """Read base tables separately and audit financial and relational invariants."""
    meta = metadata(conn)
    customers = dict(conn.execute("SELECT customer_id, acquired_at FROM customers").fetchall())
    orders = {
        order_id: (customer_id, ordered_at, region, channel, status)
        for order_id, customer_id, ordered_at, region, channel, status
        in conn.execute("SELECT * FROM orders").fetchall()
    }
    products = {product_id: (name, category) for product_id, name, category
                in conn.execute("SELECT product_id, product_name, category FROM products").fetchall()}
    lines = {
        line_id: (order_id, product_id, quantity, unit_price, discount, unit_cost)
        for line_id, order_id, product_id, quantity, unit_price, discount, unit_cost
        in conn.execute("SELECT * FROM order_lines").fetchall()
    }
    recognized = {"completed", "partially_refunded", "refunded"}
    for order_id, (customer, at, _, _, _) in orders.items():
        if not meta.coverage_start <= at.date() < meta.coverage_end:
            raise ValueError(f"Order {order_id} is outside coverage.")
        if customers[customer] > at:
            raise ValueError(f"Order {order_id} predates acquisition.")
    line_count = Counter(row[0] for row in lines.values())
    for order_id, (_, _, _, _, status) in orders.items():
        if status in recognized and not line_count[order_id]:
            raise ValueError(f"Recognized order {order_id} has no lines.")

    refund_totals = defaultdict(Counter)
    known_refunds = defaultdict(Counter)
    for refund_id, line_id, at, cents, returned in conn.execute("SELECT * FROM refunds").fetchall():
        order_id, *_ = lines[line_id]
        if at < orders[order_id][1] or orders[order_id][4] not in recognized:
            raise ValueError(f"Refund {refund_id} has invalid order status or chronology.")
        refund_totals[line_id].update(cents=cents, units=returned)
        if at < meta.data_as_of:
            known_refunds[line_id].update(cents=cents, units=returned)

    daily = Counter()
    total = Counter()
    segments = {d: Counter() for d in ("product", "category", "region", "channel")}
    active_customers = set()
    included_orders = set()
    for line_id, (order_id, product_id, qty, price, discount, cost) in lines.items():
        customer, at, region, channel, status = orders[order_id]
        if refund_totals[line_id]["cents"] > qty * price - discount or refund_totals[line_id]["units"] > qty:
            raise ValueError(f"Refunds exceed the sold value or quantity on line {line_id}.")
        if status not in recognized or at >= meta.data_as_of:
            continue
        refund = known_refunds[line_id]["cents"]
        net = qty * price - discount - refund
        cogs = (qty - known_refunds[line_id]["units"]) * cost
        total.update(gross_sales_cents=qty * price, discounts_cents=discount, refunds_cents=refund,
                     net_revenue_cents=net, cogs_cents=cogs, gross_profit_cents=net - cogs)
        daily[at.date()] += net
        name, category = products[product_id]
        segments["product"][f"{name} [#{product_id}]"] += net
        segments["category"][category] += net
        segments["region"][region] += net
        segments["channel"][channel] += net
        included_orders.add(order_id)
        active_customers.add(customer)
    total.update(orders=len(included_orders), active_customers=len(active_customers))
    daily_orders = Counter(orders[order_id][1].date() for order_id in included_orders)
    return {"total": dict(total), "daily": dict(daily), "daily_orders": dict(daily_orders), "segments": segments,
            "order_status_counts": dict(Counter(row[4] for row in orders.values()))}


def verify_analytics(conn: duckdb.DuckDBPyConnection, ledger: dict) -> dict:
    from .analytics import daily_revenue, monthly_kpis, summarize

    meta = metadata(conn)
    start, end = meta.coverage_start, meta.coverage_end
    summary = summarize(conn, start, end)
    for measure, expected in ledger["total"].items():
        if int(summary[measure].iat[0]) != expected:
            raise ArithmeticError(f"SQL summary does not match the independent ledger: {measure}.")
    daily = daily_revenue(conn, start, end)
    calendar = [start + timedelta(days=i) for i in range((end - start).days)]
    dates = [date.fromisoformat(str(value)[:10]) for value in daily["date"]]
    if dates != calendar:
        raise ArithmeticError("SQL daily series does not cover the exact requested calendar.")
    expected_daily = {
        "net_revenue_cents": [ledger["daily"].get(day, 0) for day in calendar],
        "orders": [ledger["daily_orders"].get(day, 0) for day in calendar],
    }
    monthly = monthly_kpis(conn, start, end)
    for measure, expected in expected_daily.items():
        if list(map(int, daily[measure])) != expected:
            raise ArithmeticError(f"SQL daily {measure} does not match the independent ledger.")
        expected_monthly = Counter()
        for day, value in zip(calendar, expected, strict=True):
            expected_monthly[day.replace(day=1)] += value
        actual_months = [date.fromisoformat(str(value)[:10]) for value in monthly["month"]]
        if actual_months != list(expected_monthly) or list(map(int, monthly[measure])) != list(expected_monthly.values()):
            raise ArithmeticError(f"SQL monthly {measure} does not match the independent ledger.")
    for dimension, segments in ledger["segments"].items():
        values = summarize(conn, start, end, dimension=dimension)
        actual = dict(zip(values["segment"], map(int, values["net_revenue_cents"]), strict=True))
        if actual != dict(segments):
            raise ArithmeticError(f"SQL {dimension} contributions do not match the independent ledger.")
    return {"independent_totals": ledger["total"], "checked_days": len(calendar), "checked_months": len(monthly),
            "checked_dimensions": list(ledger["segments"])}
