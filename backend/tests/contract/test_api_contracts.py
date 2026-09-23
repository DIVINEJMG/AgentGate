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


def test_storage_smoke_requires_qstash_signature() -> None:
    response = client.post("/internal/v1/runtime/storage-smoke", json={})
    assert response.status_code == 401
    assert response.json()["detail"] == "QStash signature required."


def test_migration_shadow_requires_qstash_signature() -> None:
    response = client.post("/internal/v1/migration/shadow", json={})
    assert response.status_code == 401
    assert response.json()["detail"] == "QStash signature required."


def test_migration_run_requires_qstash_signature() -> None:
    response = client.post("/internal/v1/migration/run", json={})
    assert response.status_code == 401
    assert response.json()["detail"] == "QStash signature required."


def test_agent_identity_routes_exist_in_v1_and_v2() -> None:
    schema = app.openapi()
    required = {
        "/api/v1/organizations/{organization_id}/agents",
        "/api/v2/organizations/{organization_id}/agents",
        "/api/v1/organizations/{organization_id}/agents/{agent_id}/lifecycle",
        "/api/v2/organizations/{organization_id}/agents/{agent_id}/lifecycle",
        "/api/v1/organizations/{organization_id}/agents/{agent_id}/credentials/rotate",
        "/api/v2/organizations/{organization_id}/agents/{agent_id}/credentials/rotate",
        "/api/v1/organizations/{organization_id}/agents/{agent_id}/credentials/revoke",
        "/api/v2/organizations/{organization_id}/agents/{agent_id}/credentials/revoke",
    }
    assert required <= set(schema["paths"])
