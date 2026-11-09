# RevenueScope architecture

RevenueScope is a read-only reporting application over a reproducible e-commerce snapshot. The CLI generates and checks synthetic DuckDB snapshots. The Streamlit demo opens the bundled snapshot read-only and displays Overview, Review Queue, Investigation Detail, and Evaluation pages. The workspace validates bundled evidence for the locked policy. Data generation, verification, and benchmark replay are separate developer commands.

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
2. For the retrospective order-day view, reduce refunds to one row per sale line at the shared cutoff before joining to orders and products. For posted-day activity, report recognized bookings and refund events on their own dates. Both paths avoid multiplying amounts when a line has several refunds.
3. Sum integer cent values at the requested grain and count distinct order and customer IDs. Calculate AOV, margin, and contribution shares from aggregate values. Only monetary measures are generally additive across product and customer-spanning segments.
4. Join daily and monthly reports to explicit calendar spines. Complete zero-activity days remain observations. Incomplete observations are labeled; they cannot drive growth comparisons, anomaly alerts, or a variance bridge.
5. Return SQL results as pandas frames. The CLI serializes exact monetary totals as integers, undefined values as null, and variance effects as numerator/denominator pairs in cents. A presentation layer can round the effects for display without losing their exact underlying reconciliation.

| Analytical capability | Source | Where to use it |
| --- | --- | --- |
| Overall revenue review | `pit.posted_activity`, `analytics.daily_revenue`, `screening.screen` | Streamlit Overview and Review Queue |
| Flagged-day evidence | `investigation` | Streamlit Investigation Detail with reconciled segment contributions |
| Held-out screening results | Bundled aggregate evidence, validated by `workspace_evaluation` | Streamlit Evaluation |
| Period, volume and AOV analysis | `analytics.summarize`, `monthly_kpis`, `variance.compare_periods` | CLI financial report |
| Customer cohorts and repeat purchases | `analytics.customer_cohorts` and customer KPIs | CLI financial report |

The default CLI report includes no injected-event labels. The public package includes the synthetic database, investigation evidence, and aggregate evaluation results. The raw verification manifest and detailed evaluator output stay with the developer workflow.

## Validation and extension points

- Small, hand-authored DuckDB fixtures independently specify expected financial results, count grain, refund cutoff, boundary dates, cohorts, growth and exact bridge effects.
- Generator tests compare two seeded snapshots and a no-injection run, checking realism properties and whether event changes spill into other order dates.
- Anomaly tests use constructed weekday series to check behavior independently of the generated dataset. The manifest is only applied after scoring.
- The full-snapshot verifier computes revenue and order counts from raw tables in Python, checks SQL at overall, daily, monthly and segment grains, and can rebuild the observed and counterfactual datasets. A logical table digest verifies reproducibility without relying on DuckDB file bytes.

Future changes to currency, order recognition, refund attribution or cohort eligibility should begin with a written metric contract and a hand-calculated fixture, then update the SQL and any affected reporting consumers together.
