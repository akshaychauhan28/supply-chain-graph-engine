# Power BI build guide

Everything needed to build the risk dashboard from `data/powerbi/`. Figure 3–4 hours.

Run this first if the folder is empty:

```bash
python scripts/build_warehouse.py
python scripts/export_powerbi.py
```

---

## 1. Import

**Home → Get Data → Text/CSV**, import all eight files from `data/powerbi/`.

| Table | Rows | Role |
|---|---|---|
| `dim_tier` | 8 | Tier names and flow order |
| `dim_region` | 9 | Region and trade bloc |
| `dim_category` | 43 | Product types, cost, sourcing difficulty |
| `dim_node` | 2,690 | Every firm and hub |
| `dim_supplier` | 2,690 | Role-playing copy, prefixed `supplier_` |
| `dim_buyer` | 2,690 | Role-playing copy, prefixed `buyer_` |
| `fact_node_criticality` | 2,690 | Centrality + simulated impact, one row per node |
| `fact_supply_relationship` | 14,932 | One row per BOM line |

**Why two copies of the same dimension.** `fact_supply_relationship` points at `dim_node` twice — once as supplier, once as buyer. Power BI permits only one *active* relationship between a pair of tables, so the second would sit dormant and need `USERELATIONSHIP` in every measure touching it. Role-playing dimensions are the standard fix, and the prefixes mean "Supplier Region" and "Buyer Region" can sit on the same visual without ambiguity.

## 2. Relationships

**Model view**, drag to create these four. All should be **one-to-many**, cross-filter direction **Single**.

```
dim_supplier[supplier_node_id]  1 ──→ *  fact_supply_relationship[supplier_node_id]
dim_buyer[buyer_node_id]        1 ──→ *  fact_supply_relationship[buyer_node_id]
dim_category[category_id]       1 ──→ *  fact_supply_relationship[category_id]
dim_node[node_id]               1 ──→ *  fact_node_criticality[node_id]
```

Delete any relationship Power BI auto-detected that isn't on this list — it guesses from column names and will connect `region_id` across unrelated tables, producing ambiguous filter paths that silently return wrong totals.

**Leave `Both` alone.** Bidirectional cross-filtering seems helpful and creates circular filter paths that are very hard to debug later.

## 3. Column setup

**Sort tiers in flow order, not alphabetically.** Select `dim_tier[tier_label]` → **Column tools → Sort by column → `flow_order`**. Repeat for `tier_label` in `dim_node` and `fact_node_criticality`. Without this every tier axis reads *EMS, Equipment, Fab, Logistics, OEM…* which implies no ordering at all.

**Formatting:**

| Column | Format |
|---|---|
| `annual_value_usd`, `production_value_at_risk`, `blockade_value_at_risk` | Currency, $, 0 decimals |
| `share_of_oem_value`, `blockade_share_of_oem_value` | Percentage, 1 decimal |
| `betweenness`, `pagerank` | Decimal, 4 places |
| all `rank_*`, `*_count`, `failed_*` | Whole number |

**Hide from report view** (right-click → Hide): every `*_id` column except those you slice on, plus `is_logistics_hub` and `is_single_source` — their text equivalents `node_kind` and `sourcing_type` are already there and won't get accidentally summed.

> **Blanks are deliberate.** All 820 OEM rows have blank impact columns. Nothing sits downstream of a finished good, so failure was never simulated for them. Do **not** replace those with zero — "not applicable" and "measured, found harmless" are different claims, and filling with zero drags every tier average down and makes finished goods look like the safest thing in the network.

## 4. Measures

New table called `_Measures` (Home → Enter Data → name it → Load), then put all measures there so they stay together.

```dax
Total Annual Spend = SUM ( fact_supply_relationship[annual_value_usd] )

Sole-Source Spend =
CALCULATE ( [Total Annual Spend], fact_supply_relationship[sourcing_type] = "Sole-sourced" )

Sole-Source Share of Spend = DIVIDE ( [Sole-Source Spend], [Total Annual Spend] )
```

**BOM lines, and the grain trap.** A sole-sourced input contributes one row; a triple-sourced input contributes three. Counting rows understates sole-sourcing by roughly half — this exact mistake produced 8.4% against a true 20% earlier in the project. Count distinct `(buyer, input)` pairs instead:

```dax
BOM Lines =
COUNTROWS (
    SUMMARIZE (
        fact_supply_relationship,
        fact_supply_relationship[buyer_node_id],
        fact_supply_relationship[category_id]
    )
)

Sole-Sourced BOM Lines =
CALCULATE ( [BOM Lines], fact_supply_relationship[sourcing_type] = "Sole-sourced" )

Sole-Source Rate = DIVIDE ( [Sole-Sourced BOM Lines], [BOM Lines] )
```

```dax
Production Value at Risk = SUM ( fact_node_criticality[production_value_at_risk] )

Worst Single Failure = MAX ( fact_node_criticality[share_of_oem_value] )

Critical Nodes =
CALCULATE (
    COUNTROWS ( fact_node_criticality ),
    fact_node_criticality[share_of_oem_value] > 0.01
)

Replaceable Node Share =
VAR Simulated =
    CALCULATE (
        COUNTROWS ( fact_node_criticality ),
        NOT ISBLANK ( fact_node_criticality[failed_firms] )
    )
VAR Harmless =
    CALCULATE ( COUNTROWS ( fact_node_criticality ), fact_node_criticality[failed_firms] = 0 )
RETURN
    DIVIDE ( Harmless, Simulated )

Underrated by Centrality =
CALCULATE (
    COUNTROWS ( fact_node_criticality ),
    fact_node_criticality[rank_gap] >= 100,
    fact_node_criticality[rank_impact] <= 100
)

Worst Lead Time = MAX ( fact_supply_relationship[lead_time_days] )
Avg Lead Time = AVERAGE ( fact_supply_relationship[lead_time_days] )
```

## 5. Pages

### Page 1 — Executive summary

The only page most viewers will read. **No centrality on it anywhere.**

- **Four cards across the top:** `Total Annual Spend` · `Worst Single Failure` · `Critical Nodes` · `Sole-Source Share of Spend`
- **Horizontal bar — "Worst single failure by tier":** axis `tier_label`, value `Worst Single Failure`. This is the counterintuitive finding: fabs last at 0.3%. Add a text box explaining it models qualification redundancy, not capacity redundancy.
- **Table — top 10 single points of failure:** `node_name`, `tier_label`, `region_id`, `category_id`, `failed_oems`, `production_value_at_risk`. Sort by value descending, Top N filter = 10.
- **Slicers (left rail):** `region_id`, `tier_label`, `trade_bloc`

### Page 2 — Risk register

- **Scatter — the finding.** X `best_centrality_rank` (log scale), Y `share_of_oem_value`, legend `node_kind`, details `node_name`. Every port sits bottom-left: high centrality, zero damage. Title it *"Centrality finds none of the ten most damaging nodes."*
- **Full table:** all simulated nodes with `rank_impact`, `best_centrality_rank`, `rank_gap`, `failed_firms`, `deepest_cascade`. Conditional formatting: data bars on `share_of_oem_value`, red-amber-green on `rank_gap`.
- **Card:** `Underrated by Centrality`
- **Slicers:** `breaks_something`, `node_kind`

### Page 3 — Sole-source exposure

- **Bar — sole-source rate by input:** axis `dim_category[category_id]`, value `Sole-Source Rate`, sorted descending. Lithography tops it at ~67%.
- **Scatter — where difficulty meets exposure:** X `single_source_probability`, Y `Sole-Source Rate`, size `Sole-Source Spend`. Points near the diagonal mean the model behaved as configured.
- **Table — buyer fragility:** `buyer_node_name`, `buyer_tier_label`, `BOM Lines`, `Sole-Sourced BOM Lines`, `Sole-Source Share of Spend`, `Worst Lead Time`. Sorted by sole-sourced count.
- **Cards:** `Sole-Source Spend`, `Worst Lead Time`

### Page 4 — Geographic concentration

- **Stacked bar — value by region, broken by tier:** axis `supplier_region_id`, value `Total Annual Spend`, legend `supplier_tier_label`
- **Matrix:** rows `supplier_tier_label`, columns `supplier_region_id`, values `Total Annual Spend` shown as *% of row total*. Conditional background colouring. Taiwan's fab column is the story.
- **Donut:** `trade_bloc` by `Total Annual Spend`
- **Card:** `Sole-Source Share of Spend`, filtered by the region slicer

> **Skip the map visual.** It looks impressive and it would be dishonest here — "Southeast Asia" and "Rest of World" are aggregates, and dropping a pin on them asserts a precision the data doesn't have. Bars carry the same information without the false claim.

## 6. Look

Match the README figures so the repo reads as one piece of work:

| Role | Hex |
|---|---|
| Primary / firms | `#2a78d6` |
| Accent / hubs | `#eb6834` |
| Third series | `#1baf7a` |
| Page background | `#fcfcfb` |
| Text | `#0b0b0b` primary, `#52514e` secondary |
| Gridlines | `#e1e0d9` |

**View → Themes → Customize current theme** to set these once.

Turn off gridlines on bar charts. Give every visual a title that states a *finding*, not a field list — "Fabs are the safest tier to lose" beats "Share of OEM value by tier".

## 7. Before you call it done

- Every page has the same slicers in the same position
- No visual shows a raw `*_id` column
- Tier axes read Raw Material → Refined Material → Equipment → Fab → OSAT → EMS → OEM
- Nothing on page 1 mentions betweenness, PageRank, or centrality
- Currency reads `$43B`, not `43695829471.28`
- Export a PNG of page 1 into `docs/` and link it from the README

## Common traps

**Dual-axis charts.** Power BI makes them easy and they are the single most misleading chart type — two y-scales let you imply any relationship you like. Two measures of different magnitude belong in two visuals.

**Pie charts with nine regions.** Nobody can compare nine wedge angles. Use a sorted bar.

**Summing a rank.** `rank_impact` averaged or totalled is meaningless. Set those columns to **Don't summarize** in Column tools.

**Summing `share_of_oem_value` across nodes.** Each value is an independent single-node failure and they overlap — two suppliers can halt the same OEM. Adding them double-counts. Use `MAX` or `AVERAGE`, never `SUM`.
