"""
The static shape of the invented business: departments, categories, brands,
suppliers and the fulfillment nodes stock sits in.

Kept apart from :mod:`seed.dataset` because none of it is random. The numbers
that matter for planning - seasonal indices, node demand shares, supplier
reliability - are declared here so they can be read, argued with and tested
rather than buried inside a generator loop.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Fulfillment network
# --------------------------------------------------------------------------
# Seven nodes across Canada. `DemandShare` is the share of national demand the
# node fulfils, which follows population rather than floor space - a small node
# in a dense region ships more than a large one in an empty one, and that
# mismatch is exactly what the allocation report exists to find.
NODES: tuple[dict, ...] = (
    {"NodeID": "YVR2", "NodeName": "Delta BC", "Region": "West", "City": "Delta",
     "NodeType": "Fulfillment Center", "StorageCapacityCubeFt": 1_950_000, "DemandShare": 0.155},
    {"NodeID": "YYC1", "NodeName": "Balzac AB", "Region": "Prairies", "City": "Calgary",
     "NodeType": "Fulfillment Center", "StorageCapacityCubeFt": 1_420_000, "DemandShare": 0.115},
    {"NodeID": "YEG3", "NodeName": "Nisku AB", "Region": "Prairies", "City": "Edmonton",
     "NodeType": "Fulfillment Center", "StorageCapacityCubeFt": 980_000, "DemandShare": 0.085},
    {"NodeID": "YWG2", "NodeName": "Winnipeg MB", "Region": "Prairies", "City": "Winnipeg",
     "NodeType": "Fulfillment Center", "StorageCapacityCubeFt": 720_000, "DemandShare": 0.055},
    {"NodeID": "YYZ4", "NodeName": "Brampton ON", "Region": "Central", "City": "Brampton",
     "NodeType": "Fulfillment Center", "StorageCapacityCubeFt": 2_600_000, "DemandShare": 0.315},
    {"NodeID": "YUL5", "NodeName": "Coteau-du-Lac QC", "Region": "East", "City": "Montreal",
     "NodeType": "Fulfillment Center", "StorageCapacityCubeFt": 1_640_000, "DemandShare": 0.195},
    {"NodeID": "YHZ1", "NodeName": "Dartmouth NS", "Region": "East", "City": "Halifax",
     "NodeType": "Fulfillment Center", "StorageCapacityCubeFt": 430_000, "DemandShare": 0.080},
)

# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------
# The monthly indices average to 1.0 within a department, so they redistribute
# a year rather than inflate it. They are the reason the forecast page has
# anything to do: a flat series is forecast correctly by any method, and a
# planner who cannot tell December from July orders the same in both.
DEPARTMENTS: tuple[dict, ...] = (
    {
        "Department": "Electronics",
        "Share": 0.13,
        "Seasonality": (0.82, 0.78, 0.85, 0.86, 0.90, 0.92, 1.02, 0.95, 0.96, 1.05, 1.62, 1.27),
        "Categories": ("Headphones", "Streaming Devices", "Smart Home", "Tablets",
                       "Cables & Adapters", "Portable Power"),
        "MarginPct": 0.19,
    },
    {
        "Department": "Home & Kitchen",
        "Share": 0.15,
        "Seasonality": (1.05, 0.94, 1.00, 1.02, 1.04, 0.96, 0.92, 0.93, 0.98, 1.03, 1.16, 0.97),
        "Categories": ("Cookware", "Small Appliances", "Storage & Organisation",
                       "Bedding", "Cleaning Supplies", "Tableware"),
        "MarginPct": 0.31,
    },
    {
        "Department": "Grocery",
        "Share": 0.14,
        "Seasonality": (0.99, 0.96, 1.00, 1.00, 1.01, 1.00, 1.01, 1.00, 0.99, 1.00, 1.02, 1.02),
        "Categories": ("Coffee & Tea", "Snacks", "Pantry Staples", "Beverages",
                       "Breakfast", "Baking", "Dairy & Chilled", "Frozen Meals"),
        "MarginPct": 0.22,
    },
    {
        "Department": "Apparel",
        "Share": 0.11,
        "Seasonality": (0.86, 0.88, 1.09, 1.14, 1.03, 0.94, 0.88, 0.97, 1.12, 1.09, 1.03, 0.97),
        "Categories": ("Outerwear", "Footwear", "Basics", "Activewear", "Accessories"),
        "MarginPct": 0.44,
    },
    {
        "Department": "Toys & Games",
        "Share": 0.09,
        "Seasonality": (0.68, 0.64, 0.70, 0.74, 0.78, 0.86, 0.92, 0.88, 0.86, 1.06, 2.05, 1.83),
        "Categories": ("Building Sets", "Board Games", "Outdoor Play", "Puzzles", "Figures"),
        "MarginPct": 0.35,
    },
    {
        "Department": "Health & Beauty",
        "Share": 0.11,
        "Seasonality": (1.22, 1.06, 1.01, 0.98, 0.97, 0.95, 0.93, 0.94, 0.97, 0.99, 0.99, 0.99),
        "Categories": ("Skincare", "Vitamins", "Oral Care", "Hair Care", "Personal Devices"),
        "MarginPct": 0.41,
    },
    {
        "Department": "Sports & Outdoors",
        "Share": 0.08,
        "Seasonality": (0.72, 0.76, 0.92, 1.09, 1.24, 1.32, 1.30, 1.18, 1.00, 0.86, 0.82, 0.79),
        "Categories": ("Camping", "Cycling", "Fitness", "Water Sports", "Team Sports"),
        "MarginPct": 0.33,
    },
    {
        "Department": "Office Products",
        "Share": 0.07,
        "Seasonality": (1.04, 0.95, 0.93, 0.90, 0.88, 0.86, 1.06, 1.42, 1.26, 0.95, 0.90, 0.85),
        "Categories": ("Paper", "Writing", "Desk Accessories", "Printers & Ink", "Filing"),
        "MarginPct": 0.28,
    },
    {
        "Department": "Pet Supplies",
        "Share": 0.07,
        "Seasonality": (1.00, 0.98, 1.00, 1.01, 1.02, 1.01, 1.00, 0.99, 0.99, 1.00, 1.00, 1.00),
        "Categories": ("Dog Food", "Cat Food", "Toys & Treats", "Grooming", "Habitats"),
        "MarginPct": 0.26,
    },
    {
        "Department": "Baby",
        "Share": 0.05,
        "Seasonality": (1.02, 1.00, 1.01, 1.00, 0.99, 0.99, 0.99, 1.00, 1.00, 1.00, 1.01, 0.99),
        "Categories": ("Diapering", "Feeding", "Nursery", "Travel Gear"),
        "MarginPct": 0.24,
    },
)

BRANDS: tuple[str, ...] = (
    "Northvale", "Kestrel", "Bellwood", "Harbourline", "Aurelia", "Ironpine",
    "Larkspur", "Meridian Bay", "Coppermill", "Solstice Row", "Fenwick",
    "Tidewater", "Stonebrook", "Calder and Vine", "Ashgrove", "Rivermont",
)

# Descriptor fragments, so 400-odd item titles read like a catalogue rather
# than like SKU-0001 through SKU-0420.
MODIFIERS: tuple[str, ...] = (
    "Compact", "Pro", "Everyday", "Heritage", "Lightweight", "Insulated",
    "Stackable", "Cordless", "Premium", "Essential", "Traveller", "Studio",
    "Heavy Duty", "Slimline", "Value", "Deluxe",
)
PACK_DESCRIPTORS: tuple[str, ...] = (
    "Single", "2-Pack", "4-Pack", "6-Pack", "12-Pack", "Bulk Case",
)

# Categories that live outside ambient. Keyed on category rather than
# department on purpose: "Health & Beauty" is a plausible cold department and
# frozen toothpaste is not, and a detail like that is what makes a reader stop
# trusting the rest of the numbers.
COLD_CATEGORIES: dict[str, str] = {
    "Dairy & Chilled": "Chilled",
    "Frozen Meals": "Frozen",
}

# --------------------------------------------------------------------------
# Suppliers
# --------------------------------------------------------------------------
# `LeadTimeDays` is the *contracted* lead time. What actually happens is drawn
# per purchase order around `ActualLeadTimeMean`, and the gap between the two is
# the whole point of the supplier scorecard. The two deliberately disagree in
# both directions: a supplier who quotes 42 days and delivers in 47 forces a
# buffer nobody planned, and one who quotes 42 and delivers in 34 is carrying a
# week of stock that could have been cash. Making every supplier beat their
# contract - the easy way to generate this table - grades all twenty-two of
# them an A and leaves the scorecard with nothing to say.
SUPPLIER_FIELDS = (
    "SupplierID", "SupplierName", "Country", "SupplierRegion", "LeadTimeDays",
    "ActualLeadTimeMean", "OrderCostUSD", "OnTimeReliability", "DefectRate", "PaymentTerms",
)

SUPPLIERS: tuple[tuple, ...] = (
    ("SUP-101", "Kestrel Import Group", "China", "APAC", 42, 47.0, 780.0, 0.86, 0.017, "Net 60"),
    ("SUP-102", "Pacific Rim Sourcing", "Vietnam", "APAC", 46, 55.0, 940.0, 0.79, 0.024, "Net 60"),
    ("SUP-103", "Harbourline Distribution", "Canada", "North America", 12, 11.0, 240.0, 0.94, 0.006, "Net 30"),
    ("SUP-104", "Great Lakes Consumer", "Canada", "North America", 10, 9.5, 210.0, 0.96, 0.004, "Net 30"),
    ("SUP-105", "Cascadia Goods Co", "Canada", "North America", 9, 8.0, 185.0, 0.97, 0.003, "Net 30"),
    ("SUP-106", "Monterrey Manufacturing", "Mexico", "North America", 24, 27.0, 460.0, 0.88, 0.012, "Net 45"),
    ("SUP-107", "Rhineland Werke", "Germany", "EMEA", 34, 32.0, 690.0, 0.93, 0.005, "Net 45"),
    ("SUP-108", "Lombardy Casa", "Italy", "EMEA", 38, 44.0, 720.0, 0.84, 0.011, "Net 45"),
    ("SUP-109", "Shenzhen Bright Electronics", "China", "APAC", 48, 58.0, 1_050.0, 0.74, 0.031, "Net 60"),
    ("SUP-110", "Anadolu Textile", "Turkey", "EMEA", 36, 43.0, 640.0, 0.81, 0.019, "Net 60"),
    ("SUP-111", "Prairie Provisions", "Canada", "North America", 7, 6.5, 150.0, 0.98, 0.002, "Net 15"),
    ("SUP-112", "Ohio Valley Brands", "United States", "North America", 14, 15.5, 280.0, 0.92, 0.007, "Net 30"),
    ("SUP-113", "Sonoran Household", "United States", "North America", 16, 19.0, 320.0, 0.90, 0.009, "Net 30"),
    ("SUP-114", "Bengaluru Home Works", "India", "APAC", 44, 52.0, 830.0, 0.77, 0.026, "Net 60"),
    ("SUP-115", "Seoul Precision", "South Korea", "APAC", 33, 31.0, 610.0, 0.91, 0.006, "Net 45"),
    ("SUP-116", "Osaka Kaden", "Japan", "APAC", 35, 33.0, 660.0, 0.95, 0.003, "Net 45"),
    ("SUP-117", "Andes Naturals", "Peru", "LATAM", 40, 46.0, 700.0, 0.83, 0.014, "Net 60"),
    ("SUP-118", "Iberia Casa", "Spain", "EMEA", 32, 30.0, 580.0, 0.89, 0.008, "Net 45"),
    ("SUP-119", "Baltic Paper Union", "Poland", "EMEA", 30, 34.0, 540.0, 0.87, 0.010, "Net 45"),
    ("SUP-120", "Fraser Valley Foods", "Canada", "North America", 6, 5.5, 130.0, 0.99, 0.002, "Net 15"),
    ("SUP-121", "Guangdong Toyworks", "China", "APAC", 50, 62.0, 1_120.0, 0.72, 0.028, "Net 60"),
    ("SUP-122", "Nordic Outdoor AB", "Sweden", "EMEA", 31, 29.0, 570.0, 0.94, 0.004, "Net 45"),
)

# Which suppliers plausibly serve which department. Left unconstrained, a
# paper mill ends up supplying frozen dog food and the supplier scorecard stops
# meaning anything.
DEPARTMENT_SUPPLIERS: dict[str, tuple[str, ...]] = {
    "Electronics": ("SUP-101", "SUP-109", "SUP-115", "SUP-116"),
    "Home & Kitchen": ("SUP-107", "SUP-108", "SUP-113", "SUP-114", "SUP-118"),
    "Grocery": ("SUP-111", "SUP-120", "SUP-104", "SUP-117"),
    "Apparel": ("SUP-102", "SUP-110", "SUP-106"),
    "Toys & Games": ("SUP-121", "SUP-101", "SUP-112"),
    "Health & Beauty": ("SUP-117", "SUP-118", "SUP-103", "SUP-115"),
    "Sports & Outdoors": ("SUP-122", "SUP-102", "SUP-105"),
    "Office Products": ("SUP-119", "SUP-112", "SUP-103"),
    "Pet Supplies": ("SUP-111", "SUP-104", "SUP-113"),
    "Baby": ("SUP-106", "SUP-112", "SUP-105"),
}

# Reason codes a WMS records against a count variance. The distribution is
# deliberately lopsided - mis-picks and receiving errors dominate real variance
# registers, and theft is the smallest line on most of them.
COUNT_REASONS: tuple[tuple[str, float], ...] = (
    ("Mis-pick", 0.26),
    ("Receiving error", 0.21),
    ("Put-away to wrong bin", 0.17),
    ("Damage not recorded", 0.13),
    ("System timing", 0.11),
    ("Unrecorded return", 0.07),
    ("Theft or loss", 0.05),
)

ADJUSTMENT_TYPES: tuple[tuple[str, float, int], ...] = (
    # (type, share of adjustment events, sign of the unit movement)
    ("Damage", 0.30, -1),
    ("Shrink", 0.22, -1),
    ("Expiry", 0.11, -1),
    ("Write-off", 0.09, -1),
    ("Customer return", 0.19, +1),
    ("Found stock", 0.09, +1),
)

# --------------------------------------------------------------------------
# Planning parameters
# --------------------------------------------------------------------------
# The MRP parameter table an ERP would hold. Every number the replenishment
# maths uses that is a *policy* rather than an observation lives here, so the
# app can show its working and a planner can argue with the inputs instead of
# the output.
PLANNING_PARAMETERS: tuple[tuple[str, object, str], ...] = (
    ("ReviewPeriodDays", 7, "Days between replenishment reviews (weekly planning cycle)"),
    ("CarryingCostRate", 0.24, "Annual carrying cost as a fraction of unit cost"),
    ("CapitalRate", 0.09, "Cost of capital component of carrying cost"),
    ("StorageRate", 0.08, "Warehouse space and handling component"),
    ("ServiceRate", 0.04, "Insurance, taxes and inventory service component"),
    ("RiskRate", 0.03, "Obsolescence, damage and shrink component"),
    ("ServiceLevelA", 0.98, "Target cycle service level for A items"),
    ("ServiceLevelB", 0.95, "Target cycle service level for B items"),
    ("ServiceLevelC", 0.90, "Target cycle service level for C items"),
    ("SlowMoverDays", 90, "No shipment for this many days flags a slow mover"),
    ("ObsoleteDays", 180, "No shipment for this many days flags dead stock"),
    ("ExcessCoverWeeks", 26, "Cover above this is excess stock"),
    ("CountToleranceUnits", 0, "Unit variance still counted as an accurate record"),
    ("OnTimeGraceDays", 2, "Days after the promised date still counted as on time"),
    ("StockoutPenaltyPerUnit", 6.5, "Assumed contribution lost per unit short"),
    ("WorkingDaysPerYear", 364, "Planning year, in days"),
)
