"""Generative parameters for the synthetic semiconductor network.

Every number in this file is a *rule about how the world works*, not a fact
about any particular node. That distinction is the whole credibility of the
project, so it is worth being precise about:

    Legitimate  -- "42% of wafer fabrication capacity sits in Taiwan."
                   "Lithography systems are single-sourced ~60% of the time
                    because qualifying a second tool vendor takes years."
                   "Firm sizes follow a lognormal distribution."

    Illegitimate -- "FAB_0041 is the critical bottleneck."
                    "Port_Alpha carries 45% of electronics freight."

The first kind is an empirical claim about an industry, and it is defensible in
an interview by pointing at market-share data. The second kind is writing the
answer down and then having PageRank read it back.

So: no node is named, weighted, or privileged anywhere in this file. Which
Taiwanese fab becomes the hub, and which unremarkable mid-tier chemical firm
turns out to sit on 30% of paths, is decided by the interaction of these rules
plus a random seed -- not by us.

Sources for the regional shares are public market-concentration figures for the
semiconductor industry (SIA/TrendForce-style splits). They are approximations,
deliberately: the point is the shape of the concentration, not decimal accuracy.
"""

from __future__ import annotations

from .schema import Category as C
from .schema import TIER_FLOW, LogisticsKind, Region as R, Tier

# ---------------------------------------------------------------------------
# Scale
# ---------------------------------------------------------------------------

RANDOM_SEED = 20260806

# Tier populations. The shape here is the point: wide at both ends, pinched
# hard in the middle at FAB and EQUIPMENT. That is the real "bowtie" of the
# semiconductor industry, and it is why a disruption at the waist propagates to
# almost everything downstream while a disruption at the edges does not.
#
# If you flatten these into equal tiers, the network stops being a supply chain
# and starts being a generic layered graph -- and the vulnerability analysis
# stops finding anything interesting.
TIER_SIZES: dict[Tier, int] = {
    Tier.RAW_MATERIAL: 520,
    Tier.REFINED_MATERIAL: 380,
    Tier.EQUIPMENT: 140,
    Tier.FAB: 95,
    Tier.OSAT: 170,
    Tier.EMS: 520,
    Tier.OEM: 820,
    Tier.LOGISTICS: 45,
}

# ---------------------------------------------------------------------------
# Geography
# ---------------------------------------------------------------------------

# Where each tier's capacity actually sits. Read down a column and you can see
# the industry's real structure: materials are a Japanese story, equipment is a
# US/Japan/Netherlands story, fabrication is overwhelmingly a Taiwanese story,
# and final assembly is a Chinese story.
REGION_WEIGHTS: dict[Tier, dict[R, float]] = {
    Tier.RAW_MATERIAL: {
        R.CHINA: 0.30, R.ROW: 0.25, R.NORTH_AMERICA: 0.12, R.SOUTH_ASIA: 0.08,
        R.EUROPE: 0.08, R.SEA: 0.07, R.JAPAN: 0.04, R.KOREA: 0.03, R.TAIWAN: 0.03,
    },
    Tier.REFINED_MATERIAL: {
        R.JAPAN: 0.30, R.CHINA: 0.15, R.TAIWAN: 0.12, R.KOREA: 0.12,
        R.EUROPE: 0.10, R.NORTH_AMERICA: 0.10, R.SEA: 0.07,
        R.ROW: 0.02, R.SOUTH_ASIA: 0.02,
    },
    Tier.EQUIPMENT: {
        R.NORTH_AMERICA: 0.32, R.JAPAN: 0.26, R.EUROPE: 0.24, R.KOREA: 0.06,
        R.CHINA: 0.06, R.TAIWAN: 0.04, R.SEA: 0.02,
    },
    Tier.FAB: {
        R.TAIWAN: 0.42, R.KOREA: 0.16, R.CHINA: 0.14, R.NORTH_AMERICA: 0.12,
        R.JAPAN: 0.08, R.EUROPE: 0.06, R.SEA: 0.02,
    },
    Tier.OSAT: {
        R.TAIWAN: 0.35, R.CHINA: 0.25, R.SEA: 0.20, R.KOREA: 0.07,
        R.NORTH_AMERICA: 0.05, R.JAPAN: 0.05, R.EUROPE: 0.03,
    },
    Tier.EMS: {
        R.CHINA: 0.40, R.SEA: 0.20, R.NORTH_AMERICA: 0.12, R.TAIWAN: 0.10,
        R.EUROPE: 0.08, R.SOUTH_ASIA: 0.06, R.KOREA: 0.02, R.JAPAN: 0.02,
    },
    Tier.OEM: {
        R.NORTH_AMERICA: 0.28, R.CHINA: 0.20, R.EUROPE: 0.18, R.JAPAN: 0.10,
        R.KOREA: 0.10, R.TAIWAN: 0.06, R.SEA: 0.04, R.SOUTH_ASIA: 0.04,
    },
    Tier.LOGISTICS: {
        R.CHINA: 0.20, R.SEA: 0.19, R.EUROPE: 0.155, R.NORTH_AMERICA: 0.155,
        R.TAIWAN: 0.09, R.KOREA: 0.07, R.JAPAN: 0.06, R.SOUTH_ASIA: 0.04,
        R.ROW: 0.04,
    },
}

# Firms prefer to buy close to home -- shorter lead times, easier logistics,
# shared language and timezone, and in this industry, dense regional clusters
# of engineering talent. This is the second force (alongside preferential
# attachment) that concentrates the network.
EAST_ASIA_BLOC = frozenset({R.TAIWAN, R.CHINA, R.JAPAN, R.KOREA, R.SEA})
WESTERN_BLOC = frozenset({R.NORTH_AMERICA, R.EUROPE})
BLOCS: tuple[frozenset[R], ...] = (EAST_ASIA_BLOC, WESTERN_BLOC)

SAME_REGION_BONUS = 3.2
SAME_BLOC_BONUS = 1.7

# ---------------------------------------------------------------------------
# What each tier produces
# ---------------------------------------------------------------------------

TIER_CATEGORIES: dict[Tier, tuple[C, ...]] = {
    Tier.RAW_MATERIAL: (
        C.SILICON_FEEDSTOCK, C.RARE_EARTH, C.COPPER, C.GOLD,
        C.CRUDE_GAS, C.BULK_CHEMICAL, C.RESIN,
    ),
    Tier.REFINED_MATERIAL: (
        C.WAFER, C.PHOTORESIST, C.PROCESS_GAS, C.CMP_SLURRY,
        C.PACKAGING_SUBSTRATE, C.BONDING_WIRE, C.PCB_LAMINATE,
    ),
    Tier.EQUIPMENT: (
        C.LITHOGRAPHY, C.ETCH, C.DEPOSITION,
        C.METROLOGY, C.ION_IMPLANT, C.TEST_HANDLER,
    ),
    Tier.FAB: (
        C.LOGIC_DIE, C.MEMORY_DIE, C.ANALOG_DIE,
        C.POWER_DIE, C.RF_DIE, C.MEMS_DIE,
    ),
    Tier.OSAT: (
        C.LOGIC_IC, C.MEMORY_IC, C.ANALOG_IC,
        C.POWER_IC, C.RF_IC, C.MEMS_SENSOR,
    ),
    Tier.EMS: (C.PCB_ASSEMBLY, C.POWER_MODULE, C.SENSOR_MODULE),
    Tier.OEM: (
        C.SMARTPHONE, C.AUTOMOTIVE_ECU, C.SERVER, C.LAPTOP,
        C.MEDICAL_DEVICE, C.INDUSTRIAL_CONTROLLER, C.NETWORK_EQUIPMENT,
    ),
    Tier.LOGISTICS: (C.FREIGHT,),
}

# How common each product line is within its tier. Not every category is
# equally populated -- there are far more bulk chemical suppliers than gold
# refiners, and far more analog fabs than leading-edge logic fabs.
CATEGORY_SHARE: dict[C, float] = {
    # raw
    C.BULK_CHEMICAL: 0.26, C.COPPER: 0.18, C.RESIN: 0.15, C.CRUDE_GAS: 0.14,
    C.SILICON_FEEDSTOCK: 0.12, C.RARE_EARTH: 0.09, C.GOLD: 0.06,
    # refined
    C.PCB_LAMINATE: 0.22, C.PACKAGING_SUBSTRATE: 0.18, C.PROCESS_GAS: 0.17,
    C.WAFER: 0.15, C.BONDING_WIRE: 0.12, C.PHOTORESIST: 0.09, C.CMP_SLURRY: 0.07,
    # equipment
    C.TEST_HANDLER: 0.26, C.METROLOGY: 0.21, C.DEPOSITION: 0.19,
    C.ETCH: 0.18, C.ION_IMPLANT: 0.10, C.LITHOGRAPHY: 0.06,
    # fab
    C.ANALOG_DIE: 0.28, C.POWER_DIE: 0.21, C.LOGIC_DIE: 0.18,
    C.MEMORY_DIE: 0.14, C.RF_DIE: 0.11, C.MEMS_DIE: 0.08,
    # osat
    C.ANALOG_IC: 0.27, C.POWER_IC: 0.21, C.LOGIC_IC: 0.19,
    C.MEMORY_IC: 0.14, C.RF_IC: 0.11, C.MEMS_SENSOR: 0.08,
    # ems
    C.PCB_ASSEMBLY: 0.62, C.POWER_MODULE: 0.22, C.SENSOR_MODULE: 0.16,
    # oem
    C.INDUSTRIAL_CONTROLLER: 0.20, C.AUTOMOTIVE_ECU: 0.18, C.SMARTPHONE: 0.16,
    C.NETWORK_EQUIPMENT: 0.14, C.LAPTOP: 0.13, C.MEDICAL_DEVICE: 0.10,
    C.SERVER: 0.09,
    # logistics
    C.FREIGHT: 1.0,
}

# ---------------------------------------------------------------------------
# Bill of materials -- the actual wiring rule
# ---------------------------------------------------------------------------

# What each product needs to be made, and how often. `(input, probability)`
# means a node producing this category requires that input with that
# probability -- so not every fab runs ion implantation, and not every
# smartphone maker sources a discrete power module.
#
# Driving edge creation from a BOM rather than from tier-level rules is what
# makes the single-sourcing analysis meaningful. A fab having eight suppliers
# tells you nothing if all eight sell different inputs and the photoresist has
# exactly one qualified source. Redundancy is a per-input property, so the
# generator has to think in inputs.
BOM: dict[C, tuple[tuple[C, float], ...]] = {
    # --- refined materials from raw ---
    C.WAFER: ((C.SILICON_FEEDSTOCK, 1.0), (C.BULK_CHEMICAL, 0.45)),
    C.PHOTORESIST: ((C.BULK_CHEMICAL, 1.0), (C.RARE_EARTH, 0.30)),
    C.PROCESS_GAS: ((C.CRUDE_GAS, 1.0), (C.BULK_CHEMICAL, 0.35)),
    C.CMP_SLURRY: ((C.BULK_CHEMICAL, 1.0), (C.RARE_EARTH, 0.60)),
    C.PACKAGING_SUBSTRATE: ((C.RESIN, 1.0), (C.COPPER, 1.0)),
    C.BONDING_WIRE: ((C.GOLD, 1.0), (C.COPPER, 0.70)),
    C.PCB_LAMINATE: ((C.RESIN, 1.0), (C.COPPER, 1.0)),

    # --- equipment from raw ---
    C.LITHOGRAPHY: ((C.RARE_EARTH, 0.80), (C.COPPER, 0.60), (C.BULK_CHEMICAL, 0.40)),
    C.ETCH: ((C.BULK_CHEMICAL, 0.70), (C.COPPER, 0.50)),
    C.DEPOSITION: ((C.RARE_EARTH, 0.50), (C.COPPER, 0.60)),
    C.METROLOGY: ((C.RARE_EARTH, 0.60), (C.COPPER, 0.40)),
    C.ION_IMPLANT: ((C.RARE_EARTH, 0.50), (C.BULK_CHEMICAL, 0.50)),
    C.TEST_HANDLER: ((C.COPPER, 0.60), (C.BULK_CHEMICAL, 0.30)),

    # --- fab output: leading-edge logic and memory need the full toolset,
    #     mature analog/power/RF/MEMS nodes need noticeably less ---
    C.LOGIC_DIE: (
        (C.WAFER, 1.0), (C.PHOTORESIST, 1.0), (C.PROCESS_GAS, 1.0),
        (C.CMP_SLURRY, 0.90), (C.LITHOGRAPHY, 1.0), (C.ETCH, 0.95),
        (C.DEPOSITION, 0.95), (C.METROLOGY, 0.85), (C.ION_IMPLANT, 0.75),
    ),
    C.MEMORY_DIE: (
        (C.WAFER, 1.0), (C.PHOTORESIST, 1.0), (C.PROCESS_GAS, 1.0),
        (C.CMP_SLURRY, 0.85), (C.LITHOGRAPHY, 0.95), (C.ETCH, 0.90),
        (C.DEPOSITION, 0.95), (C.METROLOGY, 0.75), (C.ION_IMPLANT, 0.70),
    ),
    C.ANALOG_DIE: (
        (C.WAFER, 1.0), (C.PHOTORESIST, 0.95), (C.PROCESS_GAS, 1.0),
        (C.CMP_SLURRY, 0.45), (C.LITHOGRAPHY, 0.70), (C.ETCH, 0.75),
        (C.DEPOSITION, 0.70), (C.METROLOGY, 0.50), (C.ION_IMPLANT, 0.55),
    ),
    C.POWER_DIE: (
        (C.WAFER, 1.0), (C.PHOTORESIST, 0.90), (C.PROCESS_GAS, 1.0),
        (C.CMP_SLURRY, 0.40), (C.LITHOGRAPHY, 0.65), (C.ETCH, 0.70),
        (C.DEPOSITION, 0.75), (C.METROLOGY, 0.45), (C.ION_IMPLANT, 0.60),
    ),
    C.RF_DIE: (
        (C.WAFER, 1.0), (C.PHOTORESIST, 0.95), (C.PROCESS_GAS, 1.0),
        (C.CMP_SLURRY, 0.55), (C.LITHOGRAPHY, 0.80), (C.ETCH, 0.80),
        (C.DEPOSITION, 0.80), (C.METROLOGY, 0.60), (C.ION_IMPLANT, 0.50),
    ),
    C.MEMS_DIE: (
        (C.WAFER, 1.0), (C.PHOTORESIST, 0.85), (C.PROCESS_GAS, 0.95),
        (C.CMP_SLURRY, 0.35), (C.LITHOGRAPHY, 0.60), (C.ETCH, 0.90),
        (C.DEPOSITION, 0.70), (C.METROLOGY, 0.55), (C.ION_IMPLANT, 0.30),
    ),

    # --- packaged parts: die + substrate + wire + test ---
    #
    # PROCESS_GAS and METROLOGY appear here as well as in the fab recipes, and
    # that overlap is deliberate. Assembly and test floors run on nitrogen and
    # forming gas and buy their own inspection tools, so the same supplier can
    # sell to a fab *and* to the packaging house that fab ships to.
    #
    # It also fixes a real defect. Without a shared input spanning two
    # connected tiers, the network had exactly zero triangles -- clustering
    # 0.0000, which no real production network exhibits. Every category was
    # consumed by exactly one tier, so no supplier could ever be a neighbour of
    # two firms that were themselves trading. The validation caught it; the
    # fix is to model an overlap that genuinely exists.
    C.LOGIC_IC: (
        (C.LOGIC_DIE, 1.0), (C.PACKAGING_SUBSTRATE, 1.0),
        (C.BONDING_WIRE, 0.75), (C.TEST_HANDLER, 0.90),
        (C.PROCESS_GAS, 0.55), (C.METROLOGY, 0.35),
    ),
    C.MEMORY_IC: (
        (C.MEMORY_DIE, 1.0), (C.PACKAGING_SUBSTRATE, 1.0),
        (C.BONDING_WIRE, 0.80), (C.TEST_HANDLER, 0.85),
        (C.PROCESS_GAS, 0.50), (C.METROLOGY, 0.30),
    ),
    C.ANALOG_IC: (
        (C.ANALOG_DIE, 1.0), (C.PACKAGING_SUBSTRATE, 0.95),
        (C.BONDING_WIRE, 0.85), (C.TEST_HANDLER, 0.80),
        (C.PROCESS_GAS, 0.45), (C.METROLOGY, 0.25),
    ),
    C.POWER_IC: (
        (C.POWER_DIE, 1.0), (C.PACKAGING_SUBSTRATE, 0.90),
        (C.BONDING_WIRE, 0.90), (C.TEST_HANDLER, 0.80),
        (C.PROCESS_GAS, 0.50), (C.METROLOGY, 0.25),
    ),
    C.RF_IC: (
        (C.RF_DIE, 1.0), (C.PACKAGING_SUBSTRATE, 1.0),
        (C.BONDING_WIRE, 0.70), (C.TEST_HANDLER, 0.85),
        (C.PROCESS_GAS, 0.50), (C.METROLOGY, 0.35),
    ),
    C.MEMS_SENSOR: (
        (C.MEMS_DIE, 1.0), (C.PACKAGING_SUBSTRATE, 0.95),
        (C.BONDING_WIRE, 0.75), (C.TEST_HANDLER, 0.80),
        (C.PROCESS_GAS, 0.45), (C.METROLOGY, 0.30),
    ),

    # --- board assembly ---
    # Board houses use far less process gas than a fab or a packaging line,
    # but not none -- reflow and selective soldering run under nitrogen.
    C.PCB_ASSEMBLY: (
        (C.PCB_LAMINATE, 1.0), (C.LOGIC_IC, 0.90), (C.MEMORY_IC, 0.80),
        (C.ANALOG_IC, 0.75), (C.POWER_IC, 0.60), (C.RF_IC, 0.45),
        (C.PROCESS_GAS, 0.22),
    ),
    C.POWER_MODULE: (
        (C.PCB_LAMINATE, 1.0), (C.POWER_IC, 1.0), (C.ANALOG_IC, 0.55),
        (C.PROCESS_GAS, 0.18),
    ),
    C.SENSOR_MODULE: (
        (C.PCB_LAMINATE, 1.0), (C.MEMS_SENSOR, 1.0), (C.ANALOG_IC, 0.60),
        (C.PROCESS_GAS, 0.18),
    ),

    # --- finished goods ---
    C.SMARTPHONE: ((C.PCB_ASSEMBLY, 1.0), (C.SENSOR_MODULE, 0.90), (C.POWER_MODULE, 0.70)),
    C.AUTOMOTIVE_ECU: ((C.PCB_ASSEMBLY, 1.0), (C.POWER_MODULE, 0.90), (C.SENSOR_MODULE, 0.80)),
    C.SERVER: ((C.PCB_ASSEMBLY, 1.0), (C.POWER_MODULE, 0.80)),
    C.LAPTOP: ((C.PCB_ASSEMBLY, 1.0), (C.POWER_MODULE, 0.70), (C.SENSOR_MODULE, 0.40)),
    C.MEDICAL_DEVICE: ((C.PCB_ASSEMBLY, 1.0), (C.SENSOR_MODULE, 0.85), (C.POWER_MODULE, 0.50)),
    C.INDUSTRIAL_CONTROLLER: ((C.PCB_ASSEMBLY, 1.0), (C.POWER_MODULE, 0.70), (C.SENSOR_MODULE, 0.60)),
    C.NETWORK_EQUIPMENT: ((C.PCB_ASSEMBLY, 1.0), (C.POWER_MODULE, 0.60)),
}

# ---------------------------------------------------------------------------
# Sourcing behaviour
# ---------------------------------------------------------------------------

# Probability that a buyer sources a given input from exactly one supplier.
#
# This is driven by *qualification difficulty*, which is a real and well
# documented property of each input. Requalifying a photoresist or a tool
# vendor means months of process re-tuning and customer re-approval, so buyers
# live with single sources they would never accept for copper wire.
#
# Note carefully what this does and does not encode. Setting lithography high
# says "advanced tools are hard to second-source" -- an industry fact. It does
# not say which lithography supplier ends up carrying the network. That falls
# out of preferential attachment and geography, and we genuinely do not know
# which node it will be until we run it.
SINGLE_SOURCE_PROB: dict[C, float] = {
    C.LITHOGRAPHY: 0.62, C.PHOTORESIST: 0.38, C.ION_IMPLANT: 0.35,
    C.RARE_EARTH: 0.35, C.CMP_SLURRY: 0.32, C.METROLOGY: 0.30,
    C.WAFER: 0.30, C.PACKAGING_SUBSTRATE: 0.28, C.ETCH: 0.25,
    C.DEPOSITION: 0.25, C.SILICON_FEEDSTOCK: 0.25, C.TEST_HANDLER: 0.22,
    C.PROCESS_GAS: 0.22, C.BONDING_WIRE: 0.20, C.CRUDE_GAS: 0.15,
    C.RESIN: 0.12, C.GOLD: 0.10, C.BULK_CHEMICAL: 0.10, C.COPPER: 0.08,
    C.PCB_LAMINATE: 0.14,
}
DEFAULT_SINGLE_SOURCE_PROB = 0.20   # dies, ICs, modules, assemblies

# When not single-sourced: 2 + Poisson(lambda), capped. Real BOM lines rarely
# have more than a handful of qualified vendors.
MULTI_SOURCE_LAMBDA = 0.8
MAX_SUPPLIERS_PER_INPUT = 5

# Preferential attachment: P(pick supplier) is proportional to
#
#     (customers_so_far + ALPHA) ** GAMMA  *  capacity ** CAPACITY_EXP  *  geo
#
# ALPHA keeps the process from freezing. With a pure degree term a node with
# zero customers has zero probability of ever getting one, so the first firm
# picked would win forever. A positive floor lets newcomers in.
#
# GAMMA below 1 makes attachment *sublinear*, and this is the important one.
# Plain Barabasi-Albert uses linear attachment (gamma = 1), which in a network
# this size lets a single firm run away with most of the market -- an early run
# produced one contract manufacturer holding 77% of all OEM relationships,
# which is not a supply chain, it is a monopoly.
#
# Sublinear attachment is also the more defensible model. Real suppliers have
# finite plants: winning customers gets harder as you fill capacity. Empirical
# studies of production networks consistently find power-law degree with an
# exponential cutoff rather than a pure power law, and sublinear attachment is
# exactly what generates that shape.
ATTACH_ALPHA = 1.5
ATTACH_GAMMA = 0.85
CAPACITY_EXP = 0.70

# Firm scale, lognormal. Sizes in these tiers genuinely span orders of
# magnitude, but sigma has to stay moderate: combined with the attachment
# weight it decides how many small firms end up with no customers at all.
CAPACITY_LOG_MEAN = 0.0
CAPACITY_LOG_SIGMA = 0.85

# ---------------------------------------------------------------------------
# Logistics
# ---------------------------------------------------------------------------

# Share of logistics nodes that are seaports rather than air hubs.
SEAPORT_SHARE = 0.65

# A hub serves its home region plus 0-2 neighbours -- but only *plausible*
# neighbours. An early run let hubs pick extra regions at random and produced a
# Houston seaport acting as the busiest export gateway for Taiwanese fabs,
# which is nonsense. Freight consolidates through geographically adjacent
# gateways, so the neighbour set has to respect the map.
HUB_EXTRA_REGIONS = (0, 1, 2)
HUB_EXTRA_REGION_P = (0.45, 0.38, 0.17)

REGION_NEIGHBOURS: dict[R, tuple[R, ...]] = {
    R.TAIWAN: (R.CHINA, R.JAPAN, R.KOREA, R.SEA),
    R.CHINA: (R.TAIWAN, R.KOREA, R.JAPAN, R.SEA),
    R.JAPAN: (R.KOREA, R.TAIWAN, R.CHINA),
    R.KOREA: (R.JAPAN, R.CHINA, R.TAIWAN),
    R.SEA: (R.CHINA, R.TAIWAN, R.SOUTH_ASIA),
    R.NORTH_AMERICA: (R.EUROPE,),
    R.EUROPE: (R.NORTH_AMERICA, R.SOUTH_ASIA),
    R.SOUTH_ASIA: (R.SEA, R.EUROPE),
    R.ROW: (R.EUROPE, R.NORTH_AMERICA, R.SEA),
}

# Probability a cross-region shipment is routed through a modeled hub rather
# than moving direct.
#
# Deliberately not 1.0. If every international edge were forced through a port,
# ports would trivially dominate every centrality measure and the analysis
# would be a tautology -- we would have built the answer into the routing rule.
# Leaving ~28% of cross-border flow on direct air freight means hubs have to
# earn their centrality, and removing one degrades the network rather than
# severing it. Degradation is the more interesting thing to measure anyway.
CROSS_REGION_ROUTE_PROB = 0.72

# ---------------------------------------------------------------------------
# Edge economics
# ---------------------------------------------------------------------------

# Median unit price in USD. Spread is applied lognormally at generation time.
UNIT_COST_USD: dict[C, float] = {
    C.SILICON_FEEDSTOCK: 22, C.RARE_EARTH: 85, C.COPPER: 9, C.GOLD: 240,
    C.CRUDE_GAS: 6, C.BULK_CHEMICAL: 11, C.RESIN: 14,
    C.WAFER: 95, C.PHOTORESIST: 320, C.PROCESS_GAS: 40, C.CMP_SLURRY: 180,
    C.PACKAGING_SUBSTRATE: 6, C.BONDING_WIRE: 12, C.PCB_LAMINATE: 15,
    C.LITHOGRAPHY: 62_000_000, C.ETCH: 4_500_000, C.DEPOSITION: 5_200_000,
    C.METROLOGY: 3_100_000, C.ION_IMPLANT: 4_000_000, C.TEST_HANDLER: 850_000,
    C.LOGIC_DIE: 42, C.MEMORY_DIE: 18, C.ANALOG_DIE: 6,
    C.POWER_DIE: 9, C.RF_DIE: 14, C.MEMS_DIE: 7,
    C.LOGIC_IC: 58, C.MEMORY_IC: 24, C.ANALOG_IC: 9,
    C.POWER_IC: 13, C.RF_IC: 19, C.MEMS_SENSOR: 11,
    C.PCB_ASSEMBLY: 140, C.POWER_MODULE: 65, C.SENSOR_MODULE: 38,
    C.SMARTPHONE: 420, C.AUTOMOTIVE_ECU: 310, C.SERVER: 4200, C.LAPTOP: 650,
    C.MEDICAL_DEVICE: 2800, C.INDUSTRIAL_CONTROLLER: 900,
    C.NETWORK_EQUIPMENT: 1600,
    C.FREIGHT: 1.0,
}

# Median annual units moved on one supply relationship. Capital equipment moves
# in single digits per year; packaged ICs move by the million.
VOLUME_MEDIAN: dict[C, float] = {
    C.SILICON_FEEDSTOCK: 90_000, C.RARE_EARTH: 12_000, C.COPPER: 260_000,
    C.GOLD: 3_500, C.CRUDE_GAS: 400_000, C.BULK_CHEMICAL: 320_000,
    C.RESIN: 180_000,
    C.WAFER: 240_000, C.PHOTORESIST: 26_000, C.PROCESS_GAS: 150_000,
    C.CMP_SLURRY: 34_000, C.PACKAGING_SUBSTRATE: 4_200_000,
    C.BONDING_WIRE: 1_800_000, C.PCB_LAMINATE: 2_600_000,
    C.LITHOGRAPHY: 3, C.ETCH: 14, C.DEPOSITION: 12,
    C.METROLOGY: 18, C.ION_IMPLANT: 9, C.TEST_HANDLER: 40,
    C.LOGIC_DIE: 5_800_000, C.MEMORY_DIE: 12_000_000, C.ANALOG_DIE: 22_000_000,
    C.POWER_DIE: 15_000_000, C.RF_DIE: 9_000_000, C.MEMS_DIE: 7_000_000,
    C.LOGIC_IC: 5_200_000, C.MEMORY_IC: 10_500_000, C.ANALOG_IC: 19_000_000,
    C.POWER_IC: 13_000_000, C.RF_IC: 8_000_000, C.MEMS_SENSOR: 6_200_000,
    C.PCB_ASSEMBLY: 1_400_000, C.POWER_MODULE: 900_000, C.SENSOR_MODULE: 750_000,
    C.SMARTPHONE: 2_200_000, C.AUTOMOTIVE_ECU: 1_100_000, C.SERVER: 85_000,
    C.LAPTOP: 900_000, C.MEDICAL_DEVICE: 45_000,
    C.INDUSTRIAL_CONTROLLER: 300_000, C.NETWORK_EQUIPMENT: 160_000,
}
VOLUME_LOG_SIGMA = 0.85

# Manufacturing/qualification lead time in days, by the tier pair the edge
# spans. The equipment window is the outlier and it is real: a new lithography
# or deposition tool is ordered 12-18 months before it produces anything, which
# is exactly why equipment shortages take years rather than months to clear.
LEAD_TIME_DAYS: dict[tuple[Tier, Tier], tuple[int, int]] = {
    (Tier.RAW_MATERIAL, Tier.REFINED_MATERIAL): (30, 60),
    (Tier.RAW_MATERIAL, Tier.EQUIPMENT): (45, 90),
    (Tier.REFINED_MATERIAL, Tier.FAB): (45, 90),
    (Tier.REFINED_MATERIAL, Tier.OSAT): (35, 70),
    (Tier.REFINED_MATERIAL, Tier.EMS): (30, 65),
    (Tier.EQUIPMENT, Tier.FAB): (270, 540),
    (Tier.EQUIPMENT, Tier.OSAT): (180, 360),
    (Tier.FAB, Tier.OSAT): (70, 110),
    (Tier.OSAT, Tier.EMS): (20, 40),
    (Tier.EMS, Tier.OEM): (15, 35),
}
DEFAULT_LEAD_TIME = (30, 60)

# Extra days added when goods physically cross a hub.
SEA_TRANSIT_DAYS = (18, 45)
AIR_TRANSIT_DAYS = (3, 8)

# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

# Firms get neutral, procedurally generated names. Using real company names
# would imply the numbers attached to them are real, which they are not -- and
# a synthetic graph labelled with real firms is a claim you cannot defend.
NAME_STEMS = (
    "Ader", "Belan", "Cortex", "Dyne", "Ember", "Faryn", "Gethin", "Halcy",
    "Ivex", "Jorel", "Kestrel", "Lumen", "Mervi", "Noral", "Oxbow", "Prendel",
    "Quarn", "Rivet", "Solvex", "Trellis", "Ulvid", "Vantor", "Wexler",
    "Xanthe", "Ystad", "Zephyr", "Alder", "Brant", "Calder", "Doran",
    "Elstow", "Ferrin", "Granth", "Holt", "Isar", "Jarn", "Korrin", "Lethe",
    "Mordant", "Nevin", "Orvid", "Palter", "Quill", "Renbeck", "Stade",
    "Thorne", "Umber", "Verity", "Wend", "Yarrow",
)
TIER_SUFFIX: dict[Tier, tuple[str, ...]] = {
    Tier.RAW_MATERIAL: ("Minerals", "Resources", "Extraction", "Feedstock"),
    Tier.REFINED_MATERIAL: ("Materials", "Chemical", "Advanced Materials", "Specialty"),
    Tier.EQUIPMENT: ("Systems", "Instruments", "Technologies", "Precision"),
    Tier.FAB: ("Semiconductor", "Foundry", "Microelectronics", "Wafer Works"),
    Tier.OSAT: ("Assembly", "Packaging", "Test Services", "Microassembly"),
    Tier.EMS: ("Electronics", "Manufacturing", "Assemblies", "Circuits"),
    Tier.OEM: ("Devices", "Products", "Group", "Industries"),
}
LOGISTICS_SUFFIX: dict[LogisticsKind, str] = {
    LogisticsKind.SEAPORT: "Port",
    LogisticsKind.AIR_HUB: "Air Cargo Hub",
}

# Real facility locations, used only as plant labels. These make node names
# readable ("Kestrel Semiconductor - Hsinchu" reads as a fab; "Node_1841" does
# not) and they give the entity-resolution layer in a later session something
# realistic to disambiguate against.
CITIES: dict[R, tuple[str, ...]] = {
    R.TAIWAN: ("Hsinchu", "Taichung", "Tainan", "Kaohsiung", "Taoyuan"),
    R.CHINA: ("Shanghai", "Shenzhen", "Wuxi", "Chengdu", "Xi'an", "Tianjin", "Dalian"),
    R.JAPAN: ("Kumamoto", "Yokkaichi", "Hiroshima", "Sendai", "Nagano", "Kitakyushu"),
    R.KOREA: ("Icheon", "Hwaseong", "Cheongju", "Gumi", "Pyeongtaek"),
    R.SEA: ("Penang", "Singapore", "Batangas", "Bac Ninh", "Batam", "Bangkok"),
    R.NORTH_AMERICA: ("Chandler", "Austin", "Hillsboro", "Malta", "Ottawa", "Guadalajara"),
    R.EUROPE: ("Dresden", "Eindhoven", "Grenoble", "Leixlip", "Villach", "Crolles"),
    R.SOUTH_ASIA: ("Bengaluru", "Sanand", "Hyderabad", "Pune"),
    R.ROW: ("Perth", "Antofagasta", "Kolwezi", "Johannesburg", "Belo Horizonte"),
}

SEAPORT_CITIES: dict[R, tuple[str, ...]] = {
    R.TAIWAN: ("Kaohsiung", "Taichung", "Keelung"),
    R.CHINA: ("Shanghai", "Shenzhen", "Ningbo", "Qingdao", "Tianjin"),
    R.JAPAN: ("Yokohama", "Kobe", "Nagoya"),
    R.KOREA: ("Busan", "Incheon"),
    R.SEA: ("Singapore", "Port Klang", "Tanjung Pelepas", "Laem Chabang", "Manila", "Hai Phong"),
    R.NORTH_AMERICA: ("Los Angeles", "Long Beach", "Savannah", "Vancouver", "Houston"),
    R.EUROPE: ("Rotterdam", "Antwerp", "Hamburg", "Valencia", "Gdansk"),
    R.SOUTH_ASIA: ("Nhava Sheva", "Mundra", "Chennai"),
    R.ROW: ("Fremantle", "Santos", "Durban"),
}

AIR_HUB_CITIES: dict[R, tuple[str, ...]] = {
    R.TAIWAN: ("Taoyuan",),
    R.CHINA: ("Hong Kong", "Shanghai Pudong", "Guangzhou"),
    R.JAPAN: ("Narita", "Kansai"),
    R.KOREA: ("Incheon",),
    R.SEA: ("Changi", "Kuala Lumpur", "Clark"),
    R.NORTH_AMERICA: ("Memphis", "Louisville", "Anchorage", "Chicago", "Miami"),
    R.EUROPE: ("Frankfurt", "Amsterdam", "Leipzig", "Liege"),
    R.SOUTH_ASIA: ("Bengaluru", "Delhi"),
    R.ROW: ("Dubai", "Doha"),
}

# Probability a routed cross-region shipment goes by air rather than sea.
AIR_FREIGHT_PROB = 0.32

# ---------------------------------------------------------------------------
# Integrity checks
# ---------------------------------------------------------------------------

# Which tier produces each category. Derived, never hand-written, so it cannot
# drift out of sync with TIER_CATEGORIES.
CATEGORY_TIER: dict[C, Tier] = {
    cat: tier for tier, cats in TIER_CATEGORIES.items() for cat in cats
}


def validate_config() -> list[str]:
    """Check the rules are internally consistent before generating anything.

    Cheap insurance. A single typo in the BOM -- pointing an input at a
    category that no tier produces, or at one *downstream* of the buyer --
    would silently create a network with missing edges or an illegal cycle,
    and the resulting centrality numbers would look perfectly plausible while
    being meaningless. Better to fail loudly at import time.

    Returns a list of problems; empty means clean.
    """
    problems: list[str] = []
    flow = set(TIER_FLOW)

    for product, recipe in BOM.items():
        if product not in CATEGORY_TIER:
            problems.append(f"BOM defines {product!r} but no tier produces it")
            continue
        buyer_tier = CATEGORY_TIER[product]

        for input_cat, prob in recipe:
            if input_cat not in CATEGORY_TIER:
                problems.append(f"{product!r} requires {input_cat!r}, which nothing produces")
                continue
            supplier_tier = CATEGORY_TIER[input_cat]
            if supplier_tier >= buyer_tier:
                problems.append(
                    f"{product!r} (tier {buyer_tier.name}) requires {input_cat!r} "
                    f"from tier {supplier_tier.name} -- not strictly upstream"
                )
            elif (supplier_tier, buyer_tier) not in flow:
                problems.append(
                    f"{supplier_tier.name} -> {buyer_tier.name} is used by "
                    f"{product!r} but is missing from TIER_FLOW"
                )
            if not 0.0 < prob <= 1.0:
                problems.append(f"{product!r} <- {input_cat!r} has probability {prob}")

    # Every producible category needs a share, a price and a volume.
    for cat, tier in CATEGORY_TIER.items():
        if tier is Tier.LOGISTICS:
            continue
        if cat not in CATEGORY_SHARE:
            problems.append(f"{cat!r} has no CATEGORY_SHARE")
        if cat not in UNIT_COST_USD:
            problems.append(f"{cat!r} has no UNIT_COST_USD")
        if cat not in VOLUME_MEDIAN:
            problems.append(f"{cat!r} has no VOLUME_MEDIAN")

    # Regional weights should be a distribution over regions.
    for tier, weights in REGION_WEIGHTS.items():
        total = sum(weights.values())
        if abs(total - 1.0) > 0.01:
            problems.append(f"REGION_WEIGHTS[{tier.name}] sums to {total:.3f}, not 1.0")

    # Category shares should sum to 1 within each tier.
    for tier, cats in TIER_CATEGORIES.items():
        if tier is Tier.LOGISTICS:
            continue
        total = sum(CATEGORY_SHARE.get(c, 0.0) for c in cats)
        if abs(total - 1.0) > 0.01:
            problems.append(f"CATEGORY_SHARE for {tier.name} sums to {total:.3f}, not 1.0")

    if abs(sum(HUB_EXTRA_REGION_P) - 1.0) > 1e-9:
        problems.append("HUB_EXTRA_REGION_P does not sum to 1.0")

    return problems
