from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ImportItemOut(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        alias_generator=to_camel,
    )

    item_id: str
    knowledge_title: str | None
    canonical_content: str | None
    decision: str | None
    confidence: float | None
    source_excerpt: str | None
    source_location: str | None
    source_type: str
    user_id: str
    version: int
    associated_image: dict[str, Any] | None
    question_text: str | None
    question_type: str | None
    options_json: list[dict[str, Any]] | None
    correct_answer: str | None
    explanation: str | None
    difficulty: str | None
    question_items_json: list[dict[str, Any]]
    ai_request_id: str | None
    ai_response_version: str | None
    ai_proposal: dict[str, Any] | None
    subject_id: str | None
    chapter_id: str | None
    human_approval: str
    knowledge_id: str | None
    source_id: str | None
    created_at: datetime
    version_no: int


class ImportSourceOut(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        alias_generator=to_camel,
    )

    source_id: str
    source_type: str
    source_name: str
    source_url: str | None
    source_file_name: str | None
    source_content_type: str | None
    source_size_bytes: int | None


class ImportOut(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        alias_generator=to_camel,
    )

    import_batch_id: str
    source_name: str
    source_type: str
    subject_id: str | None
    status: str
    created_at: datetime
    user_comments: str | None
    is_preview: bool
    error_message: str | None
    items: list[ImportItemOut]
    sources: list[ImportSourceOut]


class ItemDecisionIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel, extra="forbid")

    expected_version: int = Field(ge=1)
    decision: str = Field(default="PENDING", pattern="^(PENDING|APPROVED|REJECTED)$")
    knowledge_title: str | None = Field(default=None, min_length=1, max_length=255)
    subject_id: str | None = Field(default=None, max_length=100)
    chapter_id: str | None = Field(default=None, max_length=255)
    canonical_content: str | None = Field(default=None, min_length=1, max_length=12000)
    question_items_json: list[dict[str, Any]] | None = None

    @field_validator("question_items_json")
    @classmethod
    def validate_questions(cls, value):
        if value is None:
            raise ValueError("题目数组不能为 null；无题目使用 []。")
        for question in value:
            question_text = question.get("QuestionText")
            if not isinstance(question_text, str) or not question_text.strip():
                raise ValueError("每道题必须包含非空 QuestionText。")
        return value
