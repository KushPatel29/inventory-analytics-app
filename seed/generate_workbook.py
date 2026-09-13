"""
Write the synthetic warehouse export the app ingests.

The app is upload-driven: you hand it the multi-sheet workbook the WMS and ERP
export every Monday morning and it plans against it. That makes it useless to
anyone who does not already have such a workbook, so this script invents one.

The sheet names and columns match `invapp.services.io_utils.EXPECTED_SHEETS`
and the cleaners in `invapp.services.cleaning`, so the generated file goes
through exactly the same code path as a real export - there is no demo branch
anywhere in the pipeline.

Usage::

    python -m seed.generate_workbook                     # sample_data/inventory_workbook.xlsx
    python -m seed.generate_workbook --csv-dir data      # also write one CSV per sheet
    python -m seed.generate_workbook --skus 900 --weeks 104 --seed 7
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from seed.dataset import (
    DEFAULT_END,
    DEFAULT_SEED,
    DEFAULT_SKUS,
    DEFAULT_WEEKS,
    SHEET_NAMES,
    generate,
)

DEFAULT_OUT = Path("sample_data") / "inventory_workbook.xlsx"

# Sheet name -> CSV stem. The Power BI model reads these rather than the
# workbook: Power Query can open an xlsx, but a CSV per table keeps the M
# queries readable and the column types inferable from one place.
CSV_NAMES = {
    "Item Master": "dim_item",
    "Supplier Master": "dim_supplier",
    "Network Nodes": "dim_node",
    "Demand History": "fact_demand",
    "Inventory Snapshot": "fact_inventory",
    "Purchase Orders": "fact_purchase_order",
    "Cycle Counts": "fact_cycle_count",
    "Inventory Adjustments": "fact_adjustment",
    "Planning Parameters": "ref_planning_parameter",
}

__all__ = ["generate", "write_workbook", "write_csvs", "main"]


def write_workbook(sheets: dict[str, pd.DataFrame], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        for name in SHEET_NAMES:
            sheets[name].to_excel(writer, sheet_name=name, index=False)
    return out


def write_csvs(sheets: dict[str, pd.DataFrame], csv_dir: Path) -> list[Path]:
    """One CSV per sheet, for the Power BI model and the SQL marts.

    ``lineterminator="\\n"`` on purpose: the default on Windows is CRLF, which
    makes a file generated here differ byte-for-byte from the same file
    generated in CI, and the drift check that guards the Power BI project would
    fail on the line endings rather than on anything that matters.
    """
    csv_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name in SHEET_NAMES:
        path = csv_dir / f"{CSV_NAMES[name]}.csv"
        sheets[name].to_csv(path, index=False, lineterminator="\n")
        written.append(path)
    return written


def _summarise(sheets: dict[str, pd.DataFrame]) -> str:
    demand = sheets["Demand History"]
    inv = sheets["Inventory Snapshot"]
    items = sheets["Item Master"]
    cost = dict(zip(items["SKU"], items["UnitCost"], strict=True))
    inv_value = (inv["OnHandUnits"] * inv["SKU"].map(cost)).sum()
    price = dict(zip(items["SKU"], items["UnitPrice"], strict=True))
    cogs = (demand["UnitsShipped"] * demand["SKU"].map(cost)).sum()
    revenue = (demand["UnitsShipped"] * demand["SKU"].map(price)).sum()
    weeks = demand["WeekEnding"].nunique()

    lines = [
        f"  SKUs               : {len(items):>12,}",
        f"  nodes              : {sheets['Network Nodes'].shape[0]:>12,}",
        f"  weeks of history   : {weeks:>12,}",
        f"  units shipped      : {demand['UnitsShipped'].sum():>12,}",
        f"  net sales          : ${revenue:>11,.0f}",
        f"  cost of goods sold : ${cogs:>11,.0f}",
        f"  on-hand value      : ${inv_value:>11,.0f}",
        f"  annualised turns   : {(cogs * (52.0 / max(weeks, 1))) / max(inv_value, 1.0):>12,.2f}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--skus", type=int, default=DEFAULT_SKUS)
    ap.add_argument("--weeks", type=int, default=DEFAULT_WEEKS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--csv-dir",
        type=Path,
        default=None,
        help="also write one CSV per sheet here (the Power BI model reads data/)",
    )
    args = ap.parse_args(argv)

    sheets = generate(seed=args.seed, skus=args.skus, weeks=args.weeks, end=DEFAULT_END)
    write_workbook(sheets, args.out)

    print(f"wrote {args.out}")
    for name in SHEET_NAMES:
        frame = sheets[name]
        print(f"  {name:22} {len(frame):>8,} rows x {len(frame.columns)} cols")

    if args.csv_dir is not None:
        written = write_csvs(sheets, args.csv_dir)
        print(f"\nwrote {len(written)} CSVs to {args.csv_dir}")

    print()
    print(_summarise(sheets))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
