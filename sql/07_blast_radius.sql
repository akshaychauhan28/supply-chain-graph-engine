-- Everything downstream of one supplier, by how many hops away it sits.
--
-- Takes a bound root_node_id and walks the supply chain forward. This is the
-- query a relational model does genuinely well -- a recursive CTE is a graph
-- traversal expressed in SQL, and it means an analyst can answer "who is
-- exposed to this vendor" without leaving the warehouse.
--
-- (Note the parameter is named without its leading colon in this comment on
-- purpose. SQLAlchemy's text() scans the whole string for bind parameters and
-- does not skip SQL comments, so a colon-prefixed name written here becomes a
-- second placeholder the database never sees -- and the query fails with a
-- binding-count mismatch that points at the SQL rather than the comment.)
--
-- Two notes on correctness:
--
-- 1. UNION rather than UNION ALL. A node is usually reachable by several
--    routes, and UNION ALL would revisit it once per route -- on a network
--    this dense that does not terminate in any useful time. UNION still admits
--    the same node at DIFFERENT depths, so the outer query takes MIN(depth) to
--    get the shortest hop distance to each firm.
--
-- 2. The depth guard is not decoration. The commercial graph is acyclic today
--    and the generator's tests enforce that, but a recursive CTE over a cyclic
--    edge table runs forever, and a query that hangs in production is a worse
--    failure than one that returns a truncated answer.
--
-- What this measures, and what it does NOT: this is the POTENTIAL blast
-- radius -- every firm with any dependency path back to this supplier. The
-- ACTUAL cascade is far smaller, because most of those firms have alternative
-- suppliers for the input in question and carry on unaffected. Compare against
-- failed_firms in fact_node_criticality: the ratio between the two is exactly
-- what redundancy buys you.

WITH RECURSIVE downstream(node_id, depth) AS (
        SELECT :root_node_id, 0
    UNION
        SELECT s.buyer_node_id, d.depth + 1
        FROM downstream d
        JOIN fact_supply_relationship s
          ON s.supplier_node_id = d.node_id
        WHERE d.depth < 6
),
shortest AS (
    SELECT node_id, MIN(depth) AS hops
    FROM downstream
    WHERE depth > 0            -- exclude the supplier itself
    GROUP BY node_id
)
SELECT
    sh.hops,
    t.tier_name,
    COUNT(*)                                            AS firms_exposed,
    SUM(CASE WHEN t.tier_name = 'OEM' THEN 1 ELSE 0 END) AS finished_goods_makers,
    SUM(c.production_value_at_risk)                     AS downstream_value_at_risk
FROM shortest sh
JOIN dim_node n ON n.node_id = sh.node_id
JOIN dim_tier t ON t.tier_id = n.tier_id
LEFT JOIN fact_node_criticality c ON c.node_id = sh.node_id
GROUP BY sh.hops, t.tier_name
ORDER BY sh.hops, t.tier_name;
