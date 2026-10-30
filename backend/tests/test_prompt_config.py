from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_session
from app.main import app
from app.models import ImportBatch, PromptLeading
from app.processor import select_prompt_history


@pytest.fixture
def prompt_client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'prompts.db'}", connect_args={"check_same_thread": False})
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


def values(subject="历史", prompts="初始提示", user="demo-user", time=None):
    return {"subjectId": subject, "prompts": prompts, "userId": user, "creationTime": time}


def test_create_filter_edit_and_prompt_selection(prompt_client):
    client, factory = prompt_client
    response = client.post("/api/prompt-config", json={"rows": [
        {"values": values()},
        {"values": values("数学", "共享提示", None, "2024-01-02T03:04:05Z")},
        {"values": values("General", "通用提示", "Admin")},
    ]})
    assert response.status_code == 200
    assert response.json() == {"saved": 3}
    options = client.get("/api/prompt-config/options").json()
    assert set(options["subjects"]) == {"General", "数学", "历史"}
    assert options["users"] == ["Admin", "demo-user"]
    all_rows = client.get("/api/prompt-config").json()
    assert len(all_rows) == 3
    assert len(client.get("/api/prompt-config", params={"subjectId": "历史", "userId": "demo-user"}).json()) == 1
    assert client.get("/api/prompt-config", params={"subjectId": "历史", "userId": "Admin"}).json() == []
    row = next(item for item in all_rows if item["subjectId"] == "历史")
    original = {key: value for key, value in row.items() if key != "promptLeadingId"}
    changed = {**original, "prompts": "修改后提示", "creationTime": "2025-03-01T10:00:00+08:00"}
    response = client.post("/api/prompt-config", json={"rows": [{
        "promptLeadingId": row["promptLeadingId"], "original": original, "values": changed,
    }]})
    assert response.status_code == 200
    saved = client.get("/api/prompt-config", params={"subjectId": "历史"}).json()[0]
    assert saved["prompts"] == "修改后提示"
    assert saved["creationTime"] == "2025-03-01T02:00:00"
    with factory() as session:
        prompts, source = select_prompt_history(session, "历史", "demo-user")
        assert source == "USER_SUBJECT"
        assert prompts[0].prompts == "修改后提示"
        shared, source = select_prompt_history(session, "数学", "someone")
        assert source == "SHARED_SUBJECT"
        assert shared[0].creation_time == datetime(2024, 1, 2, 3, 4, 5)


def test_conflict_rolls_back_whole_batch_and_preserves_snapshots(prompt_client):
    client, factory = prompt_client
    assert client.post("/api/prompt-config", json={"rows": [{"values": values()}]}).status_code == 200
    row = client.get("/api/prompt-config").json()[0]
    original = {key: value for key, value in row.items() if key != "promptLeadingId"}
    with factory() as session:
        session.add(ImportBatch(user_id="demo-user", source_name="test", source_type="MANUAL",
                                status="NEEDS_REVIEW", prompt_snapshot="初始提示"))
        prompt = session.get(PromptLeading, row["promptLeadingId"])
        prompt.prompts = "另一次修改"
        session.commit()
    response = client.post("/api/prompt-config", json={"rows": [
        {"values": values("新课", "不应保存")},
        {"promptLeadingId": row["promptLeadingId"], "original": original,
         "values": {**original, "prompts": "过期修改"}},
    ]})
    assert response.status_code == 409
    with factory() as session:
        assert len(session.scalars(select(PromptLeading)).all()) == 1
        assert session.get(PromptLeading, row["promptLeadingId"]).prompts == "另一次修改"
        assert session.scalar(select(ImportBatch)).prompt_snapshot == "初始提示"


def test_legacy_empty_user_can_be_edited(prompt_client):
    client, factory = prompt_client
    with factory() as session:
        session.add(PromptLeading(subject_id="历史", prompts="原提示", user_id=""))
        session.commit()
    row = client.get("/api/prompt-config").json()[0]
    original = {key: value for key, value in row.items() if key != "promptLeadingId"}
    response = client.post("/api/prompt-config", json={"rows": [{
        "promptLeadingId": row["promptLeadingId"], "original": original,
        "values": {**original, "prompts": "修改后"},
    }]})
    assert response.status_code == 200
    assert client.get("/api/prompt-config").json()[0]["userId"] is None


@pytest.mark.parametrize("bad_values", [
    values("", "ok"), values("历史", "  "), values("历史", "ok", time="不是时间"),
    values("课" * 101, "ok"),
])
def test_invalid_values_do_not_save(prompt_client, bad_values):
    client, _ = prompt_client
    assert client.post("/api/prompt-config", json={"rows": [{"values": bad_values}]}).status_code == 422
    assert client.get("/api/prompt-config").json() == []


def test_production_configuration_is_unavailable(prompt_client, monkeypatch):
    from app import main

    client, _ = prompt_client
    monkeypatch.setattr(main, "APP_ENV", "production")
    assert client.get("/api/prompt-config").status_code == 503
    assert client.post("/api/prompt-config", json={"rows": [{"values": values()}]}).status_code == 503
