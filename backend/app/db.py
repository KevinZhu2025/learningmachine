import os
import struct
from pathlib import Path

from sqlalchemy import MetaData, create_engine, event, inspect
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.schema import CreateIndex, CreateTable


@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class Base(DeclarativeBase):
    pass


def _database_url():
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    if os.getenv("AZURE_SQL_SERVER"):
        return URL.create("mssql+pyodbc", host=os.environ["AZURE_SQL_SERVER"], database=os.getenv("AZURE_SQL_DATABASE", "MEMPALACE"), query={"driver": "ODBC Driver 18 for SQL Server", "Encrypt": "yes", "TrustServerCertificate": "no", "LongAsMax": "yes"})
    return "sqlite:///./.data/learningmachine-dev.db"


def make_engine(database_url: str | None = None):
    url = database_url or _database_url()
    parsed = make_url(url)
    if isinstance(url, str) and url.startswith("sqlite:///"):
        local_path = url.removeprefix("sqlite:///")
        if local_path != ":memory:" and not local_path.startswith("/"):
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    options = {"check_same_thread": False} if parsed.drivername.startswith("sqlite") else {}
    target = create_engine(url, connect_args=options, pool_pre_ping=True)
    if parsed.drivername.startswith("mssql"):
        target = target.execution_options(schema_translate_map={None: "dbo"})
        use_identity = os.getenv("AZURE_SQL_AUTH") == "default" or (not os.getenv("DATABASE_URL") and bool(os.getenv("AZURE_SQL_SERVER")))
        if use_identity:
            from azure.identity import DefaultAzureCredential
            credential = DefaultAzureCredential(exclude_interactive_browser_credential=True, process_timeout=60)

            @event.listens_for(target, "do_connect")
            def sql_access_token(dialect, connection_record, arguments, keyword_arguments):
                arguments[0] = arguments[0].replace(";Trusted_Connection=Yes", "").replace(";Trusted_Connection=yes", "")
                token = credential.get_token("https://database.windows.net/.default").token.encode("utf-16-le")
                keyword_arguments.setdefault("attrs_before", {})[1256] = struct.pack("<I", len(token)) + token
    return target


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def allocate_knowledge_id(connection) -> str:
    if connection.dialect.name == "mssql":
        return str(connection.exec_driver_sql("SELECT NEXT VALUE FOR dbo.KnowledgeIDSequence").scalar_one())
    if connection.dialect.name != "sqlite":
        raise RuntimeError("不支持此数据库的知识编号分配。")
    largest = 0
    for table in ("KnowledgeBase", "ImportItem"):
        value = connection.exec_driver_sql(
            f'SELECT MAX(CAST("KnowledgeID" AS INTEGER)) FROM "{table}" '
            'WHERE "KnowledgeID" <> \'\' AND "KnowledgeID" NOT GLOB \'*[^0-9]*\''
        ).scalar_one()
        largest = max(largest, value or 0)
    if largest >= 9223372036854775807:
        raise RuntimeError("知识编号范围已耗尽。")
    value = connection.exec_driver_sql(
        'INSERT INTO "KnowledgeIDCounter" ("CounterID", "LastValue") VALUES (1, ?) '
        'ON CONFLICT("CounterID") DO UPDATE SET "LastValue" = MAX("LastValue", excluded."LastValue" - 1) + 1 '
        'WHERE "LastValue" < 9223372036854775807 RETURNING "LastValue"',
        (largest + 1,),
    ).scalar_one()
    return str(value)


def _migrate_staging_schema(connection) -> None:
    from .models import ImportItem, KnowledgeIDCounter

    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    KnowledgeIDCounter.__table__.create(connection, checkfirst=True)
    if "ImportBatch" in tables:
        columns = {column["name"] for column in inspector.get_columns("ImportBatch")}
        additions = {
            "PromptHistorySnapshotJson": "TEXT", "PromptSnapshotVersion": "INTEGER NOT NULL DEFAULT 1",
            "ModelRequestJson": "TEXT", "ModelRequestObjectKey": "VARCHAR(512)",
            "ModelResponseObjectKey": "VARCHAR(512)", "AuditContainerName": "VARCHAR(63)",
        }
        for name, declaration in additions.items():
            if name not in columns:
                connection.exec_driver_sql(f'ALTER TABLE "ImportBatch" ADD COLUMN "{name}" {declaration}')
        connection.exec_driver_sql(
            'CREATE UNIQUE INDEX IF NOT EXISTS "UX_ImportBatch_Batch_User" '
            'ON "ImportBatch" ("ImportBatchID", "UserID")'
        )
    if "ImportSource" in tables:
        columns = {column["name"] for column in inspector.get_columns("ImportSource")}
        if "ExtractedSegmentsJson" not in columns:
            connection.exec_driver_sql('ALTER TABLE "ImportSource" ADD COLUMN "ExtractedSegmentsJson" TEXT')
        if "ExtractionVersion" not in columns:
            connection.exec_driver_sql('ALTER TABLE "ImportSource" ADD COLUMN "ExtractionVersion" INTEGER NOT NULL DEFAULT 1')
        for name, declaration in {"OcrResultObjectKey": "VARCHAR(512)", "OcrResultContainerName": "VARCHAR(63)"}.items():
            if name not in columns:
                connection.exec_driver_sql(f'ALTER TABLE "ImportSource" ADD COLUMN "{name}" {declaration}')
    if "KnowledgeBase" in tables:
        columns = {column["name"] for column in inspector.get_columns("KnowledgeBase")}
        if "Version" not in columns:
            connection.exec_driver_sql('ALTER TABLE "KnowledgeBase" ADD COLUMN "Version" INTEGER NOT NULL DEFAULT 0 CHECK ("Version" >= 0)')
    if "ImportItem" not in tables:
        return
    columns = {column["name"]: column for column in inspector.get_columns("ImportItem")}
    required = {"ItemID", "ImportBatchID", "SourceID", "KnowledgeID", "KnowledgeTitle", "CanonicalContent", "Decision", "Confidence", "SourceExcerpt", "SourceLocation"}
    if not required <= columns.keys():
        if connection.exec_driver_sql('SELECT COUNT(*) FROM "ImportItem"').scalar_one():
            raise RuntimeError("旧 ImportItem 缺少必要来源/知识字段，不能安全回填。")
        if not {"ImportBatch", "ImportSource", "KnowledgeBase"} <= tables:
            return
    constraints = {entry["name"] for entry in inspector.get_check_constraints("ImportItem")}
    if "CK_ImportItem_Staging_Shape" in constraints and set(ImportItem.__table__.c.keys()) <= columns.keys():
        return
    if not {"ImportBatch", "ImportSource", "KnowledgeBase"} <= tables:
        raise RuntimeError("缺少暂存迁移所需的批次、来源或知识表。")
    extra_columns = columns.keys() - set(ImportItem.__table__.c.keys())
    if extra_columns:
        raise RuntimeError("ImportItem 存在未映射字段，需人工审核: " + ", ".join(sorted(extra_columns)))
    rows = connection.exec_driver_sql('SELECT * FROM "ImportItem"').mappings().all()
    owners = dict(connection.exec_driver_sql('SELECT "ImportBatchID", "UserID" FROM "ImportBatch"').all())
    metadata = MetaData()
    for table in Base.metadata.tables.values():
        table.to_metadata(metadata)
    replacement = ImportItem.__table__.to_metadata(metadata, name="ImportItem__staging")
    connection.execute(CreateTable(replacement))
    for original in rows:
        values = dict(original)
        owner = owners.get(values["ImportBatchID"])
        if not owner or values.get("UserID", owner) != owner:
            raise RuntimeError("候选用户与所属批次不一致，迁移已回滚。")
        values["UserID"] = owner
        values.setdefault("SourceType", "UNKNOWN")
        values.setdefault("Version", 0)
        values.setdefault("QuestionItemsJson", "[]")
        if not values.get("KnowledgeID") and values.get("KnowledgeTitle"):
            values["KnowledgeID"] = allocate_knowledge_id(connection)
        names = list(values)
        sql = 'INSERT INTO "ImportItem__staging" (' + ", ".join(f'"{name}"' for name in names) + ') VALUES (' + ", ".join("?" for _ in names) + ')'
        connection.exec_driver_sql(sql, tuple(values[name] for name in names))
    saved_objects = connection.exec_driver_sql(
        "SELECT name, sql FROM sqlite_master WHERE tbl_name='ImportItem' AND type IN ('index', 'trigger') AND sql IS NOT NULL"
    ).all()
    connection.exec_driver_sql('DROP TABLE "ImportItem"')
    connection.exec_driver_sql('ALTER TABLE "ImportItem__staging" RENAME TO "ImportItem"')
    index_names = {index.name for index in ImportItem.__table__.indexes}
    for index in ImportItem.__table__.indexes:
        connection.execute(CreateIndex(index))
    for name, sql in saved_objects:
        if name not in index_names:
            connection.exec_driver_sql(sql)


def migrate_legacy_import_schema(target_engine=engine) -> None:
    if target_engine.dialect.name != "sqlite":
        raise RuntimeError("此迁移仅允许本地 SQLite；云端数据库请审核并单独执行 SQL migration。")

    with target_engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            _migrate_legacy_tables(connection)
            _migrate_staging_schema(connection)
            if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                raise RuntimeError("迁移后的外键检查失败，已回滚。")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


def _migrate_legacy_tables(connection) -> None:
    with connection.begin_nested():
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())

        if "KnowledgeTrace" in tables:
            references = []
            for table in tables:
                for foreign_key in inspector.get_foreign_keys(table):
                    if foreign_key.get("referred_table") == "KnowledgeTrace":
                        references.append(f"{table}.{foreign_key.get('name') or '<unnamed>'}")
            if references:
                raise RuntimeError(
                    "无法删除 KnowledgeTrace；先处理引用它的外键: " + ", ".join(references)
                )
            connection.exec_driver_sql('DROP TABLE "KnowledgeTrace"')

        if "ImportBatch" in tables:
            columns = {column["name"] for column in inspector.get_columns("ImportBatch")}
            if "SourceObjectKey" in columns:
                connection.exec_driver_sql(
                    'ALTER TABLE "ImportBatch" DROP COLUMN "SourceObjectKey"'
                )
            if "IdempotencyKey" not in columns:
                connection.exec_driver_sql(
                    'ALTER TABLE "ImportBatch" ADD COLUMN "IdempotencyKey" VARCHAR(128)'
                )
            if "RequestFingerprint" not in columns:
                connection.exec_driver_sql(
                    'ALTER TABLE "ImportBatch" ADD COLUMN "RequestFingerprint" VARCHAR(64)'
                )
            connection.exec_driver_sql(
                'CREATE UNIQUE INDEX IF NOT EXISTS "UX_ImportBatch_User_Idempotency" '
                'ON "ImportBatch" ("UserID", "IdempotencyKey") '
                'WHERE "IdempotencyKey" IS NOT NULL'
            )

        if "ImportItem" in tables:
            columns = {column["name"] for column in inspector.get_columns("ImportItem")}
            if "SubjectID" not in columns:
                connection.exec_driver_sql(
                    'ALTER TABLE "ImportItem" ADD COLUMN "SubjectID" VARCHAR(100)'
                )
            if "ChapterID" not in columns:
                connection.exec_driver_sql(
                    'ALTER TABLE "ImportItem" ADD COLUMN "ChapterID" VARCHAR(255)'
                )
            if "VersionNo" not in columns:
                connection.exec_driver_sql(
                    'ALTER TABLE "ImportItem" ADD COLUMN "VersionNo" INTEGER NOT NULL DEFAULT 1'
                )

        if "KnowledgeBase" in tables:
            columns = {column["name"] for column in inspector.get_columns("KnowledgeBase")}
            if "ImportBatchID" not in columns:
                connection.exec_driver_sql(
                    'ALTER TABLE "KnowledgeBase" ADD COLUMN "ImportBatchID" VARCHAR(36)'
                )
            if "ItemID" not in columns:
                connection.exec_driver_sql(
                    'ALTER TABLE "KnowledgeBase" ADD COLUMN "ItemID" VARCHAR(36)'
                )
            connection.exec_driver_sql(
                'CREATE UNIQUE INDEX IF NOT EXISTS "UX_KnowledgeBase_ItemID" '
                'ON "KnowledgeBase" ("ItemID") WHERE "ItemID" IS NOT NULL'
            )


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def initialize_database(target_engine=engine) -> None:
    if target_engine.dialect.name == "sqlite":
        migrate_legacy_import_schema(target_engine)
        Base.metadata.create_all(target_engine)
        return
    if target_engine.dialect.name != "mssql":
        raise RuntimeError("仅支持 SQLite 或 Azure SQL/SQL Server。")
    from .models import AuthSession, AuthThrottle, ImportBatch, ImportItem, ImportSource, PromptLeading, UserProfile
    try:
        with target_engine.connect() as connection:
            if connection.exec_driver_sql("SELECT DB_NAME()").scalar_one() != "MEMPALACE":
                raise RuntimeError("Azure SQL 连接目标必须是 MEMPALACE。")
            inspector = inspect(connection)
            for model in (UserProfile, AuthSession, AuthThrottle, ImportBatch, ImportSource, ImportItem, PromptLeading):
                table = model.__table__
                if not inspector.has_table(table.name, schema="dbo"):
                    raise RuntimeError(f"云端缺少 dbo.{table.name}，请先执行审核后的迁移。")
                existing = {column["name"] for column in inspector.get_columns(table.name, schema="dbo")}
                missing = set(table.c.keys()) - existing
                if missing:
                    raise RuntimeError(f"云端 {table.name} 缺少字段: " + ", ".join(sorted(missing)))
            if connection.exec_driver_sql("SELECT OBJECT_ID(N'dbo.KnowledgeIDSequence',N'SO')").scalar_one() is None:
                raise RuntimeError("云端缺少知识编号序列。")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("Azure SQL 连接或身份验证失败，请检查本机配置与数据库权限。") from exc
