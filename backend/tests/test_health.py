from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_live_health() -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_v1_and_v2_status_contracts_exist() -> None:
    assert client.get("/api/v1/system/status").status_code == 200
    assert client.get("/api/v2/system/status").status_code == 200
