from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    JSON,
    Index,
    LargeBinary,
    String,
    Text,
    Unicode,
    UnicodeText,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, allocate_knowledge_id


def new_id() -> str:
    return str(uuid4())


def item_owner(context) -> str:
    batch_id = context.get_current_parameters()["ImportBatchID"]
    owner = context.connection.execute(
        select(ImportBatch.user_id).where(ImportBatch.import_batch_id == batch_id)
    ).scalar_one()
    return owner


def reserved_knowledge_id(context) -> str | None:
    if context.get_current_parameters().get("KnowledgeTitle") is None:
        return None
    return allocate_knowledge_id(context.connection)


STAGING_SHAPE = """CASE
    WHEN KnowledgeID IS NOT NULL AND length(trim(KnowledgeID)) > 0
     AND KnowledgeTitle IS NOT NULL AND length(trim(KnowledgeTitle)) > 0
     AND CanonicalContent IS NOT NULL AND length(trim(CanonicalContent)) > 0
     AND Decision IS NOT NULL AND length(trim(Decision)) > 0
     AND SourceExcerpt IS NOT NULL AND length(trim(SourceExcerpt)) > 0 THEN 1
    WHEN KnowledgeID IS NULL AND KnowledgeTitle IS NULL AND CanonicalContent IS NULL
     AND Decision IS NULL AND Confidence IS NULL AND SourceExcerpt IS NULL AND SourceLocation IS NULL
     AND SubjectID IS NULL AND ChapterID IS NULL
     AND length(trim(json_extract(QuestionItemsJson, '$[0].QuestionText'))) > 0 THEN 1
    ELSE 0 END = 1"""


class KnowledgeIDCounter(Base):
    __tablename__ = "KnowledgeIDCounter"
    counter_id: Mapped[int] = mapped_column("CounterID", Integer, primary_key=True)
    last_value: Mapped[int] = mapped_column("LastValue", BigInteger, nullable=False)


class UserRole(Base):
    __tablename__ = "UserRole"

    role: Mapped[str] = mapped_column("Role", String(36), primary_key=True)
    class_hour_threshold: Mapped[int] = mapped_column(
        "Class_Hour_Threshold", Integer, nullable=False, default=0, server_default="0"
    )
    medal_count: Mapped[int] = mapped_column(
        "Medal_Count", Integer, nullable=False, default=0, server_default="0"
    )
    medal_type: Mapped[str] = mapped_column("Medal_Type", String(36), nullable=False)
    medal_image: Mapped[bytes | None] = mapped_column("Medal_Image", LargeBinary)


class UserProfile(Base):
    __tablename__ = "UserProfile"

    user_id: Mapped[str] = mapped_column("UserID", Unicode(100), primary_key=True)
    password_hash: Mapped[str] = mapped_column("PasswordHash", String(512), nullable=False)
    role: Mapped[str | None] = mapped_column("Role", String(50))
    class_hour: Mapped[int | None] = mapped_column("Class_Hour", Integer)


class AuthSession(Base):
    __tablename__ = "AuthSession"

    session_token_hash: Mapped[str] = mapped_column("SessionTokenHash", String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        "UserID", ForeignKey("UserProfile.UserID"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        "ExpiresAt", DateTime, nullable=False, index=True
    )


class AuthThrottle(Base):
    __tablename__ = "AuthThrottle"
    __table_args__ = (
        CheckConstraint("AttemptCount > 0", name="CK_AuthThrottle_AttemptCount"),
    )

    rate_limit_key: Mapped[str] = mapped_column("RateLimitKey", String(64), primary_key=True)
    attempt_count: Mapped[int] = mapped_column("AttemptCount", Integer, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(
        "WindowStartedAt", DateTime(timezone=True), nullable=False
    )


class KnowledgeBase(Base):
    __tablename__ = "KnowledgeBase"
    __table_args__ = (
        CheckConstraint("Version >= 0", name="CK_KnowledgeBase_Version_NonNegative"),
        Index(
            "UX_KnowledgeBase_ItemID",
            "ItemID",
            unique=True,
            mssql_where=text("ItemID IS NOT NULL"),
            sqlite_where=text("ItemID IS NOT NULL"),
        ),
    )

    knowledge_id: Mapped[str] = mapped_column("KnowledgeID", String(50), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column("KnowledgeTitle", String(255), nullable=False)
    canonical_content: Mapped[str] = mapped_column("CanonicalContent", Text, nullable=False)
    subject_id: Mapped[str | None] = mapped_column("SubjectID", String(100))
    chapter_id: Mapped[str | None] = mapped_column("ChapterID", String(255))
    tags: Mapped[str | None] = mapped_column("Tags", Text)
    exam_heat: Mapped[int] = mapped_column("ExamHeat", Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column("Version", Integer, nullable=False, default=0, server_default="0")
    user_id: Mapped[str] = mapped_column("UserID", String(100), nullable=False, index=True)
    import_batch_id: Mapped[str | None] = mapped_column(
        "ImportBatchID", ForeignKey("ImportBatch.ImportBatchID"), index=True
    )
    item_id: Mapped[str | None] = mapped_column(
        "ItemID", ForeignKey("ImportItem.ItemID"), index=True
    )


class PromptLeading(Base):
    __tablename__ = "PromptLeading"

    prompt_leading_id: Mapped[str] = mapped_column(
        "Prompts_SN", String(36), primary_key=True, default=new_id
    )
    subject_id: Mapped[str] = mapped_column("SubjectID", Unicode(100), nullable=False, index=True)
    prompts: Mapped[str] = mapped_column("Prompts", UnicodeText, nullable=False)
    user_id: Mapped[str | None] = mapped_column("UserID", Unicode(100), index=True)
    creation_time: Mapped[datetime] = mapped_column(
        "Creation_Time", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp(), index=True
    )


class ImportBatch(Base):
    __tablename__ = "ImportBatch"
    __table_args__ = (
        UniqueConstraint("ImportBatchID", "UserID", name="UQ_ImportBatch_Batch_User"),
        Index(
            "UX_ImportBatch_User_Idempotency",
            "UserID",
            "IdempotencyKey",
            unique=True,
            mssql_where=text("IdempotencyKey IS NOT NULL"),
            sqlite_where=text("IdempotencyKey IS NOT NULL"),
        ),
    )

    import_batch_id: Mapped[str] = mapped_column("ImportBatchID", String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column("UserID", Unicode(100), nullable=False, index=True)
    idempotency_key: Mapped[str | None] = mapped_column("IdempotencyKey", String(128))
    request_fingerprint: Mapped[str | None] = mapped_column("RequestFingerprint", String(64))
    source_name: Mapped[str] = mapped_column("SourceName", Unicode(255), nullable=False)
    source_type: Mapped[str] = mapped_column("SourceType", String(20), nullable=False)
    subject_id: Mapped[str | None] = mapped_column("SubjectID", String(100))
    user_comments: Mapped[str | None] = mapped_column("UserComments", UnicodeText)
    status: Mapped[str] = mapped_column("Status", String(20), nullable=False, index=True)
    error_message: Mapped[str | None] = mapped_column("ErrorMessage", Text)
    is_preview: Mapped[bool] = mapped_column("IsPreview", Boolean, nullable=False, default=True)
    prompt_snapshot: Mapped[str | None] = mapped_column("PromptSnapshot", UnicodeText)
    prompt_snapshot_source: Mapped[str | None] = mapped_column("PromptSnapshotSource", String(50))
    prompt_snapshot_ids: Mapped[str | None] = mapped_column("PromptSnapshotIds", Text)
    prompt_history_snapshot: Mapped[list[dict[str, Any]] | None] = mapped_column("PromptHistorySnapshotJson", JSON(none_as_null=True))
    prompt_snapshot_version: Mapped[int] = mapped_column("PromptSnapshotVersion", Integer, nullable=False, default=1, server_default="1")
    model_request: Mapped[dict[str, Any] | None] = mapped_column("ModelRequestJson", JSON(none_as_null=True))
    model_request_object_key: Mapped[str | None] = mapped_column("ModelRequestObjectKey", String(512))
    model_response_object_key: Mapped[str | None] = mapped_column("ModelResponseObjectKey", String(512))
    audit_container_name: Mapped[str | None] = mapped_column("AuditContainerName", String(63))
    created_at: Mapped[datetime] = mapped_column(
        "CreatedAt", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp(), index=True
    )

    items: Mapped[list["ImportItem"]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="ImportItem.item_id",
        foreign_keys="ImportItem.import_batch_id",
        primaryjoin="ImportBatch.import_batch_id == ImportItem.import_batch_id",
    )
    sources: Mapped[list["ImportSource"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", order_by="ImportSource.source_id"
    )


class ImportSource(Base):
    __tablename__ = "ImportSource"
    __table_args__ = (
        UniqueConstraint("ImportBatchID", "SourceID", name="UQ_ImportSource_Batch_Source"),
        UniqueConstraint("ContainerName", "SourceObjectKey", name="UQ_ImportSource_Container_ObjectKey"),
    )

    source_id: Mapped[str] = mapped_column("SourceID", String(36), primary_key=True, default=new_id)
    import_batch_id: Mapped[str] = mapped_column(
        "ImportBatchID", ForeignKey("ImportBatch.ImportBatchID"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column("SourceType", String(20), nullable=False, default="BLOB")
    source_name: Mapped[str] = mapped_column("SourceName", Unicode(255), nullable=False)
    source_url: Mapped[str | None] = mapped_column("SourceURL", String(2048))
    container_name: Mapped[str] = mapped_column("ContainerName", String(63), nullable=False)
    source_object_key: Mapped[str] = mapped_column("SourceObjectKey", String(512), nullable=False)
    source_file_name: Mapped[str] = mapped_column("SourceFileName", Unicode(255), nullable=False)
    source_content_type: Mapped[str | None] = mapped_column("SourceContentType", String(255))
    source_size_bytes: Mapped[int | None] = mapped_column("SourceSizeBytes", BigInteger)
    source_sha256: Mapped[str | None] = mapped_column("SourceSHA256", String(64))
    extracted_segments: Mapped[list[dict[str, Any]] | None] = mapped_column("ExtractedSegmentsJson", JSON(none_as_null=True))
    extraction_version: Mapped[int] = mapped_column("ExtractionVersion", Integer, nullable=False, default=1, server_default="1")
    ocr_result_object_key: Mapped[str | None] = mapped_column("OcrResultObjectKey", String(512))
    ocr_result_container_name: Mapped[str | None] = mapped_column("OcrResultContainerName", String(63))
    created_at: Mapped[datetime] = mapped_column(
        "CreatedAt", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )

    batch: Mapped[ImportBatch] = relationship(back_populates="sources")
    items: Mapped[list["ImportItem"]] = relationship(
        back_populates="source",
        primaryjoin=(
            "and_(ImportSource.import_batch_id == ImportItem.import_batch_id, "
            "ImportSource.source_id == ImportItem.source_id)"
        ),
        foreign_keys="ImportItem.source_id",
    )


class ImportItem(Base):
    __tablename__ = "ImportItem"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ImportBatchID", "UserID"], ["ImportBatch.ImportBatchID", "ImportBatch.UserID"],
            name="FK_ImportItem_Batch_User",
        ),
        CheckConstraint("Version >= 0", name="CK_ImportItem_Version_NonNegative"),
        CheckConstraint("Confidence IS NULL OR (Confidence >= 0 AND Confidence <= 1)", name="CK_ImportItem_Confidence"),
        CheckConstraint("length(trim(SourceType)) > 0", name="CK_ImportItem_SourceType_NotBlank").ddl_if(dialect="sqlite"),
        CheckConstraint("json_valid(QuestionItemsJson) AND json_type(QuestionItemsJson) = 'array'", name="CK_ImportItem_QuestionItems_Array").ddl_if(dialect="sqlite"),
        CheckConstraint("AssociatedImage IS NULL OR (json_valid(AssociatedImage) AND json_type(AssociatedImage) = 'object')", name="CK_ImportItem_AssociatedImage_Object").ddl_if(dialect="sqlite"),
        CheckConstraint("OptionsJson IS NULL OR (json_valid(OptionsJson) AND json_type(OptionsJson) = 'array')", name="CK_ImportItem_Options_Array").ddl_if(dialect="sqlite"),
        CheckConstraint("AIProposal IS NULL OR json_valid(AIProposal)", name="CK_ImportItem_AIProposal_Json").ddl_if(dialect="sqlite"),
        CheckConstraint(STAGING_SHAPE, name="CK_ImportItem_Staging_Shape").ddl_if(dialect="sqlite"),
        Index("UX_ImportItem_ReservedKnowledgeID", "KnowledgeID", unique=True, sqlite_where=text("KnowledgeID IS NOT NULL"), mssql_where=text("KnowledgeID IS NOT NULL")),
        ForeignKeyConstraint(
            ["ImportBatchID", "SourceID"],
            ["ImportSource.ImportBatchID", "ImportSource.SourceID"],
            name="FK_ImportItem_ImportSource",
        ),
    )

    item_id: Mapped[str] = mapped_column("ItemID", String(36), primary_key=True, default=new_id)
    import_batch_id: Mapped[str] = mapped_column(
        "ImportBatchID", ForeignKey("ImportBatch.ImportBatchID"), nullable=False, index=True
    )
    source_id: Mapped[str | None] = mapped_column("SourceID", String(36), nullable=False, index=True)
    knowledge_id: Mapped[str | None] = mapped_column("KnowledgeID", String(50), default=reserved_knowledge_id)
    knowledge_title: Mapped[str | None] = mapped_column("KnowledgeTitle", Unicode(255))
    canonical_content: Mapped[str | None] = mapped_column("CanonicalContent", UnicodeText)
    decision: Mapped[str | None] = mapped_column("Decision", String(20))
    confidence: Mapped[float | None] = mapped_column("Confidence", Float)
    source_excerpt: Mapped[str | None] = mapped_column("SourceExcerpt", UnicodeText)
    source_location: Mapped[str | None] = mapped_column("SourceLocation", Unicode(255))
    source_type: Mapped[str] = mapped_column("SourceType", Unicode(50), nullable=False, default="UNKNOWN")
    user_id: Mapped[str] = mapped_column("UserID", Unicode(100), nullable=False, default=item_owner)
    version: Mapped[int] = mapped_column("Version", Integer, nullable=False, default=0, server_default="0")
    associated_image: Mapped[dict[str, Any] | None] = mapped_column("AssociatedImage", JSON(none_as_null=True))
    question_text: Mapped[str | None] = mapped_column("QuestionText", UnicodeText)
    question_type: Mapped[str | None] = mapped_column("QuestionType", Unicode(100))
    options_json: Mapped[list[dict[str, Any]] | None] = mapped_column("OptionsJson", JSON(none_as_null=True))
    correct_answer: Mapped[str | None] = mapped_column("CorrectAnswer", UnicodeText)
    explanation: Mapped[str | None] = mapped_column("Explanation", UnicodeText)
    difficulty: Mapped[str | None] = mapped_column("Difficulty", Unicode(100))
    question_items_json: Mapped[list[dict[str, Any]]] = mapped_column(
        "QuestionItemsJson", JSON(none_as_null=True), nullable=False, default=list, server_default=text("'[]'")
    )
    ai_request_id: Mapped[str | None] = mapped_column("AIRequestID", String(36))
    ai_response_version: Mapped[str | None] = mapped_column("AIResponseVersion", String(50))
    ai_proposal: Mapped[dict[str, Any] | None] = mapped_column("AIProposal", JSON(none_as_null=True))
    subject_id: Mapped[str | None] = mapped_column("SubjectID", String(100))
    chapter_id: Mapped[str | None] = mapped_column("ChapterID", String(255))
    human_approval: Mapped[str] = mapped_column("HumanApproval", String(20), nullable=False, default="PENDING")
    version_no: Mapped[int] = mapped_column(
        "VersionNo", Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        "CreatedAt", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )

    batch: Mapped[ImportBatch] = relationship(
        back_populates="items", foreign_keys=[import_batch_id],
        primaryjoin="ImportItem.import_batch_id == ImportBatch.import_batch_id",
    )
    source: Mapped[ImportSource] = relationship(
        back_populates="items",
        primaryjoin=(
            "and_(ImportSource.import_batch_id == ImportItem.import_batch_id, "
            "ImportSource.source_id == ImportItem.source_id)"
        ),
        foreign_keys=[source_id],
    )


class QuestionBank(Base):
    __tablename__ = "QuestionBank"

    question_id: Mapped[int] = mapped_column("QuestionID", BigInteger, primary_key=True, autoincrement=True)
    question_text: Mapped[str] = mapped_column("QuestionText", Text, nullable=False)
    question_type: Mapped[str | None] = mapped_column("QuestionType", String(30))
    options_json: Mapped[str | None] = mapped_column("OptionsJson", Text)
    correct_answer: Mapped[str | None] = mapped_column("CorrectAnswer", Text)
    explanation: Mapped[str | None] = mapped_column("Explanation", Text)
    difficulty: Mapped[str | None] = mapped_column("Difficulty", String(50))
    subject_id: Mapped[str | None] = mapped_column("SubjectID", String(100))
    chapter_id: Mapped[str | None] = mapped_column("ChapterID", String(255))
    knowledge_id: Mapped[str | None] = mapped_column(
        "KnowledgeID", ForeignKey("KnowledgeBase.KnowledgeID"), index=True
    )
    knowledge_content: Mapped[str | None] = mapped_column("KnowledgeContent", Text)
    human_approval: Mapped[bool | None] = mapped_column("HumanApproval", Boolean)
    occurrence_count: Mapped[int] = mapped_column("OccurrenceCount", Integer, nullable=False, default=1)
    user_id: Mapped[str] = mapped_column("UserID", String(100), nullable=False)
    import_batch_id: Mapped[str | None] = mapped_column(
        "ImportBatchID", ForeignKey("ImportBatch.ImportBatchID"), index=True
    )
    item_id: Mapped[str | None] = mapped_column(
        "ItemID", ForeignKey("ImportItem.ItemID"), index=True
    )
