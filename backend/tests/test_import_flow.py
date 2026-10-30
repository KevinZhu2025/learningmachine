from fastapi.testclient import TestClient

from app.db import Base
from app.main import app
from app.models import PromptLeading


def test_preview_import_review_and_confirm(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import main
    from app.db import get_session

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_session():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(main, "UPLOAD_ROOT", tmp_path / "uploads")
    main.UPLOAD_ROOT.mkdir(exist_ok=True)
    client = TestClient(app)
    try:
        response = client.post(
            "/api/imports",
            data={"subjectId": "历史", "userComments": "请提取课程重点", "text": "北京人\n能够直立行走并使用天然火。"},
        )
        assert response.status_code == 200
        batch = response.json()
        assert batch["status"] == "NEEDS_REVIEW"
        assert batch["isPreview"] is True
        assert batch["subjectId"] == "历史"
        assert batch["items"][0]["knowledgeTitle"] == "北京人"
        prompt_session = testing_session()
        try:
            prompt = prompt_session.query(PromptLeading).one()
            assert prompt.user_id == "demo-user"
            assert prompt.subject_id == "历史"
            assert "请提取课程重点" in prompt.prompts
        finally:
            prompt_session.close()

        item_id = batch["items"][0]["itemId"]
        response = client.patch(
            f"/api/imports/{batch['importBatchId']}/items/{item_id}",
            json={"decision": "APPROVED"},
        )
        assert response.status_code == 200
        response = client.post(f"/api/imports/{batch['importBatchId']}/confirm")
        assert response.status_code == 200
        assert response.json()["status"] == "COMPLETED"
        traces = client.get("/api/knowledge-traces")
        assert traces.status_code == 200
        assert traces.json()[0]["knowledgeTrace"] == "北京人"

        rejected = client.post(
            "/api/imports",
            data={"text": "unsupported"},
            files={"files": ("unsafe.png", b"not an image", "image/png")},
        )
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "FAILED"
        assert "暂不支持" in rejected.json()["errorMessage"]
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
