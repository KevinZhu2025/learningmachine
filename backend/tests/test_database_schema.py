from app.models import (
    AuthSession,
    AuthThrottle,
    Base,
    ImportBatch,
    ImportItem,
    ImportSource,
    KnowledgeBase,
    PromptLeading,
    QuestionBank,
    UserProfile,
    UserRole,
)


def test_sql_identity_diagnostics_never_logs_token_or_claim_values(monkeypatch, caplog):
    import base64
    import json
    from app.db import _log_sql_identity_match

    monkeypatch.setenv("AZURE_SQL_EXPECTED_PRINCIPAL_ID", "expected-principal")
    monkeypatch.setenv("AZURE_SQL_EXPECTED_TENANT_ID", "expected-tenant")
    payload = base64.urlsafe_b64encode(json.dumps({"oid": "expected-principal", "tid": "different-tenant"}).encode()).decode().rstrip("=")
    token = "header." + payload + ".sensitive-signature"
    _log_sql_identity_match(token)
    assert "principal_match=True tenant_match=False" in caplog.text
    assert token not in caplog.text
    assert "expected-principal" not in caplog.text
    assert "different-tenant" not in caplog.text
    _log_sql_identity_match("invalid")
    assert "diagnostics unavailable" in caplog.text


def test_import_and_user_schema_matches_database_requirements():
    assert ImportBatch.__table__.c.ImportBatchID.primary_key
    assert "SourceObjectKey" not in ImportBatch.__table__.c
    assert ImportSource.__table__.c.SourceID.primary_key
    assert not ImportSource.__table__.c.ImportBatchID.primary_key
    assert ImportItem.__table__.c.ItemID.primary_key
    assert ImportItem.__table__.c.ImportBatchID.foreign_keys
    assert not ImportItem.__table__.c.SourceID.nullable

    assert "ImportBatchID" in KnowledgeBase.__table__.c
    assert "ItemID" in KnowledgeBase.__table__.c
    assert "AssociatedImages" not in KnowledgeBase.__table__.c
    assert "AssociatedImagines" not in KnowledgeBase.__table__.c

    assert {"ImportBatchID", "ItemID"} <= set(QuestionBank.__table__.c.keys())
    assert not {"SourceURL", "Status", "UserComments"} & set(QuestionBank.__table__.c.keys())
    assert {"Role", "Class_Hour"} <= set(UserProfile.__table__.c.keys())
    assert UserRole.__table__.c.Role.primary_key
    assert PromptLeading.__table__.c.Prompts_SN.primary_key
    assert ImportBatch.__table__ in Base.metadata.tables.values()


def test_auth_schema_stores_only_session_hashes_and_pseudonymous_throttle_keys():
    assert AuthSession.__table__.c.SessionTokenHash.primary_key
    assert AuthSession.__table__.c.UserID.foreign_keys
    assert AuthThrottle.__table__.c.RateLimitKey.primary_key
    assert any(
        constraint.name == "CK_AuthThrottle_AttemptCount"
        for constraint in AuthThrottle.__table__.constraints
    )


def test_sqlite_staging_constraints_reject_invalid_rows(tmp_path):
    import pytest
    from sqlalchemy import create_engine
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    engine = create_engine(f"sqlite:///{tmp_path / 'constraints.db'}")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(ImportBatch(import_batch_id="batch", user_id="owner", source_name="test", source_type="BLOB", status="COMPLETED"))
            session.flush()
            session.add(ImportSource(source_id="source", import_batch_id="batch", source_name="test", container_name="local", source_object_key="test", source_file_name="test.txt"))
            session.commit()
        for overrides in (
            {"user_id": "wrong-user"}, {"version": -1}, {"confidence": 1.2},
            {"question_items_json": {}}, {"options_json": {}}, {"associated_image": []},
            {"canonical_content": None}, {"source_type": " "},
        ):
            with Session(engine) as session:
                values = dict(import_batch_id="batch", source_id="source", knowledge_title="title", canonical_content="content", decision="NEW", source_excerpt="evidence")
                values.update(overrides)
                session.add(ImportItem(**values))
                with pytest.raises(IntegrityError):
                    session.commit()
        with Session(engine) as session:
            session.add(ImportItem(import_batch_id="batch", source_id="source", question_items_json=[]))
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        engine.dispose()


def test_concurrent_staging_number_allocation_is_unique(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(f"sqlite:///{tmp_path / 'numbers.db'}", connect_args={"check_same_thread": False})
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(ImportBatch(import_batch_id="batch", user_id="owner", source_name="test", source_type="BLOB", status="COMPLETED"))
            session.flush()
            session.add(ImportSource(source_id="source", import_batch_id="batch", source_name="test", container_name="local", source_object_key="test", source_file_name="test.txt"))
            session.commit()

        def reserve(number):
            with Session(engine) as session:
                item = ImportItem(import_batch_id="batch", source_id="source", user_id="owner", knowledge_title=f"title {number}", canonical_content="content", decision="NEW", source_excerpt="evidence")
                session.add(item)
                session.commit()
                return int(item.knowledge_id)

        with ThreadPoolExecutor(max_workers=4) as executor:
            numbers = list(executor.map(reserve, range(12)))
        assert set(numbers) == set(range(1, 13))
        with Session(engine) as session:
            assert session.query(KnowledgeBase).count() == 0
    finally:
        engine.dispose()
