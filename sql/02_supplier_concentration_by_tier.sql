-- How concentrated is spend within each tier?
--
-- The classic procurement question, answered per layer of the chain rather
-- than across the whole network -- because a tier with five viable vendors
-- and a tier with two hundred carry completely different risk even at the
-- same total spend.
--
-- `cumulative_share_of_tier` is the honest concentration measure here: read
-- down a tier until it crosses 50% and you have the number of suppliers that
-- account for half that layer's value.

WITH supplier_load AS (
    SELECT
        s.supplier_node_id                          AS node_id,
        COUNT(DISTINCT s.buyer_node_id)             AS customers,
        COUNT(*)                                    AS bom_lines_served,
        SUM(s.annual_value_usd)                     AS annual_value_usd,
        COUNT(*) FILTER (WHERE s.is_single_source = TRUE) AS sole_source_lines
    FROM fact_supply_relationship s
    GROUP BY s.supplier_node_id
),
scored AS (
    SELECT
        t.tier_id,
        t.tier_name,
        n.node_name,
        n.region_id,
        n.category_id,
        l.customers,
        l.sole_source_lines,
        l.annual_value_usd,
        RANK() OVER (
            PARTITION BY t.tier_id ORDER BY l.annual_value_usd DESC
        )                                           AS rank_in_tier,
        l.annual_value_usd
            / SUM(l.annual_value_usd) OVER (PARTITION BY t.tier_id)
                                                    AS share_of_tier,
        SUM(l.annual_value_usd) OVER (
            PARTITION BY t.tier_id
            ORDER BY l.annual_value_usd DESC
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) / SUM(l.annual_value_usd) OVER (PARTITION BY t.tier_id)
                                                    AS cumulative_share_of_tier,
        COUNT(*) OVER (PARTITION BY t.tier_id)      AS suppliers_in_tier
    FROM supplier_load l
    JOIN dim_node n ON n.node_id = l.node_id
    JOIN dim_tier t ON t.tier_id = n.tier_id
)
SELECT
    tier_name,
    rank_in_tier,
    node_name,
    region_id,
    category_id                                     AS produces,
    customers,
    sole_source_lines,
    annual_value_usd,
    share_of_tier,
    cumulative_share_of_tier,
    suppliers_in_tier
FROM scored
WHERE rank_in_tier <= 5
ORDER BY tier_id, rank_in_tier;
