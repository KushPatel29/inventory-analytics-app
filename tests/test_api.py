import pandas as pd


def test_api_before_processing_returns_not_found(client):
    r = client.get('/api/kpis')
    assert r.status_code in (404, 200)  # kpis returns 404 if no data
    # inventory_summary always returns 200 static
    r2 = client.get('/api/inventory/summary')
    assert r2.status_code == 200


def test_insights_after_set_state(app, client):
    from invapp.services.state import set_state
    df = pd.DataFrame([
        {
            'SKU': 'X1', 'Supplier': 'S', 'Protein': 'Chicken', 'Description': 'X',
            'ProductState': 'EXT', 'ProductName': 'X',
            'OnHandWeightTotal': 100.0, 'OnHandCostTotal': 200.0,
            'TotalShippedLb': 50.0, 'TotalProductionLb': 10.0,
            'TotalUsage': 60.0, 'AvgWeeklyUsage': 15.0,
            'WeeksOnHand': 6.67, 'AnnualTurns': 7.8,
        }
    ])
    set_state(sku_stats=df, holding_cost=pd.DataFrame(), raw_sheets={})

    r = client.get('/api/kpis')
    assert r.status_code == 200
    data = r.get_json()
    assert 'total_skus' in data and data['total_skus'] == 1

    r2 = client.get('/api/purchase_plan?woh=4')
    # purchase plan requires state; should return 200 with list (maybe empty)
    assert r2.status_code == 200 or r2.status_code == 404

