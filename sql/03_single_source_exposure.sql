-- Which inputs are we sole-sourced on, and what is riding on them?
--
-- The grain problem this query solves is the one that produced a real bug
-- earlier in the project. The fact table stores one row per supplier
-- relationship, so a sole-sourced input contributes ONE row while a
-- triple-sourced input contributes THREE. Counting rows therefore understates
-- sole-sourcing by roughly half.
--
-- A bill-of-materials line is (buyer, input), so counting DISTINCT buyers
-- within a category counts lines correctly -- a buyer appears at most once per
-- category by construction.
--
-- `configured_difficulty` is the modelled probability that an input is
-- sole-sourced, driven by how hard that input is to requalify. Comparing it to
-- `observed_single_source_rate` shows the rule and the outcome side by side.

SELECT
    c.category_id                                       AS input,
    t.tier_name                                         AS supplied_by_tier,
    c.single_source_probability                         AS configured_difficulty,
    COUNT(DISTINCT s.buyer_node_id)                     AS bom_lines,
    COUNT(DISTINCT s.buyer_node_id)
        FILTER (WHERE s.is_single_source = TRUE)        AS sole_sourced_lines,
    CAST(COUNT(DISTINCT s.buyer_node_id)
         FILTER (WHERE s.is_single_source = TRUE) AS FLOAT)
        / COUNT(DISTINCT s.buyer_node_id)               AS observed_single_source_rate,
    COUNT(DISTINCT s.supplier_node_id)                  AS qualified_suppliers,
    SUM(s.annual_value_usd)                             AS annual_value_usd,
    SUM(s.annual_value_usd)
        FILTER (WHERE s.is_single_source = TRUE)        AS value_on_sole_source,
    MAX(s.lead_time_days)                               AS worst_lead_time_days
FROM fact_supply_relationship s
JOIN dim_category c ON c.category_id = s.category_id
JOIN dim_tier     t ON t.tier_id     = c.tier_id
GROUP BY c.category_id, t.tier_name, c.single_source_probability
HAVING COUNT(DISTINCT s.buyer_node_id) >= 20
ORDER BY observed_single_source_rate DESC;
