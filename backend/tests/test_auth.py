from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker

from app import auth
from app.db import Base, get_session
from app.main import app
from app.models import AuthSession, ImportBatch, PromptLeading, UserProfile
from conftest import AUTH_ORIGIN, authenticate


@pytest.fixture
def auth_client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'auth.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_session():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        yield client, factory
    app.dependency_overrides.clear()
    engine.dispose()


def test_authentication_session_csrf_and_logout_revocation(auth_client):
    client, factory = auth_client
    assert client.get("/api/health").json() == {"status": "ok", "mode": "preview"}
    assert client.get("/api/imports").status_code == 401
    assert client.get("/api/prompt-config/options").status_code == 401

    created = client.post(
        "/api/auth/register",
        headers={"Origin": AUTH_ORIGIN},
        json={"userId": " learner ", "password": "correct-horse-battery"},
    )
    assert created.status_code == 201
    assert created.json() == {"userId": "learner"}
    assert client.post(
        "/api/auth/register",
        headers={"Origin": AUTH_ORIGIN},
        json={"userId": "learner", "password": "another-correct-password"},
    ).status_code == 409

    login = client.post(
        "/api/auth/login",
        headers={"Origin": AUTH_ORIGIN},
        json={"userId": "learner", "password": "correct-horse-battery"},
    )
    assert login.status_code == 200
    cookie = login.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/api" in cookie
    csrf = login.json()["csrfToken"]
    token = client.cookies.get(auth.SESSION_COOKIE)
    with factory() as session:
        stored = session.scalar(select(AuthSession))
        assert stored is not None
        assert stored.session_token_hash != token
        assert stored.session_token_hash == auth._token_hash(token)
        assert session.get(UserProfile, "learner").password_hash != "correct-horse-battery"

    assert client.get("/api/auth/me").json()["userId"] == "learner"
    no_csrf = client.post(
        "/api/imports",
        headers={"Origin": AUTH_ORIGIN, "Idempotency-Key": "missing-csrf"},
        data={"text": "不得提交"},
    )
    assert no_csrf.status_code == 403
    client.headers.update({"Origin": AUTH_ORIGIN, "X-CSRF-Token": csrf})
    created_import = client.post(
        "/api/imports",
        headers={"Idempotency-Key": "authenticated-import"},
        data={"text": "课程知识", "userId": "another-user"},
    )
    assert created_import.status_code == 200
    with factory() as session:
        batch = session.get(ImportBatch, created_import.json()["importBatchId"])
        assert batch.user_id == "learner"

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    with factory() as session:
        assert session.scalar(select(AuthSession)) is None


def test_invalid_login_uses_same_message_and_requires_allowed_origin(auth_client):
    client, _ = auth_client
    authenticate(client, "student-a")
    client.cookies.clear()
    for user_id, password in [
        ("missing-user", "correct-horse-battery"),
        ("student-a", "incorrect-password"),
    ]:
        response = client.post(
            "/api/auth/login",
            headers={"Origin": AUTH_ORIGIN},
            json={"userId": user_id, "password": password},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "用户名或密码错误。"
    assert client.post(
        "/api/auth/login",
        headers={"Origin": "https://untrusted.example"},
        json={"userId": "student-a", "password": "correct-horse-battery"},
    ).status_code == 403


def test_session_expiration_and_production_cookie_flags(auth_client, monkeypatch):
    client, factory = auth_client
    authenticate(client, "student-b")
    token = client.cookies.get(auth.SESSION_COOKIE)
    token_hash = auth._token_hash(token)
    with factory() as session:
        stored = session.get(AuthSession, token_hash)
        stored.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        session.commit()
    assert client.get("/api/auth/me").status_code == 401

    production_settings = replace(
        auth.settings,
        app_env="production",
        admin_user_id="student-b",
    )
    monkeypatch.setattr(auth, "settings", production_settings)
    assert client.post(
        "/api/auth/register",
        headers={"Origin": AUTH_ORIGIN},
        json={"userId": "student-b", "password": "another-correct-password"},
    ).status_code == 403
    login = client.post(
        "/api/auth/login",
        headers={"Origin": AUTH_ORIGIN},
        json={"userId": "student-b", "password": "correct-horse-battery"},
    )
    assert login.status_code == 200
    assert "secure" in login.headers["set-cookie"].lower()


def test_login_rate_limit_is_configurable(auth_client, monkeypatch):
    client, factory = auth_client
    authenticate(client, "student-c")
    client.cookies.clear()
    with factory() as session:
        session.execute(delete(auth.AuthThrottle))
        session.commit()
    monkeypatch.setattr(auth, "LOGIN_ATTEMPTS_PER_WINDOW", 1)
    first = client.post(
        "/api/auth/login",
        headers={"Origin": AUTH_ORIGIN},
        json={"userId": "student-c", "password": "incorrect-password"},
    )
    second = client.post(
        "/api/auth/login",
        headers={"Origin": AUTH_ORIGIN},
        json={"userId": "student-c", "password": "incorrect-password"},
    )
    assert first.status_code == 401
    assert second.status_code == 429


def test_owner_isolation_and_admin_prompt_permissions(auth_client):
    client, factory = auth_client
    authenticate(client, "student-one")
    owner_batch = client.post(
        "/api/imports",
        headers={"Idempotency-Key": "owner-one-import"},
        data={"text": "owner one"},
    ).json()
    assert owner_batch["importBatchId"]
    own_prompt = client.post(
        "/api/prompt-config",
        json={"rows": [{"values": {
            "subjectId": "历史",
            "prompts": "个人提示词",
            "userId": None,
            "creationTime": None,
        }}]},
    )
    assert own_prompt.status_code == 200
    with factory() as session:
        prompt = session.scalar(
            select(PromptLeading).where(PromptLeading.prompts == "个人提示词")
        )
        assert prompt.user_id == "student-one"

    client.post("/api/auth/logout")
    authenticate(client, "student-two")
    assert client.get("/api/imports").json() == []
    assert client.get(f"/api/imports/{owner_batch['importBatchId']}").status_code == 404
    assert client.get(
        "/api/prompt-config",
        params={"userId": "student-one"},
    ).status_code == 403
    shared_write = client.post(
        "/api/prompt-config",
        json={"rows": [{"values": {
            "subjectId": "历史",
            "prompts": "越权共享提示",
            "userId": None,
            "creationTime": None,
        }}]},
    )
    assert shared_write.status_code == 200
    with factory() as session:
        personal_prompt = session.scalar(
            select(PromptLeading).where(PromptLeading.prompts == "越权共享提示")
        )
        assert personal_prompt.user_id == "student-two"
    assert [
        row["prompts"] for row in client.get("/api/prompt-config").json()
    ] == ["越权共享提示"]

    client.post("/api/auth/logout")
    admin = authenticate(client, "Admin")
    assert admin["isAdmin"] is True
    shared_write = client.post(
        "/api/prompt-config",
        json={"rows": [{"values": {
            "subjectId": "历史",
            "prompts": "管理员共享提示",
            "userId": None,
            "creationTime": None,
        }}]},
    )
    assert shared_write.status_code == 200
    assert client.get("/api/prompt-config", params={"userId": "student-one"}).status_code == 200
    with factory() as session:
        shared_prompt = session.scalar(
            select(PromptLeading).where(PromptLeading.prompts == "管理员共享提示")
        )
        assert shared_prompt.user_id is None
        shared_values = {
            "subjectId": shared_prompt.subject_id,
            "prompts": shared_prompt.prompts,
            "userId": None,
            "creationTime": shared_prompt.creation_time.isoformat(sep=" "),
        }

    client.post("/api/auth/logout")
    second_login = client.post(
        "/api/auth/login",
        headers={"Origin": AUTH_ORIGIN},
        json={"userId": "student-two", "password": "correct-horse-battery"},
    )
    assert second_login.status_code == 200
    client.headers.update({"X-CSRF-Token": second_login.json()["csrfToken"]})
    edit_shared = client.post("/api/prompt-config", json={"rows": [{
        "promptLeadingId": shared_prompt.prompt_leading_id,
        "original": shared_values,
        "values": {**shared_values, "prompts": "试图编辑共享提示"},
    }]})
    assert edit_shared.status_code == 404
    with factory() as session:
        assert session.get(PromptLeading, shared_prompt.prompt_leading_id).prompts == "管理员共享提示"
