import pandas as pd


def test_moves_endpoints_basic(app, client):
    from invapp.services.state import set_state

    # Craft minimal sku_stats with FZ and EXT states
    df = pd.DataFrame([
        {"SKU": "S1", "SKU_Desc": "S1 Desc", "Supplier": "SupA", "Protein": "Chicken", "ProductState": "FZ", "OnHandWeightTotal": 100.0, "NumPacksOnHand": 10, "AvgWeeklyUsage": 50.0},
        {"SKU": "S1", "SKU_Desc": "S1 Desc", "Supplier": "SupA", "Protein": "Chicken", "ProductState": "EXT", "OnHandWeightTotal": 10.0,  "NumPacksOnHand": 1,  "AvgWeeklyUsage": 50.0},
    ])
    set_state(sku_stats=df, holding_cost=pd.DataFrame())

    r1 = client.get('/api/moves/fz_to_ext?threshold=1.0')
    assert r1.status_code == 200
    data1 = r1.get_json()
    # desired ext = 50, ext=10, available FZ=100, so move 40
    assert data1 and abs(data1[0]['WeightToMove'] - 40.0) < 1e-6

    r2 = client.get('/api/moves/ext_to_fz?threshold=1.0')
    assert r2.status_code == 200
    data2 = r2.get_json()
    # desired fz = 50, fz=100 so no need to return
    assert isinstance(data2, list)

