-- Where does the supply base physically sit, and how exposed is each tier?
--
-- Concentration risk is regional before it is ever supplier-specific. An
-- earthquake, an export control, or a closed strait does not take out one
-- vendor -- it takes out a region, and every firm in it at once.
--
-- Two different questions are answered here and they are easy to confuse:
--   share_of_tier_value  -- how much of this LAYER comes from this region
--   sole_source_exposure -- how much of that arrives with no alternative
-- A region can supply 40% of a tier and be low risk if all of it is
-- dual-sourced, or supply 8% and be critical if none of it is.

WITH supply_by_region AS (
    SELECT
        st.tier_id,
        st.tier_name,
        sn.region_id,
        rg.trade_bloc,
        COUNT(DISTINCT sn.node_id)                          AS suppliers,
        COUNT(DISTINCT s.buyer_node_id)                     AS buyers_served,
        SUM(s.annual_value_usd)                             AS annual_value_usd,
        SUM(s.annual_value_usd) FILTER (WHERE s.is_single_source = TRUE)
                                                            AS sole_source_value_usd
    FROM fact_supply_relationship s
    JOIN dim_node   sn ON sn.node_id = s.supplier_node_id
    JOIN dim_tier   st ON st.tier_id = sn.tier_id
    JOIN dim_region rg ON rg.region_id = sn.region_id
    GROUP BY st.tier_id, st.tier_name, sn.region_id, rg.trade_bloc
)
SELECT
    tier_name,
    region_id,
    trade_bloc,
    suppliers,
    buyers_served,
    annual_value_usd,
    annual_value_usd / SUM(annual_value_usd) OVER (PARTITION BY tier_id)
                                                            AS share_of_tier_value,
    RANK() OVER (PARTITION BY tier_id ORDER BY annual_value_usd DESC)
                                                            AS rank_in_tier,
    COALESCE(sole_source_value_usd, 0)                      AS sole_source_value_usd,
    COALESCE(sole_source_value_usd, 0) / annual_value_usd   AS sole_source_exposure,
    -- How much of the whole network's flow depends on this one region.
    annual_value_usd / SUM(annual_value_usd) OVER ()        AS share_of_network_value
FROM supply_by_region
ORDER BY tier_id, annual_value_usd DESC;
