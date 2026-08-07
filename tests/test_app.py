import json
import pytest


@pytest.fixture
def app():
    from invapp import create_app

    app = create_app()
    app.config.update({
        "TESTING": True,
    })
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_dashboard_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Weekly Inbound Volume" in resp.data


def test_api_ping(client):
    resp = client.get("/api/ping")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload == {"message": "pong"}


def test_inventory_summary(client):
    resp = client.get("/api/inventory/summary")
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(["total_items", "low_stock", "out_of_stock", "last_updated"]).issubset(data.keys())

