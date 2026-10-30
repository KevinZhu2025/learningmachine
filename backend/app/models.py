from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id() -> str:
    return str(uuid4())


class KnowledgeBase(Base):
    __tablename__ = "KnowledgeBase"

    knowledge_id: Mapped[str] = mapped_column("KnowledgeID", String(50), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column("KnowledgeTitle", String(255), nullable=False)
    canonical_content: Mapped[str] = mapped_column("CanonicalContent", Text, nullable=False)
    subject_id: Mapped[str | None] = mapped_column("SubjectID", String(100))
    chapter_id: Mapped[str | None] = mapped_column("ChapterID", String(255))
    associated_imagines: Mapped[str | None] = mapped_column("AssociatedImagines", Text)
    tags: Mapped[str | None] = mapped_column("Tags", Text)
    exam_heat: Mapped[int] = mapped_column("ExamHeat", Integer, nullable=False, default=0)
    user_id: Mapped[str] = mapped_column("UserID", String(100), nullable=False, index=True)

    traces: Mapped[list["KnowledgeTrace"]] = relationship(back_populates="knowledge")


class KnowledgeTrace(Base):
    __tablename__ = "KnowledgeTrace"

    trace_id: Mapped[int] = mapped_column("TraceID", Integer, primary_key=True, autoincrement=True)
    knowledge_id: Mapped[str] = mapped_column(
        "KnowledgeID", ForeignKey("KnowledgeBase.KnowledgeID"), nullable=False, index=True
    )
    contents_added: Mapped[str | None] = mapped_column("KnowledgeContentsAdded", Text)
    change_type: Mapped[str] = mapped_column("ChangeType", String(20), nullable=False)
    knowledge_source: Mapped[str | None] = mapped_column("KnowledgeSource", String(2048))
    subject_id: Mapped[str | None] = mapped_column("SubjectID", String(100))
    chapter_id: Mapped[str | None] = mapped_column("ChapterID", String(255))
    human_approval: Mapped[bool | None] = mapped_column("HumanApproval", Boolean)
    ai_eval_returned: Mapped[str | None] = mapped_column("AI_Eval_Returned", Text)
    source_type: Mapped[str] = mapped_column("SourceType", String(20), nullable=False)
    import_batch_id: Mapped[str | None] = mapped_column("ImportBatchID", String(36), index=True)
    user_id: Mapped[str] = mapped_column("UserID", String(100), nullable=False, index=True)
    user_comments: Mapped[str | None] = mapped_column("UserComments", Text)
    created_at: Mapped[datetime] = mapped_column(
        "CreatedAt", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )

    knowledge: Mapped[KnowledgeBase] = relationship(back_populates="traces")


class PromptLeading(Base):
    __tablename__ = "PromptLeading"

    prompt_leading_id: Mapped[int] = mapped_column("PromptLeadingID", Integer, primary_key=True, autoincrement=True)
    subject_id: Mapped[str] = mapped_column("SubjectID", String(100), nullable=False, index=True)
    prompts: Mapped[str] = mapped_column("Prompts", Text, nullable=False)
    user_id: Mapped[str | None] = mapped_column("UserID", String(100), index=True)
    creation_time: Mapped[datetime] = mapped_column(
        "Creation_Time", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp(), index=True
    )


class ImportBatch(Base):
    __tablename__ = "ImportBatch"

    import_batch_id: Mapped[str] = mapped_column("ImportBatchID", String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column("UserID", String(100), nullable=False, index=True)
    source_name: Mapped[str] = mapped_column("SourceName", String(255), nullable=False)
    source_type: Mapped[str] = mapped_column("SourceType", String(20), nullable=False)
    source_object_key: Mapped[str | None] = mapped_column("SourceObjectKey", String(512))
    subject_id: Mapped[str | None] = mapped_column("SubjectID", String(100))
    user_comments: Mapped[str | None] = mapped_column("UserComments", Text)
    status: Mapped[str] = mapped_column("Status", String(20), nullable=False, index=True)
    error_message: Mapped[str | None] = mapped_column("ErrorMessage", Text)
    is_preview: Mapped[bool] = mapped_column("IsPreview", Boolean, nullable=False, default=True)
    prompt_snapshot: Mapped[str | None] = mapped_column("PromptSnapshot", Text)
    prompt_snapshot_source: Mapped[str | None] = mapped_column("PromptSnapshotSource", String(50))
    prompt_snapshot_ids: Mapped[str | None] = mapped_column("PromptSnapshotIds", Text)
    created_at: Mapped[datetime] = mapped_column(
        "CreatedAt", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp(), index=True
    )

    items: Mapped[list["ImportItem"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", order_by="ImportItem.item_id"
    )


class ImportItem(Base):
    __tablename__ = "ImportItem"

    item_id: Mapped[str] = mapped_column("ItemID", String(36), primary_key=True, default=new_id)
    import_batch_id: Mapped[str] = mapped_column(
        "ImportBatchID", ForeignKey("ImportBatch.ImportBatchID"), nullable=False, index=True
    )
    knowledge_id: Mapped[str | None] = mapped_column("KnowledgeID", String(50))
    knowledge_title: Mapped[str] = mapped_column("KnowledgeTitle", String(255), nullable=False)
    canonical_content: Mapped[str] = mapped_column("CanonicalContent", Text, nullable=False)
    decision: Mapped[str] = mapped_column("Decision", String(20), nullable=False)
    confidence: Mapped[float] = mapped_column("Confidence", Float, nullable=False)
    source_excerpt: Mapped[str] = mapped_column("SourceExcerpt", Text, nullable=False)
    source_location: Mapped[str] = mapped_column("SourceLocation", String(255), nullable=False)
    human_approval: Mapped[str] = mapped_column("HumanApproval", String(20), nullable=False, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(
        "CreatedAt", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )

    batch: Mapped[ImportBatch] = relationship(back_populates="items")
