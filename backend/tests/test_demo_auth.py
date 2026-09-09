import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core import demo_auth
from app.core.config import Settings, get_settings
from app.models import Employee, User, UserRole


def session_for(client, employee_id="EMP002"):
    user = next(
        item for item in client.get("/api/demo/users").json() if item["employee_id"] == employee_id
    )
    response = client.post("/api/demo/session", json={"user_id": user["id"]})
    assert response.status_code == 200
    return response.json(), {"Authorization": "Bearer " + response.json()["access_token"]}


def test_demo_sessions_use_current_server_identity(client, db_session):
    schema = client.get("/openapi.json").json()
    assert "DemoBearerSession" in schema["components"]["securitySchemes"]
    created, headers = session_for(client)
    assert created["token_type"] == "bearer"
    assert client.get("/api/session", headers=headers).json()["role"] == "manager"
    user = db_session.scalar(select(User).where(User.employee_id == "EMP002"))
    user.role = UserRole.EMPLOYEE
    db_session.commit()
    assert client.get("/api/session", headers=headers).json()["role"] == "employee"
    db_session.get(Employee, "EMP002").is_active = False
    db_session.commit()
    assert client.get("/api/session", headers=headers).status_code == 401


@pytest.mark.parametrize(
    "authorization", ["", "Bearer forged", "Basic abc", "Bearer abc.def", "Bearer " + "a" * 3000]
)
def test_invalid_session_is_rejected(client, authorization):
    response = client.get("/api/session", headers={"Authorization": authorization})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_session"


def test_signature_and_expiry_are_checked(client, monkeypatch):
    created, headers = session_for(client)
    token = created["access_token"]
    changed = token[:-1] + ("x" if token[-1] != "x" else "y")
    assert (
        client.get("/api/session", headers={"Authorization": "Bearer " + changed}).status_code
        == 401
    )
    now = demo_auth.time.time()
    monkeypatch.setattr(demo_auth.time, "time", lambda: now + 86401)
    assert client.get("/api/session", headers=headers).status_code == 401


def test_client_cannot_supply_role_and_demo_can_be_disabled(client, monkeypatch):
    created, _ = session_for(client)
    assert (
        client.post(
            "/api/demo/session", json={"user_id": created["user"]["id"], "role": "admin"}
        ).status_code
        == 422
    )
    monkeypatch.setattr(get_settings(), "demo_auth_enabled", False)
    assert client.get("/api/demo/users").status_code == 404
    assert (
        client.post("/api/demo/session", json={"user_id": created["user"]["id"]}).status_code == 404
    )


def test_production_rejects_demo_identity_and_default_secret():
    with pytest.raises(ValidationError):
        Settings(app_env="production")
    with pytest.raises(ValidationError):
        Settings(app_env="production", demo_auth_enabled=False)
    assert Settings(app_env="production", demo_auth_enabled=False, session_signing_key="x" * 40)
