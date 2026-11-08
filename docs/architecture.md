# RevenueScope architecture

RevenueScope is a local, read-only reporting application over a reproducible e-commerce snapshot. The data generator owns writes; reporting and verification each open the finished DuckDB file read-only. The command line and future dashboard use the same Python analytics API. There is no web service, account system, or external data source in Phase 1.

## Storage model

| Table | Grain | Keys and reporting use |
| --- | --- | --- |
| `dataset_meta` | One row per snapshot | Seed, declared coverage start/end and data cutoff. The Python reader rejects missing or duplicate metadata rows. |
| `customers` | One row per synthetic customer | `customer_id`; acquisition time is distinct from the first recognized purchase. |
| `products` | One row per SKU | `product_id`; category and name label the SKU. Historic line prices and costs come from the line, so catalog labels do not change financial facts. |
| `orders` | One row per checkout | `order_id`; links to the customer and stores UTC order time, status, region and channel. Region and channel describe the order. |
| `order_lines` | One row per checkout line | `line_id`; links to an order and SKU. Stores quantity, captured price and cost per unit, and the total allocated line discount. |
| `refunds` | One row per refund event | `refund_id`; links to the original line. Stores refund time, amount and returned quantity. Multiple events may refer to the same line. |

The database enforces primary and foreign keys, valid statuses, positive sale quantities and constraints on discounts and amounts. The independent verifier checks cross-row chronology, maximum refunded value/units and recognized orders without lines. Timestamps are UTC by convention and stored without a time zone; date windows use `[start, end)`. The data cutoff represents what events were observable when the snapshot was generated. Coverage indicates which order dates the dataset is designed to represent. They are separate so reports can distinguish missing coverage from a covered day with zero activity.

## Query and reporting contracts

1. Rank recognized purchases at order grain across the snapshot before filtering the reporting window. This keeps new/returning definitions stable when users change dates or break results down by segment.
2. Reduce refunds to one row per sale line at the shared cutoff. Join that result to sale lines and their unique orders/products. This avoids multiplying amounts when a line has several refund events.
3. Sum integer cent values at the requested grain and count distinct order/customer IDs. AOV, margin, and contribution shares are ratios of aggregate values; they are not averages of subgroup ratios. Only monetary measures are generally additive across product and customer-spanning segments.
4. Join daily and monthly reports to explicit calendar spines. Complete zero-activity days remain observations. Incomplete observations are labeled; they cannot drive growth comparisons, anomaly alerts, or a variance bridge.
5. Return SQL results as pandas frames. The CLI serializes exact monetary totals as integers, undefined values as null, and variance effects as numerator/denominator pairs in cents. A presentation layer can round the effects for display without losing their exact underlying reconciliation.

| Planned module | Phase 1 reporting source | Phase 2 presentation |
| --- | --- | --- |
| Executive Command Center | `analytics.summarize`, `monthly_kpis` | Overall financial KPIs, complete-period trends and denominator explanations. |
| Revenue Driver Analysis | `variance.compare_periods`, `analytics.summarize` by dimension | Volume/AOV bridge and independently reconciling segment changes. |
| Anomaly Investigation | `analytics.daily_revenue`, `anomaly.detect_anomalies` | Observed versus expected revenue, historical support and segment investigation. |
| Customer & Cohort Intelligence | `analytics.customer_cohorts` and customer KPIs | Cohort, repeat-purchase and retention views that distinguish unobserved cells from zero. |
| Scenario & Decision Lab | Observed KPI and variance baselines | Explicit what-if assumptions and scenario comparisons. No simulation endpoint in Phase 1. |

The default CLI report includes no injected-event labels. The verification manifest and evaluator output are developer artifacts. A future public build should exclude both and use only the synthetic database and detector results.

## Validation and extension points

- Small, hand-authored DuckDB fixtures independently specify expected financial results, count grain, refund cutoff, boundary dates, cohorts, growth and exact bridge effects.
- Generator tests compare two seeded snapshots and a no-injection run, checking realism properties and whether event changes spill into other order dates.
- Anomaly tests use constructed weekday series to check behavior independently of the generated dataset. The manifest is only applied after scoring.
- The full-snapshot verifier computes revenue and order counts from raw tables in Python, checks SQL at overall, daily, monthly and segment grains, and can rebuild the observed and counterfactual datasets. A logical table digest verifies reproducibility without relying on DuckDB file bytes.

Future changes to currency, order recognition, refund attribution or cohort eligibility should begin with a written metric contract and a hand-calculated fixture, then update the SQL and any affected reporting consumers together.
