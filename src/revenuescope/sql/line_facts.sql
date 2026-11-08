-- Recognized orders must have at least one line. The ordering tie-break also
-- applies to same-timestamp purchases by the same customer.
known_orders AS (
    SELECT o.*
    FROM orders AS o
    CROSS JOIN dataset_meta AS m
    WHERE o.status IN ('completed', 'partially_refunded', 'refunded')
      AND o.ordered_at >= m.coverage_start
      AND o.ordered_at < m.coverage_end
      AND o.ordered_at < m.data_as_of
      AND EXISTS (SELECT 1 FROM order_lines AS l WHERE l.order_id = o.order_id)
),
ranked_orders AS (
    SELECT o.*,
           ROW_NUMBER() OVER (
               PARTITION BY customer_id ORDER BY ordered_at, order_id
           ) AS customer_order_number,
           MIN(ordered_at::DATE) OVER (PARTITION BY customer_id) AS first_order_date
    FROM known_orders AS o
),
-- Reduce refund events to the line grain before joining to sales.
refunds_by_line AS (
    SELECT r.line_id,
           SUM(r.refund_cents)::BIGINT AS refunds_cents,
           SUM(r.returned_quantity)::BIGINT AS returned_quantity
    FROM refunds AS r
    CROSS JOIN dataset_meta AS m
    WHERE r.refunded_at < m.data_as_of
    GROUP BY r.line_id
),
line_amounts AS (
    SELECT o.order_id, o.customer_id, o.ordered_at::DATE AS order_date,
           o.channel, o.region, o.customer_order_number, o.first_order_date,
           p.product_id, p.product_name, p.category,
           l.quantity * l.unit_price_cents AS gross_sales_cents,
           l.discount_cents AS discounts_cents,
           COALESCE(r.refunds_cents, 0) AS refunds_cents,
           (l.quantity - COALESCE(r.returned_quantity, 0)) * l.unit_cost_cents AS cogs_cents
    FROM ranked_orders AS o
    JOIN order_lines AS l ON l.order_id = o.order_id
    JOIN products AS p ON p.product_id = l.product_id
    LEFT JOIN refunds_by_line AS r ON r.line_id = l.line_id
),
line_facts AS (
    SELECT *,
           gross_sales_cents - discounts_cents - refunds_cents AS net_revenue_cents
    FROM line_amounts
)
