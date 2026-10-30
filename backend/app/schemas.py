from datetime import datetime
from pydantic import BaseModel, ConfigDict


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
    knowledge_title: str
    canonical_content: str
    decision: str
    confidence: float
    source_excerpt: str
    source_location: str
    human_approval: str
    knowledge_id: str | None


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


class ItemDecisionIn(BaseModel):
    decision: str
