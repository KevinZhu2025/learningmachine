import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from app.db import migrate_legacy_import_schema


def test_fresh_staging_schema_matches_cloud_columns(tmp_path):
    from app.db import Base
    from app import models

    engine = create_engine(f"sqlite:///{tmp_path / 'staging.db'}")
    try:
        Base.metadata.create_all(engine)
        inspector = inspect(engine)
        columns = {column["name"]: column for column in inspector.get_columns("ImportItem")}
        assert {
            "SourceType", "UserID", "Version", "AssociatedImage", "QuestionText",
            "QuestionType", "OptionsJson", "CorrectAnswer", "Explanation", "Difficulty",
            "QuestionItemsJson", "AIRequestID", "AIResponseVersion", "AIProposal",
        } <= columns.keys()
        for name in (
            "KnowledgeTitle", "CanonicalContent", "Decision", "Confidence",
            "SourceExcerpt", "SourceLocation",
        ):
            assert columns[name]["nullable"]
        assert "Version" in {column["name"] for column in inspector.get_columns("KnowledgeBase")}
    finally:
        engine.dispose()


def test_empty_legacy_without_source_column_is_rebuilt(tmp_path):
    from app.db import Base
    from app import models

    engine = create_engine(f"sqlite:///{tmp_path / 'no-source.db'}")
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql('DROP TABLE "ImportItem"')
            connection.exec_driver_sql('CREATE TABLE "ImportItem" ("ItemID" VARCHAR(36) PRIMARY KEY, "ImportBatchID" VARCHAR(36))')
        migrate_legacy_import_schema(engine)
        columns = {column["name"]: column for column in inspect(engine).get_columns("ImportItem")}
        assert "SourceID" in columns and not columns["SourceID"]["nullable"]
        assert "QuestionItemsJson" in columns and not columns["QuestionItemsJson"]["nullable"]
        assert columns["KnowledgeTitle"]["nullable"]
    finally:
        engine.dispose()


def test_staging_migration_preserves_rows_and_incoming_knowledge_links(tmp_path):
    from app.db import Base
    from app.models import ImportBatch, ImportSource, KnowledgeBase
    from sqlalchemy.orm import Session

    engine = create_engine(f"sqlite:///{tmp_path / 'populated.db'}")
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql('DROP TABLE "ImportItem"')
            connection.exec_driver_sql(
                'CREATE TABLE "ImportItem" ("ItemID" VARCHAR(36) PRIMARY KEY, '
                '"ImportBatchID" VARCHAR(36) NOT NULL, "SourceID" VARCHAR(36) NOT NULL, '
                '"KnowledgeID" VARCHAR(50), "KnowledgeTitle" VARCHAR(255) NOT NULL, '
                '"CanonicalContent" TEXT NOT NULL, "Decision" VARCHAR(20) NOT NULL, '
                '"Confidence" FLOAT NOT NULL, "SourceExcerpt" TEXT NOT NULL, '
                '"SourceLocation" VARCHAR(255) NOT NULL, "HumanApproval" VARCHAR(20) NOT NULL DEFAULT \'PENDING\', '
                '"CreatedAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)'
            )
        with Session(engine) as session:
            session.add(ImportBatch(import_batch_id="batch-old", user_id="learner", source_name="old", source_type="BLOB", status="NEEDS_REVIEW"))
            session.flush()
            session.add(ImportSource(source_id="source-old", import_batch_id="batch-old", source_name="old", container_name="local", source_object_key="old", source_file_name="old.txt"))
            session.commit()
        with engine.begin() as connection:
            connection.exec_driver_sql(
                'INSERT INTO "ImportItem" ("ItemID", "ImportBatchID", "SourceID", "KnowledgeTitle", "CanonicalContent", "Decision", "Confidence", "SourceExcerpt", "SourceLocation") '
                "VALUES ('item-old', 'batch-old', 'source-old', 'old title', 'old content', 'REVIEW', 0.8, 'old evidence', 'page 1')"
            )
        with Session(engine) as session:
            session.add(KnowledgeBase(knowledge_id="50", title="formal", canonical_content="preserved", user_id="learner", import_batch_id="batch-old", item_id="item-old"))
            session.commit()
        migrate_legacy_import_schema(engine)
        migrate_legacy_import_schema(engine)
        with engine.connect() as connection:
            assert connection.exec_driver_sql('SELECT "UserID", "SourceType", "KnowledgeID", "QuestionItemsJson", "Version" FROM "ImportItem"').one() == ("learner", "UNKNOWN", "51", "[]", 0)
            assert connection.exec_driver_sql('SELECT "ItemID" FROM "KnowledgeBase" WHERE "KnowledgeID" = \'50\'').scalar_one() == "item-old"
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    finally:
        engine.dispose()


def test_legacy_import_schema_migration_drops_trace_and_batch_object_key(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                'CREATE TABLE "ImportBatch" '
                '("ImportBatchID" VARCHAR(36) PRIMARY KEY, "UserID" VARCHAR(100) NOT NULL, '
                '"SourceObjectKey" VARCHAR(512))'
            )
            connection.exec_driver_sql(
                'INSERT INTO "ImportBatch" VALUES '
                '(\'batch-1\', \'learner\', \'legacy-object-key\')'
            )
            connection.exec_driver_sql(
                'CREATE TABLE "KnowledgeTrace" ("TraceID" VARCHAR(36) PRIMARY KEY)'
            )
            connection.exec_driver_sql(
                'INSERT INTO "KnowledgeTrace" VALUES (\'trace-1\')'
            )

        migrate_legacy_import_schema(engine)

        inspector = inspect(engine)
        assert "KnowledgeTrace" not in inspector.get_table_names()
        assert "SourceObjectKey" not in {
            column["name"] for column in inspector.get_columns("ImportBatch")
        }
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                'SELECT "ImportBatchID", "UserID" FROM "ImportBatch"'
            ).one() == ("batch-1", "learner")
            columns = {
                column["name"] for column in inspect(connection).get_columns("ImportBatch")
            }
            assert {"IdempotencyKey", "RequestFingerprint"} <= columns
        migrate_legacy_import_schema(engine)
    finally:
        engine.dispose()


def test_legacy_import_migration_refuses_incoming_trace_foreign_keys(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'dependent.db'}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                'CREATE TABLE "ImportBatch" '
                '("ImportBatchID" VARCHAR(36) PRIMARY KEY, "UserID" VARCHAR(100) NOT NULL, '
                '"SourceObjectKey" VARCHAR(512))'
            )
            connection.exec_driver_sql(
                'CREATE TABLE "KnowledgeTrace" ("TraceID" VARCHAR(36) PRIMARY KEY)'
            )
            connection.exec_driver_sql(
                'CREATE TABLE "LegacyLink" '
                '("TraceID" VARCHAR(36) REFERENCES "KnowledgeTrace" ("TraceID"))'
            )

        with pytest.raises(RuntimeError, match="引用它的外键"):
            migrate_legacy_import_schema(engine)

        inspector = inspect(engine)
        assert "KnowledgeTrace" in inspector.get_table_names()
        assert "SourceObjectKey" in {
            column["name"] for column in inspector.get_columns("ImportBatch")
        }
    finally:
        engine.dispose()


def test_legacy_import_item_migration_adds_review_fields_and_unique_knowledge_link(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'review-migration.db'}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                'CREATE TABLE "ImportItem" ("ItemID" VARCHAR(36) PRIMARY KEY)'
            )
            connection.exec_driver_sql(
                'CREATE TABLE "KnowledgeBase" '
                '("KnowledgeID" VARCHAR(50) PRIMARY KEY, "ItemID" VARCHAR(36))'
            )

        migrate_legacy_import_schema(engine)
        inspector = inspect(engine)
        item_columns = {column["name"] for column in inspector.get_columns("ImportItem")}
        assert {"SubjectID", "ChapterID", "VersionNo"} <= item_columns
        assert {
            index["name"] for index in inspector.get_indexes("KnowledgeBase")
        } >= {"UX_KnowledgeBase_ItemID"}

        with engine.connect() as connection:
            connection.exec_driver_sql(
                'INSERT INTO "KnowledgeBase" ("KnowledgeID", "ItemID") VALUES '
                "('knowledge-1', 'item-1')"
            )
            with pytest.raises(IntegrityError):
                connection.exec_driver_sql(
                    'INSERT INTO "KnowledgeBase" ("KnowledgeID", "ItemID") VALUES '
                    "('knowledge-2', 'item-1')"
                )
        migrate_legacy_import_schema(engine)
    finally:
        engine.dispose()
