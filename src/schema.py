"""Domain schema for the semiconductor supply network.

This module describes *what kinds of things exist* in the graph -- the tiers
material flows through, the regions firms operate in, and the product
categories that move along each edge.

Design rule that governs this whole project:
    Nothing here assigns importance to any node. No `criticality` field, no
    `is_bottleneck` flag, no hand-picked hub. Criticality is a property the
    analysis layer *discovers* from topology. If we declared it here, the
    centrality results would just be reading our own answer back to us.

What we do encode is *mechanism*: which tier buys from which, how firm size is
distributed, how strongly sourcing is regionally clustered. Those are
empirically grounded rules. The hubs and chokepoints they produce are
consequences we can trace, not values we typed in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum


class Tier(IntEnum):
    """Position in the material flow, upstream (low) to downstream (high).

    The real semiconductor chain is a "bowtie": very wide at raw materials,
    very wide again at finished products, and extremely narrow in the middle
    where wafer fabrication and lithography equipment sit. That narrow waist is
    not decoration -- it is the structural reason the industry is fragile, and
    the tier sizes in config.py are chosen to reproduce it.
    """

    RAW_MATERIAL = 0      # ore, polysilicon feedstock, crude gases
    REFINED_MATERIAL = 1  # wafers, photoresist, process gases, substrates
    EQUIPMENT = 2         # litho, etch, deposition, metrology, test handlers
    FAB = 3               # wafer fabrication / foundry
    OSAT = 4              # outsourced assembly and test (packaging)
    EMS = 5               # board assembly / electronic manufacturing services
    OEM = 6               # finished goods
    LOGISTICS = 99        # seaports and air hubs -- cross-cutting, not a tier


class Region(StrEnum):
    """Geography at a granularity where real concentration is visible.

    Taiwan is deliberately its own region rather than being folded into "East
    Asia". Roughly 90% of leading-edge fabrication happens there; collapsing it
    into a larger bucket would hide the single most important structural fact
    about this industry.
    """

    TAIWAN = "Taiwan"
    CHINA = "China"
    JAPAN = "Japan"
    KOREA = "Korea"
    SEA = "Southeast Asia"       # Malaysia, Philippines, Singapore, Vietnam
    NORTH_AMERICA = "North America"
    EUROPE = "Europe"
    SOUTH_ASIA = "South Asia"
    ROW = "Rest of World"        # Australia, Chile, Central Africa -- mining


class Category(StrEnum):
    """What actually flows along an edge.

    Edges are typed by product, not just by "A supplies B". This matters for
    the vulnerability analysis: a buyer having three suppliers is meaningless
    if all three supply *different* inputs and each input has exactly one
    source. Single-sourcing is a per-category property, so category has to be
    first-class.
    """

    # Tier 0 -- raw
    SILICON_FEEDSTOCK = "Silicon feedstock"
    RARE_EARTH = "Rare earth elements"
    COPPER = "Copper"
    GOLD = "Gold"
    CRUDE_GAS = "Crude industrial gas"
    BULK_CHEMICAL = "Bulk chemical"
    RESIN = "Substrate resin"

    # Tier 1 -- refined
    WAFER = "Silicon wafer"
    PHOTORESIST = "Photoresist"
    PROCESS_GAS = "Process gas"
    CMP_SLURRY = "CMP slurry"
    PACKAGING_SUBSTRATE = "Packaging substrate"
    BONDING_WIRE = "Bonding wire"
    PCB_LAMINATE = "PCB laminate"

    # Tier 2 -- equipment
    LITHOGRAPHY = "Lithography system"
    ETCH = "Etch system"
    DEPOSITION = "Deposition system"
    METROLOGY = "Metrology system"
    ION_IMPLANT = "Ion implantation system"
    TEST_HANDLER = "Test handler"

    # Tier 3 -- fab output (bare die)
    LOGIC_DIE = "Logic die"
    MEMORY_DIE = "Memory die"
    ANALOG_DIE = "Analog die"
    POWER_DIE = "Power die"
    RF_DIE = "RF die"
    MEMS_DIE = "MEMS die"

    # Tier 4 -- packaged parts
    LOGIC_IC = "Logic IC"
    MEMORY_IC = "Memory IC"
    ANALOG_IC = "Analog IC"
    POWER_IC = "Power IC"
    RF_IC = "RF IC"
    MEMS_SENSOR = "MEMS sensor"

    # Tier 5 -- assemblies
    PCB_ASSEMBLY = "PCB assembly"
    POWER_MODULE = "Power module"
    SENSOR_MODULE = "Sensor module"

    # Tier 6 -- finished goods
    SMARTPHONE = "Smartphone"
    AUTOMOTIVE_ECU = "Automotive ECU"
    SERVER = "Server"
    LAPTOP = "Laptop"
    MEDICAL_DEVICE = "Medical device"
    INDUSTRIAL_CONTROLLER = "Industrial controller"
    NETWORK_EQUIPMENT = "Network equipment"

    # Logistics
    FREIGHT = "Freight"


class EdgeKind(StrEnum):
    """Why two nodes are connected.

    SUPPLIES is a commercial relationship: this firm sells that firm an input.
    SHIPS_VIA is physical routing: the goods pass through this port or air hub.

    Keeping them distinct matters for interpretation. A port with enormous
    betweenness is a logistics chokepoint you can reroute around at a cost. A
    supplier with enormous betweenness is a commercial dependency you cannot
    reroute around at all without qualifying a new vendor, which in this
    industry takes 12-24 months.
    """

    SUPPLIES = "SUPPLIES"
    SHIPS_VIA = "SHIPS_VIA"


class LogisticsKind(StrEnum):
    SEAPORT = "Seaport"
    AIR_HUB = "Air hub"


@dataclass(slots=True)
class Node:
    """A firm, facility, or logistics hub.

    `capacity_index` is relative production scale, drawn from a lognormal
    distribution because real firm sizes are heavily right-skewed. It feeds the
    attachment model as a fitness term: larger firms attract more customers.
    That is the Bianconi-Barabasi "fitness" variant of preferential attachment,
    and it is a better fit for supply chains than plain Barabasi-Albert, where
    the only thing that makes a node attractive is having arrived early.

    Note what capacity_index is *not*: it is not importance. A large firm with
    many customers in a well-supplied category may be entirely replaceable,
    while a small firm holding the only qualified source of one input may not
    be. The analysis layer is what tells those apart.
    """

    node_id: str
    name: str
    tier: Tier
    region: Region
    category: Category            # what this node produces / handles
    capacity_index: float
    logistics_kind: LogisticsKind | None = None
    serves_regions: frozenset[Region] = field(default_factory=frozenset)

    def to_attrs(self) -> dict:
        """Flatten to a NetworkX attribute dict (GraphML-safe primitives)."""
        return {
            "name": self.name,
            "tier": int(self.tier),
            "tier_name": self.tier.name,
            "region": str(self.region),
            "category": str(self.category),
            "capacity_index": round(self.capacity_index, 4),
            "logistics_kind": str(self.logistics_kind) if self.logistics_kind else "",
            "serves_regions": ",".join(sorted(str(r) for r in self.serves_regions)),
        }


@dataclass(slots=True)
class Relationship:
    """One commercial supply agreement: `source` sells `category` to `target`.

    This is the ground-truth record of the network -- one row per BOM line, the
    thing an actual procurement database would store. Both graph projections
    are derived from a list of these, and this is also what gets exported to
    Postgres for the SQL and Power BI layers.

    Attributes are what let the analysis speak in business terms instead of
    pure topology. `annual_value_usd` is the difference between reporting "this
    node has betweenness 0.31" and "this node sits between $840M of annual flow
    and the customers who depend on it".

    `qualified_alternatives` counts how many *other* suppliers the buyer has
    for this same input. Zero means single-sourced, which is the strongest
    practical predictor of supply chain fragility and is completely invisible
    to degree centrality -- a buyer with twenty suppliers can still be one
    fire away from a line stop if a single input has one qualified source.
    """

    source: str
    target: str
    category: Category
    lead_time_days: int
    annual_volume_units: int
    unit_cost_usd: float
    qualified_alternatives: int
    via_hub: str | None = None   # set when the shipment is routed through a hub

    @property
    def annual_value_usd(self) -> float:
        return self.annual_volume_units * self.unit_cost_usd

    @property
    def is_single_source(self) -> bool:
        return self.qualified_alternatives == 0

    def to_row(self) -> dict:
        """Flatten to a table row for export."""
        return {
            "supplier_id": self.source,
            "buyer_id": self.target,
            "category": str(self.category),
            "lead_time_days": self.lead_time_days,
            "annual_volume_units": self.annual_volume_units,
            "unit_cost_usd": round(self.unit_cost_usd, 4),
            "annual_value_usd": round(self.annual_value_usd, 2),
            "qualified_alternatives": self.qualified_alternatives,
            "is_single_source": int(self.is_single_source),
            "via_hub": self.via_hub or "",
        }


# Which tier sells to which. This is the DAG skeleton of the whole network.
#
# Two details here are load-bearing and worth being able to defend:
#
#   1. REFINED_MATERIAL feeds FAB, OSAT *and* EMS. Packaging substrates and
#      bonding wire go straight to the assembly houses without ever passing
#      through a fab, and PCB laminate goes straight to board assembly. This
#      creates genuine alternate paths through the network rather than a clean
#      single-file chain, which is what makes multi-hop dependency analysis
#      non-trivial.
#
#   2. EQUIPMENT feeds FAB and OSAT but nothing downstream. Equipment makers
#      are a side branch, not a link in the material chain -- a fab cannot
#      operate without them, but no atom of an equipment maker's output ends up
#      inside a finished phone. Whether that side branch shows up as critical
#      is one of the more interesting things the analysis has to settle.
TIER_FLOW: tuple[tuple[Tier, Tier], ...] = (
    (Tier.RAW_MATERIAL, Tier.REFINED_MATERIAL),
    (Tier.RAW_MATERIAL, Tier.EQUIPMENT),
    (Tier.REFINED_MATERIAL, Tier.FAB),
    (Tier.REFINED_MATERIAL, Tier.OSAT),
    (Tier.REFINED_MATERIAL, Tier.EMS),
    (Tier.EQUIPMENT, Tier.FAB),
    (Tier.EQUIPMENT, Tier.OSAT),
    (Tier.FAB, Tier.OSAT),
    (Tier.OSAT, Tier.EMS),
    (Tier.EMS, Tier.OEM),
)
