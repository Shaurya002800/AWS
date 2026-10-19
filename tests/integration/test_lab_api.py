from fastapi.testclient import TestClient

from backend.api.app import create_app


def test_local_dashboard_and_lab_endpoint(database, monkeypatch):
    base, _schema, _dsn = database
    monkeypatch.setenv("RGX_ENV", "local")
    monkeypatch.setenv("RGX_AUTH_MODE", "demo")
    monkeypatch.setenv("RGX_LOCAL_API_TOKEN", "local-test")
    monkeypatch.setenv("RGX_TEST_POSTGRES_DSN", base)
    api = TestClient(create_app())
    headers = {
        "Authorization": "Bearer local-test",
        "X-RGX-Tenant": "t01",
        "X-RGX-Subject": "operator",
    }
    assert api.get("/ui").status_code == 200
    assert api.get("/ui/assets/app.js").status_code == 200
    assert len(api.get("/v1/lab/scenarios", headers=headers).json()["scenarios"]) == 4
    result = api.post(
        "/v1/lab/runs", headers=headers,
        json={"scenario": "old_database_recovery", "mode": "protected", "seed": 42},
    )
    assert result.status_code == 200
    assert result.json()["forbidden_exposures"] == 0
    assert result.json()["evidence"]["signature_status"] == "UNSIGNED_LOCAL"
