from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.eraselock.authority import InMemoryAuthority
from backend.eraselock.sqlite_authority import SQLiteAuthority
from backend.adapters.versioned_objects import VersionedObjectStore
from contracts.adapter_protocol import SubjectScope


def client(monkeypatch):
    monkeypatch.setenv("RGX_ENV", "local")
    monkeypatch.setenv("RGX_AUTH_MODE", "demo")
    monkeypatch.setenv("RGX_LOCAL_API_TOKEN", "test-secret")
    authority = InMemoryAuthority()
    authority.register_subject(SubjectScope("t01", "p017", 4))
    authority.register_subject(SubjectScope("t01", "p018", 1))
    return TestClient(create_app(authority, adapters=[VersionedObjectStore(authority)]))


def headers(subject="p017"):
    return {
        "Authorization": "Bearer test-secret",
        "X-RGX-Tenant": "t01",
        "X-RGX-Subject": subject,
    }


def body(subject="p017", request="req-1"):
    return {
        "tenant_id": "t01",
        "subject_id": subject,
        "subject_generation": 4 if subject == "p017" else 1,
        "request_id": request,
    }


def test_local_api_enforces_identity_and_idempotency(monkeypatch):
    api = client(monkeypatch)
    assert api.get("/health").json() == {"status": "ok", "mode": "local-only"}
    assert api.post("/v1/erase-requests", json=body()).status_code == 401
    assert api.post("/v1/erase-requests", json=body("p018"), headers=headers()).status_code == 403
    first = api.post("/v1/erase-requests", json=body(), headers=headers())
    assert first.status_code == 202
    assert first.json()["state"] == "VERIFIED"
    repeated = api.post("/v1/erase-requests", json=body(), headers=headers())
    assert repeated.json()["decision_id"] == first.json()["decision_id"]
    assert repeated.json()["state"] == "VERIFIED"
    decision_id = first.json()["decision_id"]
    assert api.get(f"/v1/erase-requests/{decision_id}", headers=headers()).status_code == 200
    assert api.get(f"/v1/erase-requests/{decision_id}", headers=headers("p018")).status_code == 403


def test_api_is_disabled_outside_local_mode(monkeypatch):
    api = client(monkeypatch)
    monkeypatch.setenv("RGX_ENV", "dev")
    assert api.post("/v1/erase-requests", json=body(), headers=headers()).status_code == 503


def test_local_api_decision_survives_server_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("RGX_ENV", "local")
    monkeypatch.setenv("RGX_AUTH_MODE", "demo")
    monkeypatch.setenv("RGX_LOCAL_API_TOKEN", "test-secret")
    path = tmp_path / "authority.sqlite3"
    authority = SQLiteAuthority(path)
    authority.register_subject(SubjectScope("t01", "p017", 4))
    first_api = TestClient(create_app(authority, adapters=[VersionedObjectStore(authority)]))
    first = first_api.post("/v1/erase-requests", json=body(), headers=headers())
    assert first.status_code == 202
    restarted_api = TestClient(create_app(SQLiteAuthority(path)))
    decision_id = first.json()["decision_id"]
    assert restarted_api.get(
        f"/v1/erase-requests/{decision_id}", headers=headers()
    ).json()["state"] == first.json()["state"]


def test_unconfigured_erase_api_fails_closed(monkeypatch):
    monkeypatch.setenv("RGX_ENV", "local")
    monkeypatch.setenv("RGX_AUTH_MODE", "demo")
    monkeypatch.setenv("RGX_LOCAL_API_TOKEN", "test-secret")
    api = TestClient(create_app())
    assert api.post("/v1/erase-requests", json=body(), headers=headers()).status_code == 503


def test_signed_session_ignores_forged_identity_headers(monkeypatch):
    from backend.eraselock.local_security import issue_token
    api = client(monkeypatch)
    monkeypatch.setenv("RGX_AUTH_MODE", "signed")
    monkeypatch.setenv("RGX_LOCAL_SIGNING_KEY", "s" * 32)
    identity = headers("p018")
    identity["Authorization"] = "Bearer " + issue_token(b"s" * 32, "t01", "p017")
    assert api.post("/v1/erase-requests", json=body("p018"), headers=identity).status_code == 403
    assert api.post("/v1/erase-requests", json=body(), headers=identity).json()["state"] == "VERIFIED"
