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


def test_database_url_prefers_explicit_connection_string(monkeypatch):
    from sqlalchemy.engine import make_url
    from app.db import _database_url

    explicit = "mssql+pyodbc://app:secret@rm-example.sqlserver.rds.aliyuncs.com:3433/LearningMachineDB?driver=ODBC+Driver+18"
    monkeypatch.setenv("DATABASE_URL", explicit)
    monkeypatch.delenv("SQL_SERVER", raising=False)
    url = make_url(_database_url())
    assert url.host == "rm-example.sqlserver.rds.aliyuncs.com"
    assert url.port == 3433
    assert url.database == "LearningMachineDB"


def test_database_url_builds_from_sql_server_parts(monkeypatch):
    from sqlalchemy.engine import make_url
    from app.db import _database_url

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SQL_SERVER", "rm-example.sqlserver.rds.aliyuncs.com")
    monkeypatch.setenv("SQL_SERVER_PORT", "3433")
    monkeypatch.setenv("SQL_SERVER_USER", "app")
    monkeypatch.setenv("SQL_SERVER_PASSWORD", "secret")
    monkeypatch.setenv("DATABASE_NAME", "LearningMachineDB")
    url = make_url(_database_url())
    assert url.drivername == "mssql+pyodbc"
    assert url.host == "rm-example.sqlserver.rds.aliyuncs.com"
    assert url.port == 3433
    assert url.database == "LearningMachineDB"
    assert url.query["TrustServerCertificate"] == "yes"


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
