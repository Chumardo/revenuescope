"""Dataset storage and relational constraints for the Phase 1 snapshot."""

from datetime import date, datetime
from pathlib import Path
from typing import NamedTuple

import duckdb


SCHEMA = """
CREATE TABLE dataset_meta (
    coverage_start DATE NOT NULL,
    coverage_end DATE NOT NULL,
    data_as_of TIMESTAMP NOT NULL,
    seed BIGINT NOT NULL,
    CHECK (coverage_start < coverage_end),
    CHECK (data_as_of >= CAST(coverage_start AS TIMESTAMP))
);
CREATE TABLE customers (
    customer_id BIGINT PRIMARY KEY,
    acquired_at TIMESTAMP NOT NULL
);
CREATE TABLE products (
    product_id BIGINT PRIMARY KEY,
    product_name VARCHAR NOT NULL CHECK (length(trim(product_name)) > 0),
    category VARCHAR NOT NULL CHECK (length(trim(category)) > 0)
);
CREATE TABLE orders (
    order_id BIGINT PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customers (customer_id),
    ordered_at TIMESTAMP NOT NULL,
    region VARCHAR NOT NULL CHECK (length(trim(region)) > 0),
    channel VARCHAR NOT NULL CHECK (length(trim(channel)) > 0),
    status VARCHAR NOT NULL CHECK (status IN (
        'completed', 'partially_refunded', 'refunded', 'canceled', 'pending'
    ))
);
CREATE TABLE order_lines (
    line_id BIGINT PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES orders (order_id),
    product_id BIGINT NOT NULL REFERENCES products (product_id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents BIGINT NOT NULL CHECK (unit_price_cents >= 0),
    discount_cents BIGINT NOT NULL CHECK (discount_cents >= 0),
    unit_cost_cents BIGINT NOT NULL CHECK (unit_cost_cents >= 0),
    CHECK (discount_cents <= quantity::HUGEINT * unit_price_cents)
);
CREATE TABLE refunds (
    refund_id BIGINT PRIMARY KEY,
    line_id BIGINT NOT NULL REFERENCES order_lines (line_id),
    refunded_at TIMESTAMP NOT NULL,
    refund_cents BIGINT NOT NULL CHECK (refund_cents > 0),
    returned_quantity INTEGER NOT NULL CHECK (returned_quantity >= 0)
);
"""


class DatasetMetadata(NamedTuple):
    coverage_start: date
    coverage_end: date
    data_as_of: datetime
    seed: int


def initialize_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create a fresh schema. An existing dataset is never silently replaced."""
    conn.execute(SCHEMA)


def metadata(conn: duckdb.DuckDBPyConnection) -> DatasetMetadata:
    rows = conn.execute(
        "SELECT coverage_start, coverage_end, data_as_of, seed FROM dataset_meta"
    ).fetchall()
    if len(rows) != 1:
        raise ValueError("Expected exactly one dataset metadata row.")
    return DatasetMetadata(*rows[0])


def open_dataset(path: str | Path) -> duckdb.DuckDBPyConnection:
    """Open an existing database read-only without implicitly creating it."""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Dataset not found: {path}. Run 'revenuescope generate' first.")
    return duckdb.connect(str(path), read_only=True)
