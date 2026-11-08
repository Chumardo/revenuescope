"""Generate a reproducible synthetic store and a separate event answer key."""

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from math import exp
from random import Random

import duckdb
import pandas as pd

from .db import initialize_schema

DEFAULT_SEED = 202501
DEFAULT_START = date(2023, 1, 1)
DEFAULT_MONTHS = 24

_REGIONS = ("Northeast", "Southeast", "Midwest", "West", "Southwest")
_CHANNELS = ("Direct Web", "Mobile App", "Marketplace")
_OWNED_CHANNELS = _CHANNELS[:2]
_MONTHLY_DEMAND = (0.83, 0.86, 0.95, 0.98, 1.03, 1.00, 0.95, 0.99, 1.04, 1.08, 1.22, 1.18)
_WEEKDAY_DEMAND = (1.04, 0.98, 1.00, 1.06, 1.16, 0.93, 0.88)

# (category, share, base COGS basis points, (product name, USD cents) pairs).
_CATALOG = (
    ("Home & Living", 28, 4650, (
        ("Woven Throw", 4900), ("Ceramic Planter", 2800), ("Linen Sheet Set", 11800),
        ("Cotton Bath Set", 6400), ("Desk Lamp", 7200), ("Bamboo Organizer", 3200),
    )),
    ("Electronics", 17, 6800, (
        ("USB-C Travel Hub", 4400), ("Bluetooth Earbuds", 6900), ("Wireless Charger", 3600),
        ("Portable Speaker", 8900), ("Webcam", 7400), ("Studio Audio Bundle", 24900),
    )),
    ("Apparel", 20, 3850, (
        ("Cotton Tee", 2600), ("Fleece Pullover", 5800), ("Everyday Jogger", 5200),
        ("Performance Socks", 1600), ("Insulated Jacket", 11900), ("Canvas Cap", 2400),
    )),
    ("Kitchen", 15, 5300, (
        ("Glass Food Containers", 3800), ("Coffee Press", 4500), ("Chef Knife", 5600),
        ("Cast-Iron Skillet", 6800), ("Stoneware Dinner Set", 9600), ("Measuring Set", 2200),
    )),
    ("Outdoor", 12, 5150, (
        ("Insulated Bottle", 3200), ("Packable Daypack", 4900), ("Compact Lantern", 4200),
        ("Camp Chair", 8400), ("Waterproof Dry Bag", 3300), ("Trail Blanket", 6200),
    )),
    ("Personal Care", 8, 4250, (
        ("Hand Care Set", 2500), ("Travel Wash Kit", 3400), ("Face Towel Set", 1900),
        ("Body Brush", 1800), ("Bath Gift Set", 4700), ("Refill Pack", 2100),
    )),
)
_PRODUCT_WEIGHTS = (29, 24, 18, 14, 10, 5)


@dataclass(frozen=True, slots=True)
class _Product:
    id: int
    name: str
    category: str
    price_cents: int
    cost_cents: int


@dataclass(frozen=True, slots=True)
class _Customer:
    id: int
    acquired_at: datetime
    region: str
    preferred_channel: str


@dataclass(frozen=True, slots=True)
class _Refund:
    at: datetime
    cents: int
    returned_quantity: int


@dataclass(frozen=True, slots=True)
class _Line:
    product: _Product
    quantity: int
    price_cents: int
    discount_cents: int
    cost_cents: int
    refunds: tuple[_Refund, ...] = ()

    @property
    def sale_cents(self) -> int:
        return self.quantity * self.price_cents - self.discount_cents


@dataclass(frozen=True, slots=True)
class _Order:
    customer: _Customer
    at: datetime
    channel: str
    status: str
    lines: tuple[_Line, ...]

    @property
    def recognized(self) -> bool:
        return self.status == "completed"

    @property
    def refund_cents(self) -> int:
        return sum(r.cents for line in self.lines for r in line.refunds)

    @property
    def sale_cents(self) -> int:
        return sum(line.sale_cents for line in self.lines)

    @property
    def net_cents(self) -> int:
        return self.sale_cents - self.refund_cents if self.recognized else 0

    @property
    def snapshot_status(self) -> str:
        if not self.refund_cents:
            return self.status
        return "refunded" if self.refund_cents == self.sale_cents else "partially_refunded"


@dataclass(frozen=True, slots=True)
class _Event:
    id: str
    offset: int
    days: int
    direction: str
    target: str
    mechanism: str
    settlement_days: int = 0


_EVENTS = (
    _Event("mobile-acquisition-campaign", 134, 1, "up", "Mobile App",
           "A paid mobile acquisition campaign adds completed orders, weighted toward first-time purchasers."),
    _Event("owned-checkout-outage", 261, 1, "down", "Direct Web and Mobile App",
           "An owned-channel checkout incident cancels most otherwise completed orders on those channels."),
    _Event("warehouse-returns-wave", 439, 2, "down", "Home & Living, Electronics and Kitchen",
           "A warehouse damage incident triggers a wave of full line refunds and returned quantities. "
           "Refunds are recorded later and attributed to the original order date in the purchase-day view.", 18),
    _Event("premium-bundle-feature", 561, 2, "up", "Electronics on Direct Web and Mobile App",
           "A premium bundle placement shifts merchandise mix toward the Studio Audio Bundle while holding order count fixed."),
    _Event("mobile-coupon-over-redemption", 627, 1, "down", "Mobile App",
           "A mobile coupon is accepted more often than intended, lowering net revenue without removing orders."),
)


def _basis_points(cents: int, rate: int) -> int:
    return (cents * rate + 5000) // 10000


def _holiday_sale(day: date) -> bool:
    first = date(day.year, 11, 1)
    thanksgiving = first + timedelta(days=(3 - first.weekday()) % 7 + 21)
    return 1 <= (day - thanksgiving).days <= 4


def _basket_weights(month: int, channel: str) -> tuple[float, ...]:
    weights = [category[1] for category in _CATALOG]
    if month in {5, 6, 7, 8}:
        weights[4] *= 1.6
    if month in {11, 12}:
        weights[1] *= 1.3
        weights[5] *= 1.25
    if channel == "Marketplace":
        weights[1] *= 1.4
    elif channel == "Mobile App":
        weights[2] *= 1.25
    return tuple(weights)


class _Store:
    def __init__(self, seed: int, start: date, end: date):
        self.rng = Random(seed)
        self.start = start
        self.end = end
        self.as_of = datetime.combine(end, time.min)
        self.customers: list[_Customer] = []
        self.regulars: list[_Customer] = []
        self.daily: dict[date, list[_Order]] = {}
        self.products: list[_Product] = []
        self.catalog: list[tuple[_Product, ...]] = []
        for category, _, cost_bps, items in _CATALOG:
            group = tuple(
                _Product(len(self.products) + index, name, category, price,
                         _basis_points(price, cost_bps + self.rng.randint(-350, 350)))
                for index, (name, price) in enumerate(items, 1)
            )
            self.products.extend(group)
            self.catalog.append(group)
        self.premium_bundle = self.catalog[1][-1]
        self.basket_weights = {(month, channel): _basket_weights(month, channel)
                               for month in range(1, 13) for channel in _CHANNELS}

    def new_customer(self, at: datetime, *, mobile: bool = False) -> _Customer:
        rng = self.rng
        customer = _Customer(
            len(self.customers) + 1,
            at - timedelta(days=rng.randint(0, 25), minutes=rng.randint(0, 1439)),
            rng.choices(_REGIONS, (24, 21, 18, 26, 11), k=1)[0],
            "Mobile App" if mobile else rng.choices(_CHANNELS, (54, 29, 17), k=1)[0],
        )
        self.customers.append(customer)
        if rng.random() < 0.23:
            self.regulars.append(customer)
        return customer

    def repeat_customer(self) -> _Customer:
        draw = self.rng.random()
        if draw < 0.40 and self.regulars:
            return self.rng.choice(self.regulars)
        if draw < 0.83:
            # Index into the last 1,200 customers without copying the pool per order.
            return self.customers[self.rng.randrange(max(0, len(self.customers) - 1200), len(self.customers))]
        return self.rng.choice(self.customers)

    def make_line(
        self, product: _Product, at: datetime, quantity: int, status: str, promotion: bool,
    ) -> _Line:
        rng = self.rng
        year_offset = at.year - self.start.year
        price = product.price_cents + _basis_points(product.price_cents, 280 * year_offset)
        cost = product.cost_cents + _basis_points(product.cost_cents, 370 * year_offset)
        discount_rate = rng.choices(
            (0, 500, 1000, 1500, 2000), (15, 8, 27, 33, 17) if promotion else (66, 13, 12, 7, 2), k=1,
        )[0]
        line = _Line(product, quantity, price, _basis_points(price * quantity, discount_rate), cost)
        if status != "completed":
            return line
        return_probability = 0.082 if product.category == "Apparel" else 0.039
        if rng.random() >= return_probability:
            return line
        returned_at = at + timedelta(days=rng.randint(2, 37), hours=rng.randint(0, 23))
        if returned_at >= self.as_of:
            return line
        if rng.random() < 0.16:
            return replace(line, refunds=(_Refund(returned_at, _basis_points(line.sale_cents, 1800), 0),))
        returned = rng.choices((1, quantity), (24, 76), k=1)[0]
        cents = line.sale_cents * returned // quantity
        events = [_Refund(returned_at, cents, returned)]
        # Some partial physical returns receive a later separate courtesy credit.
        credit_at = returned_at + timedelta(days=2)
        if returned < quantity and credit_at < self.as_of and rng.random() < 0.25:
            events.append(_Refund(credit_at, _basis_points(line.sale_cents - cents, 900), 0))
        return replace(line, refunds=tuple(events))

    def make_order(self, day: date, *, campaign: bool = False) -> _Order:
        rng = self.rng
        hour = rng.choices(range(24), (2, 1, 1, 1, 1, 2, 3, 4, 5, 6, 7, 8, 8, 7, 7, 7, 8, 9, 10, 11, 11, 9, 6, 4), k=1)[0]
        at = datetime.combine(day, time(hour, rng.randrange(60), rng.randrange(60)))
        # Campaign orders are injected after baseline creation, so reuse only customers
        # who had already made a baseline purchase by the affected day.
        if campaign:
            eligible = [o.customer for o in self.daily[day] if o.at <= at and o.recognized]
            customer = rng.choice(eligible) if eligible and rng.random() < 0.28 else self.new_customer(at, mobile=True)
        else:
            new_probability = 0.25 + 0.24 * exp(-len(self.customers) / 1600)
            customer = self.new_customer(at) if not self.customers or rng.random() < new_probability else self.repeat_customer()
            # Daily order slots are generated in random time order. An existing customer
            # may have been acquired earlier in this loop but after this order's time.
            if customer.acquired_at > at:
                at = customer.acquired_at
        channel = "Mobile App" if campaign else customer.preferred_channel
        if not campaign and rng.random() < 0.16:
            channel = rng.choices(_CHANNELS, (51, 32, 17), k=1)[0]
        status = "completed"
        if not campaign and rng.random() < 0.031:
            status = "canceled"
        elif not campaign and (self.end - day).days <= 2 and rng.random() < 0.13:
            status = "pending"
        lines = []
        used: set[int] = set()
        for _ in range(rng.choices((1, 2, 3, 4), (55, 29, 12, 4), k=1)[0]):
            while True:
                category = rng.choices(range(len(self.catalog)), self.basket_weights[(day.month, channel)], k=1)[0]
                product = rng.choices(self.catalog[category], _PRODUCT_WEIGHTS, k=1)[0]
                if product.id not in used:
                    used.add(product.id)
                    break
            quantity = rng.choices((1, 2, 3, 4), (82, 14, 3, 1), k=1)[0]
            lines.append(self.make_line(product, at, quantity, status, campaign or _holiday_sale(day)))
        return _Order(customer, at, channel, status, tuple(lines))

    def build_baseline(self) -> None:
        for elapsed in range((self.end - self.start).days):
            day = self.start + timedelta(days=elapsed)
            seasonal = _MONTHLY_DEMAND[day.month - 1] * _WEEKDAY_DEMAND[day.weekday()]
            holiday = 1.35 if _holiday_sale(day) else 1
            demand = (49 + 0.029 * elapsed) * seasonal * holiday * max(0.72, self.rng.gauss(1, 0.095))
            self.daily[day] = [self.make_order(day) for _ in range(round(demand))]

    def modify_order(self, event: _Event, order: _Order) -> _Order:
        if not order.recognized:
            return order
        rng = self.rng
        if event.id == "owned-checkout-outage" and order.channel in _OWNED_CHANNELS and rng.random() < 0.87:
            return replace(order, status="canceled",
                           lines=tuple(replace(line, refunds=()) for line in order.lines))
        if event.id == "warehouse-returns-wave":
            lines = []
            for line in order.lines:
                if line.product.category in {"Home & Living", "Electronics", "Kitchen"} and rng.random() < 0.79:
                    at = order.at + timedelta(days=rng.randint(5, 17))
                    line = replace(line, refunds=(_Refund(at, line.sale_cents, line.quantity),))
                lines.append(line)
            return replace(order, lines=tuple(lines))
        if (event.id == "premium-bundle-feature" and order.channel in _OWNED_CHANNELS
                and not any(line.product == self.premium_bundle for line in order.lines) and rng.random() < 0.65):
            # Replace the cheapest line when the new bundle is higher value.
            index = min(range(len(order.lines)), key=lambda i: order.lines[i].sale_cents)
            bundle = self.make_line(self.premium_bundle, order.at, 1, order.status, False)
            old = order.lines[index]
            if bundle.sale_cents - sum(r.cents for r in bundle.refunds) > old.sale_cents - sum(r.cents for r in old.refunds):
                return replace(order, lines=order.lines[:index] + (bundle,) + order.lines[index + 1:])
        if event.id == "mobile-coupon-over-redemption" and order.channel == "Mobile App" and rng.random() < 0.60:
            return replace(order, lines=tuple(
                replace(line, discount_cents=max(line.discount_cents, _basis_points(line.price_cents * line.quantity, 1900)))
                if not line.refunds else line for line in order.lines
            ))
        return order

    def inject_events(self) -> list[dict]:
        manifest = []
        for event in _EVENTS:
            start = self.start + timedelta(days=event.offset)
            end = start + timedelta(days=event.days)
            if end + timedelta(days=event.settlement_days) > self.end:
                continue
            baseline_orders = observed_orders = baseline_net = observed_net = affected_orders = 0
            for offset in range(event.days):
                day = start + timedelta(days=offset)
                old = self.daily[day]
                if event.id == "mobile-acquisition-campaign":
                    added = [self.make_order(day, campaign=True) for _ in range(round(len(old) * 0.98))]
                    new = old + added
                    affected_orders += len(added)
                else:
                    new = [self.modify_order(event, order) for order in old]
                    affected_orders += sum(before != after for before, after in zip(old, new, strict=True))
                baseline_orders += sum(order.recognized for order in old)
                observed_orders += sum(order.recognized for order in new)
                baseline_net += sum(order.net_cents for order in old)
                observed_net += sum(order.net_cents for order in new)
                self.daily[day] = new
            manifest.append({
                "id": event.id, "start": start.isoformat(), "end": end.isoformat(),
                "scope": "overall", "direction": event.direction, "target": event.target, "mechanism": event.mechanism,
                "affected_orders": affected_orders, "baseline_recognized_orders": baseline_orders,
                "observed_recognized_orders": observed_orders, "baseline_net_revenue_cents": baseline_net,
                "observed_net_revenue_cents": observed_net, "net_revenue_delta_cents": observed_net - baseline_net,
            })
        return manifest


_COLUMNS = {
    "customers": ("customer_id", "acquired_at"),
    "products": ("product_id", "product_name", "category"),
    "orders": ("order_id", "customer_id", "ordered_at", "region", "channel", "status"),
    "order_lines": ("line_id", "order_id", "product_id", "quantity", "unit_price_cents", "discount_cents", "unit_cost_cents"),
    "refunds": ("refund_id", "line_id", "refunded_at", "refund_cents", "returned_quantity"),
}


def _records(store: _Store) -> dict[str, list[tuple]]:
    rows: dict[str, list[tuple]] = {
        "customers": [(c.id, c.acquired_at) for c in store.customers],
        "products": [(p.id, p.name, p.category) for p in store.products],
        "orders": [], "order_lines": [], "refunds": [],
    }
    for order_id, order in enumerate(
        (order for daily_orders in store.daily.values() for order in sorted(daily_orders, key=lambda o: o.at)), 1,
    ):
        rows["orders"].append((order_id, order.customer.id, order.at, order.customer.region,
                               order.channel, order.snapshot_status))
        for line in order.lines:
            line_id = len(rows["order_lines"]) + 1
            rows["order_lines"].append((line_id, order_id, line.product.id, line.quantity,
                                        line.price_cents, line.discount_cents, line.cost_cents))
            for refund in line.refunds:
                rows["refunds"].append((len(rows["refunds"]) + 1, line_id, refund.at, refund.cents,
                                        refund.returned_quantity))
    return rows


def generate(
    conn: duckdb.DuckDBPyConnection, seed: int = DEFAULT_SEED, start: date = DEFAULT_START,
    months: int = DEFAULT_MONTHS, *, inject_anomalies: bool = True,
) -> dict:
    """Create the synthetic dataset and return its separate verification manifest.

    Calendar and monetary fields use UTC timestamps without offsets and integer USD cents.
    The generator never writes the answer key into DuckDB.
    """
    if type(start) is not date or start.day != 1:
        raise ValueError("start must be the first day of a calendar month")
    if type(months) is not int or not 1 <= months <= 60:
        raise ValueError("months must be an integer in [1, 60]")
    if type(seed) is not int or not 0 <= seed <= 2**63 - 1:
        raise ValueError("seed must be a nonnegative signed 64-bit integer")
    end_year, end_month = divmod(start.year * 12 + start.month - 1 + months, 12)
    end = date(end_year, end_month + 1, 1)
    store = _Store(seed, start, end)
    store.build_baseline()
    anomalies = store.inject_events() if inject_anomalies else []
    rows = _records(store)
    conn.execute("BEGIN")
    try:
        initialize_schema(conn)
        for table, records in rows.items():
            if not records:
                continue
            conn.register("_generator_rows", pd.DataFrame.from_records(records, columns=_COLUMNS[table]))
            try:
                conn.execute(f"INSERT INTO {table} BY NAME SELECT * FROM _generator_rows")
            finally:
                conn.unregister("_generator_rows")
        conn.execute("INSERT INTO dataset_meta (coverage_start, coverage_end, data_as_of, seed) VALUES (?, ?, ?, ?)",
                     [start, end, store.as_of, seed])
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

    return {
        "schema_version": 1, "synthetic": True, "currency": "USD", "seed": seed,
        "coverage_start": start.isoformat(), "coverage_end": end.isoformat(),
        "data_as_of": store.as_of.isoformat(), "time_zone": "UTC",
        "attribution": "Original order date; refund events posted strictly before data_as_of.",
        "row_counts": {table: len(records) for table, records in rows.items()}, "anomalies": anomalies,
    }
