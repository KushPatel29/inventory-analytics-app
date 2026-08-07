# Inventory Analytics

[![CI](https://github.com/KushPatel29/inventory-analytics-app/actions/workflows/ci.yml/badge.svg)](https://github.com/KushPatel29/inventory-analytics-app/actions/workflows/ci.yml)
![tests](https://img.shields.io/badge/tests-37-brightgreen)
![python](https://img.shields.io/badge/python-3.12-blue)

**Live app:** [inventory-analytics-app.onrender.com](https://inventory-analytics-app.onrender.com/)

A Flask app for warehouse inventory analytics at a perishable-goods
distributor. You upload the weekly workbook the warehouse system exports and it
returns stock-vs-sales indices, weeks-on-hand, ABC classification, dead stock,
holding cost, bin-level location analytics, freezer/fresh rebalancing
suggestions and a parent-level purchase plan.

It was built for a real warehouse. The data here is not — the repo ships a
seeded generator that invents a comparable business, so the whole thing runs
from a clean clone with no warehouse system attached.

```bash
pip install -r requirements.txt
python -m seed.generate_workbook     # writes sample_data/inventory_workbook.xlsx
python run.py                        # http://127.0.0.1:5000, then upload it
```

---

## What it computes

| Report | Question it answers |
|---|---|
| **SVSI** (stock vs sales index) | Which SKUs are overstocked relative to how fast they actually move? |
| **Weeks on hand** | At current velocity, how long does this stock last? |
| **ABC classification** | Which 20% of SKUs carry most of the value? |
| **Movers** | What is turning fastest, and what has not moved at all? |
| **Holding cost** | What does carrying this stock cost, split into capital, service, storage and risk? |
| **Bins** | Where is stock physically sitting, and how much is at the consignment site? |
| **FZ ↔ EXT moves** | What should move between frozen and fresh to hit a target cover? |
| **Purchase plan** | What to buy, rolled up to the parent SKU. |

Holding cost follows the standard four-component split rather than a flat
carrying percentage, because the four components respond to different levers —
capital cost falls with the interest rate, storage cost falls only if you
actually give back the space.

---

## Five defects found while making it runnable

The app had a passing CI badge and none of this worked. The badge was
attached to a workflow whose test job could not have run.

**1. The package did not parse.** `invapp/api/__init__.py` ended with a stray
fragment of a dict literal left over from a paste — four orphaned lines after
the final `return`. The module raised `SyntaxError` on import, so nothing in
the package could be imported at all.

**2. Every upload failed.** `compute_holding_cost` was handed the raw
*Inventory Detail* sheet, but `OnHandCost` is a derived column that only exists
after `process_inventory_snapshot` runs. `df.get("OnHandCost", 0.0)` returned
the scalar default, and a float has no `.fillna`, so the one action the app
exists to perform ended in `'float' object has no attribute 'fillna'`.

**3. On-hand weight ignored quantity.** `merge_data` aggregated
`OnHandWeightLb=("WeightLb", "sum")` — the weight of *one* item per row — while
`OnHandCost` came from `CostValue`, which already included `ItemCount`. Weight
and cost therefore disagreed by the case count. On the sample data the app
reported **3,535 lb of stock worth $856,336**, an implied $242/lb. Every
weeks-on-hand and turnover figure derived from that weight was wrong. Corrected,
the same data reads **105,088 lb at $8.15/lb**.

**4. Move recommendations were doubled.** Sales are joined per SKU, so the same
`AvgWeeklyUsage` is copied onto the FZ row and the EXT row. `_combine_state`
summed it, counting a SKU's demand once per state it was stocked in. A SKU
needing 40 lb moved to fresh was told to move 90.

**5. Three test files never ran.** `test_api.py` and `test_moves_api.py`
requested `app` and `client` fixtures that were defined locally inside
`test_app.py`, so they errored at collection. One of them was already asserting
the correct answer to defect 4 — the test was right, it just never executed.

There is a regression test for each, plus an assertion that the implied cost
per pound stays in a plausible range, which is the cheapest way to catch a
quantity multiplier going missing again.

---

## Running it

```bash
python -m venv .venv && .venv/Scripts/activate      # Windows
python -m venv .venv && source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt

python -m seed.generate_workbook      # sample_data/inventory_workbook.xlsx
python run.py                         # or: python -m flask --app invapp run
```

Open <http://127.0.0.1:5000> and upload the generated workbook.

**Tests:**

```bash
pytest -q
```

**Everything at once, the way CI checks it:**

```bash
python -m seed.generate_workbook && python scripts/demo_smoke.py
```

`demo_smoke.py` uploads the workbook through the real endpoint and asserts all
19 reports return data and all 6 pages render — so the README's claim that a
clean clone works is checked on every push rather than asserted here.

---

## The synthetic data

`seed/generate_workbook.py` writes the same seven sheets the warehouse system
exports (`Sales History`, `Cost Value`, `Inventory Detail`, `Production Batch`,
`Inventory Detail1`, `Key Account`, `Product Detail`) with the columns the
cleaners expect, so the generated file goes through exactly the same code path
as a real export — no test-only branch.

260 SKUs across seven protein categories, 26 weeks of shipments, on-hand stock
split between frozen and fresh, and bin scans at both the main warehouse and a
consignment site. Velocity is drawn from a log-normal, so a few SKUs carry most
of the volume and a long tail barely moves — without that skew the ABC
classification and the dead-stock report have nothing to find.

Fixed seed, so two runs on two machines produce the same workbook.

```bash
python -m seed.generate_workbook --skus 500 --weeks 52 --seed 9
```

---

## Deployment

Dockerfile, `render.yaml` and a systemd unit script are included and unchanged
from the original deployment.

```bash
docker build -t inventory-analytics . && docker run -p 8000:8000 inventory-analytics
```

Health check is on `/healthz`.

---

## Notes

Originally built against a live warehouse export. All employer identifiers,
customer names and cost data have been removed; everything in this repo is
generated.
