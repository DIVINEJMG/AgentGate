from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_contract() -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "audoryn-api"


def test_v1_system_status_contract() -> None:
    response = client.get("/api/v1/system/status")
    assert response.status_code == 200
    body = response.json()
    assert body["apiVersion"] == "v1"
    assert body["currentVersion"] == "v2"
    assert body["supportedVersions"] == ["v1", "v2"]
    assert body["securityMode"] == "fail-closed"


def test_v2_system_status_contract() -> None:
    response = client.get("/api/v2/system/status")
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == "v2"
    assert body["controlPlane"]["securityMode"] == "fail-closed"
    assert body["lifecycle"]["current"] == "v2"
    assert body["lifecycle"]["supported"] == ["v1", "v2"]


def test_qstash_heartbeat_requires_signature() -> None:
    response = client.post(
        "/internal/v1/runtime/heartbeat",
        json={"source": "contract-test"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "QStash signature required."


def test_qstash_failure_callback_requires_signature() -> None:
    response = client.post(
        "/internal/v1/runtime/failures",
        json={"sourceMessageId": "msg-test", "url": "https://example.invalid"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "QStash signature required."
