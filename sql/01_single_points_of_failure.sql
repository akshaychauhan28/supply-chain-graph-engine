-- Single points of failure, ranked by production value they would halt.
--
-- The headline risk register. Each row is one node removed on its own, with
-- the cascade allowed to propagate downstream through the bill of materials.
--
-- IMPORTANT -- read `cumulative_share` carefully. It is a running total of
-- INDEPENDENT single-node failures, not the combined effect of losing all of
-- them at once. Those failures overlap: two suppliers can both halt the same
-- OEM, and this column double-counts that OEM. It is useful for seeing how
-- fast risk concentrates in a handful of nodes, and wrong if read as
-- "the top 10 together halt X% of production". Joint failure needs its own
-- simulation.

WITH ranked AS (
    SELECT
        n.node_id,
        n.node_name,
        t.tier_name,
        n.region_id,
        n.category_id,
        c.failed_oems,
        c.failed_firms,
        c.deepest_cascade,
        c.production_value_at_risk,
        c.share_of_oem_value,
        c.best_centrality_rank,
        c.blockade_share_of_oem_value,
        ROW_NUMBER() OVER (ORDER BY c.production_value_at_risk DESC) AS impact_rank
    FROM fact_node_criticality c
    JOIN dim_node   n ON n.node_id = c.node_id
    JOIN dim_tier   t ON t.tier_id = n.tier_id
    WHERE c.production_value_at_risk > 0
)
SELECT
    impact_rank,
    node_name,
    tier_name,
    region_id,
    category_id                                     AS produces,
    failed_oems,
    failed_firms,
    deepest_cascade,
    production_value_at_risk,
    share_of_oem_value,
    SUM(share_of_oem_value) OVER (
        ORDER BY impact_rank
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    )                                               AS cumulative_share_overlapping,
    best_centrality_rank,
    best_centrality_rank - impact_rank              AS centrality_rank_gap,
    blockade_share_of_oem_value
FROM ranked
WHERE impact_rank <= 30
ORDER BY impact_rank;
