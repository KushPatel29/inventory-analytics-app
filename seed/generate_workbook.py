"""
Generate the synthetic inventory workbook the app expects.

The app is upload-driven: you hand it a multi-sheet Excel workbook exported
from the warehouse system and it computes KPIs, stock-vs-sales indices,
movers, holding cost, bin analytics and a purchase plan. That makes it
useless to anyone who does not already have such a workbook, so this script
invents one.

The sheet names and columns match `invapp.services.io_utils.EXPECTED_SHEETS`
and the cleaners in `invapp.services.cleaning`, so the generated file goes
through exactly the same code path as a real export.

Usage:
    python -m seed.generate_workbook                    # writes sample_data/inventory_workbook.xlsx
    python -m seed.generate_workbook --out /tmp/wb.xlsx
    python -m seed.generate_workbook --weeks 12 --skus 200
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_SEED = 1701
DEFAULT_SKUS = 260
DEFAULT_WEEKS = 26
DEFAULT_OUT = Path("sample_data") / "inventory_workbook.xlsx"

PROTEINS = ("Beef", "Pork", "Poultry", "Lamb", "Seafood", "Charcuterie", "Game")
CUTS = {
    "Beef": ("Ribeye", "Striploin", "Brisket", "Short Rib", "Chuck Roll", "Flank"),
    "Pork": ("Belly", "Loin", "Back Rib", "Shoulder Butt", "Hock"),
    "Poultry": ("Breast", "Thigh", "Wing", "Whole Bird", "Drumstick"),
    "Lamb": ("Rack", "Leg", "Shoulder", "Shank"),
    "Seafood": ("Salmon Fillet", "Halibut Fillet", "Spot Prawn", "Sea Scallop"),
    "Charcuterie": ("Prosciutto", "Coppa", "Pancetta", "Saucisson"),
    "Game": ("Venison Loin", "Bison Ribeye", "Duck Breast", "Elk Striploin"),
}
PREPS = ("Fresh", "Frozen", "Vac Pack", "Portioned", "Whole")

SUPPLIERS = tuple(
    f"{a} {b}"
    for a in ("Cascade", "Fraser", "Ridgeline", "Silverbrook", "Kootenay", "Alderwood", "Blackpine")
    for b in ("Meats", "Provisions", "Packing Co", "Farms")
)

# EXT = ambient/fresh hold, FZ = frozen. The whole point of the movers report
# is spotting stock sitting in the wrong one.
STATES = ("EXT", "FZ")
LOCATIONS = ("Main", "Main", "Main", "Cold Room A", "Cold Room B", "Key Account")


def build_products(rng: np.random.Generator, n: int) -> pd.DataFrame:
    proteins = rng.choice(PROTEINS, size=n, p=_protein_weights())
    rows = []
    for i, protein in enumerate(proteins):
        cut = CUTS[protein][rng.integers(len(CUTS[protein]))]
        prep = PREPS[rng.integers(len(PREPS))]
        cost_lb = float(np.clip(rng.lognormal(np.log(7.0), 0.45), 1.5, 40.0))
        rows.append(
            {
                "SKU": f"{10000 + i}",
                "ProductName": f"{cut} {prep}",
                "Protein": protein,
                "Supplier": SUPPLIERS[rng.integers(len(SUPPLIERS))],
                "CostPerLb": round(cost_lb, 3),
                "CaseWeightLb": round(float(np.clip(rng.normal(11.0, 4.0), 2.0, 40.0)), 2),
                "ProductState": STATES[0] if rng.random() < 0.55 else STATES[1],
            }
        )
    return pd.DataFrame(rows)


def _protein_weights() -> list[float]:
    raw = np.array([0.30, 0.19, 0.17, 0.07, 0.14, 0.08, 0.05])
    return list(raw / raw.sum())


def build_sales_history(rng, products: pd.DataFrame, weeks: int, end: date) -> pd.DataFrame:
    """One row per SKU per week that SKU actually moved."""
    rows = []
    # Velocity is heavily skewed: a few SKUs carry most of the volume, which is
    # what makes the ABC classification and the dead-stock report meaningful.
    velocity = rng.lognormal(mean=0.0, sigma=1.3, size=len(products))
    for week in range(weeks):
        week_end = end - timedelta(days=7 * (weeks - week - 1))
        # Seasonal swell into the back half of the window.
        season = 1.0 + 0.18 * np.sin(2 * np.pi * week / 26.0)
        for idx, prod in enumerate(products.itertuples(index=False)):
            if rng.random() > min(0.95, 0.18 + velocity[idx] / 8.0):
                continue  # no movement this week
            lb = float(np.clip(rng.gamma(2.0, 18.0 * velocity[idx] * season), 1.0, 8000.0))
            cases = max(1, int(round(lb / max(prod.CaseWeightLb, 1.0))))
            cost = lb * prod.CostPerLb
            rows.append(
                {
                    "SKU": prod.SKU,
                    "Description": prod.ProductName,
                    "Protein": prod.Protein,
                    "Supplier": prod.Supplier,
                    "ShippedLb": round(lb, 2),
                    "QuantityOrdered": cases,
                    "Cost": round(cost, 2),
                    "Rev": round(cost * float(rng.normal(1.27, 0.09)), 2),
                    "DateExpected": week_end.isoformat(),
                }
            )
    return pd.DataFrame(rows)


def build_inventory_detail(rng, products: pd.DataFrame, end: date) -> pd.DataFrame:
    """On-hand stock. `SKU` carries 'code - name', which the cleaner splits."""
    rows = []
    for prod in products.itertuples(index=False):
        for state in STATES:
            if rng.random() < 0.35:
                continue
            items = int(rng.integers(0, 60))
            if items == 0:
                continue
            age_days = int(rng.integers(1, 240))
            rows.append(
                {
                    "SKU": f"{prod.SKU} - {prod.ProductName}",
                    "ProductName": prod.ProductName,
                    "ProductState": state,
                    "ItemCount": items,
                    "WeightLb": round(prod.CaseWeightLb, 2),
                    "Cost_pr": round(prod.CostPerLb * prod.CaseWeightLb, 2),
                    "CostValue": round(prod.CostPerLb * prod.CaseWeightLb * items, 2),
                    "OriginDate": (end - timedelta(days=age_days)).isoformat(),
                    "Supplier": prod.Supplier,
                }
            )
    return pd.DataFrame(rows)


def build_bins(rng, products: pd.DataFrame, end: date, *, key_account: bool) -> pd.DataFrame:
    """Bin-level scans, either the main warehouse or the key account's site."""
    rows = []
    for prod in products.itertuples(index=False):
        if rng.random() < (0.75 if key_account else 0.35):
            continue
        scanned = end - timedelta(days=int(rng.integers(0, 90)))
        created = scanned - timedelta(days=int(rng.integers(0, 120)))
        location = "Key Account" if key_account else LOCATIONS[rng.integers(len(LOCATIONS) - 1)]
        rows.append(
            {
                "SKU": f"{prod.SKU} - {prod.ProductName}",
                "ProductName": prod.ProductName,
                # Carried on the bin rows so the by-protein breakdown on the
                # bins page has something to group on.
                "Protein": prod.Protein,
                "Supplier": prod.Supplier,
                "ProductLocation": location,
                "LastKnownBin": f"{chr(65 + int(rng.integers(0, 8)))}{int(rng.integers(1, 40)):02d}",
                "ItemCount": int(rng.integers(1, 40)),
                "WeightLb": round(prod.CaseWeightLb, 2),
                "BinScannedAt": scanned.isoformat(),
                "CreatedAt": created.isoformat(),
                "PackId1": f"P{int(rng.integers(100000, 999999))}",
                "ProductState": prod.ProductState,
            }
        )
    return pd.DataFrame(rows)


def build_production_batch(rng, products: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for prod in products.itertuples(index=False):
        if rng.random() < 0.6:
            continue
        rows.append(
            {
                "SKU": prod.SKU,
                "Supplier": prod.Supplier,
                "ProductionShippedLb": round(float(rng.gamma(2.0, 40.0)), 2),
            }
        )
    return pd.DataFrame(rows)


def build_cost_value(products: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "SKU": products["SKU"],
            "CostNow": (products["CostPerLb"] * products["CaseWeightLb"]).round(2),
        }
    )


def build_product_detail(products: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "SKU": products["SKU"],
            "ProductName": products["ProductName"],
            "Protein": products["Protein"],
            "Supplier": products["Supplier"],
            "CostNow": (products["CostPerLb"] * products["CaseWeightLb"]).round(2),
        }
    )


def generate(
    *,
    seed: int = DEFAULT_SEED,
    skus: int = DEFAULT_SKUS,
    weeks: int = DEFAULT_WEEKS,
    end: date | None = None,
) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    end = end or date(2026, 6, 30)
    products = build_products(rng, skus)
    return {
        "Sales History": build_sales_history(rng, products, weeks, end),
        "Cost Value": build_cost_value(products),
        "Inventory Detail": build_inventory_detail(rng, products, end),
        "Production Batch": build_production_batch(rng, products),
        "Inventory Detail1": build_bins(rng, products, end, key_account=False),
        "Key Account": build_bins(rng, products, end, key_account=True),
        "Product Detail": build_product_detail(products),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--skus", type=int, default=DEFAULT_SKUS)
    ap.add_argument("--weeks", type=int, default=DEFAULT_WEEKS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    sheets = generate(seed=args.seed, skus=args.skus, weeks=args.weeks)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(args.out, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)

    print(f"wrote {args.out}")
    for name, frame in sheets.items():
        print(f"  {name:20} {len(frame):>7,} rows x {len(frame.columns)} cols")
    sales = sheets["Sales History"]
    print(f"\n  shipped weight : {sales['ShippedLb'].sum():>12,.0f} lb")
    print(f"  revenue        : ${sales['Rev'].sum():>11,.0f}")
    print(f"  on-hand value  : ${sheets['Inventory Detail']['CostValue'].sum():>11,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
