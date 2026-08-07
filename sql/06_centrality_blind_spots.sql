-- Where the topology proxy and the measured outcome disagree.
--
-- Centrality is cheap and computed from structure alone; simulation is
-- expensive and measures what actually stops producing. This query is the
-- audit of the cheap method against the expensive one, in both directions:
--
--   UNDERRATED -- low centrality, high measured damage. What a topology-only
--                 analysis would have missed. These are the ones to act on.
--   OVERRATED  -- high centrality, no measured damage. Prominent in the graph
--                 but replaceable, so effort spent hardening them is wasted.
--
-- The pattern in the overrated set is worth noting in its own right: they are
-- almost all logistics hubs, which carry enormous betweenness and cause no
-- production loss because freight simply reroutes through another gateway.
-- Centrality can see traffic. It cannot see substitutability.

WITH scored AS (
    SELECT
        n.node_name,
        t.tier_name,
        n.region_id,
        n.category_id,
        n.is_logistics_hub,
        c.best_centrality_rank,
        c.rank_impact,
        c.betweenness,
        c.customer_count,
        c.failed_firms,
        c.failed_oems,
        c.production_value_at_risk,
        c.share_of_oem_value,
        c.rank_gap,
        CASE
            WHEN c.rank_gap >= 100  THEN 'UNDERRATED by centrality'
            WHEN c.rank_gap <= -100 THEN 'OVERRATED by centrality'
            ELSE 'roughly agrees'
        END AS verdict
    FROM fact_node_criticality c
    JOIN dim_node n ON n.node_id = c.node_id
    JOIN dim_tier t ON t.tier_id = n.tier_id
    WHERE c.best_centrality_rank IS NOT NULL
      AND c.rank_impact IS NOT NULL
)
SELECT
    verdict,
    node_name,
    tier_name,
    region_id,
    category_id                     AS produces,
    best_centrality_rank,
    rank_impact,
    rank_gap,
    customer_count,
    failed_firms,
    share_of_oem_value
FROM scored
WHERE verdict <> 'roughly agrees'
  AND (rank_impact <= 60 OR best_centrality_rank <= 20)
ORDER BY
    CASE verdict WHEN 'UNDERRATED by centrality' THEN 0 ELSE 1 END,
    ABS(rank_gap) DESC
LIMIT 40;
