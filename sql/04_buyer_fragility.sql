-- Which buyers are one supplier failure away from stopping?
--
-- This is the query that a spend report cannot produce, and the reason the
-- fact table is kept at BOM-line grain.
--
-- Ranking buyers by supplier count says the firms with the fewest vendors are
-- the exposed ones. That is wrong. A buyer with twenty suppliers is still one
-- fire away from a line stop if a single INPUT has one qualified source, and a
-- buyer with three suppliers covering three dual-sourced inputs is fine.
-- Fragility is a property of inputs, so the inner query aggregates to
-- (buyer, input) first and only then counts.
--
-- `sole_source_share_of_spend` is the number to act on: it is the fraction of
-- a buyer's purchasing that has no alternative behind it.

WITH buyer_inputs AS (
    SELECT
        s.buyer_node_id,
        s.category_id,
        MIN(s.qualified_alternatives)       AS alternatives,
        SUM(s.annual_value_usd)             AS input_value_usd,
        MAX(s.lead_time_days)               AS input_lead_time_days
    FROM fact_supply_relationship s
    GROUP BY s.buyer_node_id, s.category_id
),
buyer_profile AS (
    SELECT
        b.buyer_node_id,
        COUNT(*)                                            AS distinct_inputs,
        COUNT(*) FILTER (WHERE b.alternatives = 0)          AS sole_sourced_inputs,
        SUM(b.input_value_usd)                              AS total_spend_usd,
        SUM(b.input_value_usd) FILTER (WHERE b.alternatives = 0)
                                                            AS sole_source_spend_usd,
        MAX(b.input_lead_time_days)                         AS worst_lead_time_days
    FROM buyer_inputs b
    GROUP BY b.buyer_node_id
)
SELECT
    n.node_name                                             AS buyer,
    t.tier_name,
    n.region_id,
    n.category_id                                           AS produces,
    p.distinct_inputs,
    p.sole_sourced_inputs,
    CAST(p.sole_sourced_inputs AS FLOAT) / p.distinct_inputs
                                                            AS sole_sourced_input_rate,
    p.total_spend_usd,
    COALESCE(p.sole_source_spend_usd, 0)                    AS sole_source_spend_usd,
    COALESCE(p.sole_source_spend_usd, 0) / p.total_spend_usd
                                                            AS sole_source_share_of_spend,
    p.worst_lead_time_days
FROM buyer_profile p
JOIN dim_node n ON n.node_id = p.buyer_node_id
JOIN dim_tier t ON t.tier_id = n.tier_id
WHERE p.sole_sourced_inputs > 0
ORDER BY p.sole_sourced_inputs DESC, sole_source_share_of_spend DESC
LIMIT 30;
