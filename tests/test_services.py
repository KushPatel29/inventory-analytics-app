import pandas as pd

from invapp.services.aggregation import aggregate_sales_history, merge_data, aggregate_final_data


def test_aggregate_pipeline_basic():
    sales = pd.DataFrame(
        [
            {"SKU": "A1", "Supplier": "SUP1", "Protein": "Chicken", "Description": "Item A", "ShippedLb": 10, "QuantityOrdered": 2, "Cost": 20, "Rev": 30, "DateExpected": "2024-01-01"},
            {"SKU": "A1", "Supplier": "SUP1", "Protein": "Chicken", "Description": "Item A", "ShippedLb": 5,  "QuantityOrdered": 1, "Cost": 10, "Rev": 15, "DateExpected": "2024-01-08"},
        ]
    )
    inv = pd.DataFrame(
        [
            {"SKU": "A1", "ProductState": "EXT", "ProductName": "Item A", "WeightLb": 20, "CostValue": 40},
        ]
    )
    prod = pd.DataFrame([{"SKU": "A1", "ProductionShippedLb": 2, "Supplier": "SUP1"}])

    agg_sales = aggregate_sales_history(sales)
    merged = merge_data(agg_sales, inv, prod)
    result = aggregate_final_data(merged, sales)

    row = result.iloc[0]
    assert row["SKU"] == "A1"
    assert row["OnHandWeightTotal"] == 20
    assert row["TotalShippedLb"] == 15
    assert row["TotalProductionLb"] == 2

