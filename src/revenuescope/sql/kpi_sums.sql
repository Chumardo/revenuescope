COALESCE(SUM(f.gross_sales_cents), 0)::BIGINT AS gross_sales_cents,
COALESCE(SUM(f.discounts_cents), 0)::BIGINT AS discounts_cents,
COALESCE(SUM(f.refunds_cents), 0)::BIGINT AS refunds_cents,
COALESCE(SUM(f.net_revenue_cents), 0)::BIGINT AS net_revenue_cents,
COALESCE(SUM(f.cogs_cents), 0)::BIGINT AS cogs_cents,
COALESCE(SUM(f.net_revenue_cents - f.cogs_cents), 0)::BIGINT AS gross_profit_cents,
COUNT(DISTINCT f.order_id) AS orders,
COUNT(DISTINCT f.customer_id) AS active_customers,
COUNT(DISTINCT f.customer_id) FILTER (
    WHERE f.first_order_date >= b.start_date AND f.first_order_date < b.end_date
) AS new_customers,
COUNT(DISTINCT f.customer_id) FILTER (WHERE f.first_order_date < b.start_date) AS returning_customers,
COUNT(DISTINCT f.order_id) FILTER (WHERE f.customer_order_number = 1) AS new_customer_orders,
COUNT(DISTINCT f.order_id) FILTER (WHERE f.customer_order_number > 1) AS returning_customer_orders,
COALESCE(SUM(f.net_revenue_cents) FILTER (
    WHERE f.customer_order_number = 1
), 0)::BIGINT AS new_customer_net_revenue_cents,
COALESCE(SUM(f.net_revenue_cents) FILTER (
    WHERE f.customer_order_number > 1
), 0)::BIGINT AS returning_customer_net_revenue_cents
