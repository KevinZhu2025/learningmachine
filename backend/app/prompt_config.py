from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import String, cast, desc, select, update
from sqlalchemy.orm import Session

from .db import get_session
from .models import PromptLeading
from .schemas import to_camel


class PromptValues(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    subject_id: str = Field(min_length=1, max_length=100)
    prompts: str = Field(min_length=1)
    user_id: str | None = Field(default=None, max_length=100)
    creation_time: datetime | None = None

    @field_validator("subject_id", "prompts")
    @classmethod
    def validate_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("课程和提示词不能为空。")
        return value

    @field_validator("creation_time")
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value


class PromptOut(PromptValues):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)

    prompt_leading_id: int
    creation_time: datetime


class PromptEdit(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    prompt_leading_id: int | None = Field(default=None, gt=0)
    original: PromptValues | None = None
    values: PromptValues


class PromptSave(BaseModel):
    rows: list[PromptEdit] = Field(min_length=1, max_length=500)


def create_prompt_router(current_user):
    router = APIRouter(prefix="/api/prompt-config", dependencies=[Depends(current_user)])

    @router.get("/options")
    def options(session: Session = Depends(get_session)):
        subjects = session.scalars(select(PromptLeading.subject_id).distinct().order_by(PromptLeading.subject_id)).all()
        users = session.scalars(
            select(PromptLeading.user_id).where(PromptLeading.user_id.is_not(None))
            .distinct().order_by(PromptLeading.user_id)
        ).all()
        return {"subjects": subjects, "users": users}

    @router.get("", response_model=list[PromptOut])
    def list_prompts(
        subject_id: Annotated[str, Query(alias="subjectId", max_length=100)] = "",
        user_id: Annotated[str, Query(alias="userId", max_length=100)] = "",
        session: Session = Depends(get_session),
    ):
        query = select(PromptLeading)
        if subject_id.strip():
            query = query.where(PromptLeading.subject_id == subject_id.strip())
        if user_id.strip():
            query = query.where(PromptLeading.user_id == user_id.strip())
        return session.scalars(
            query.order_by(desc(PromptLeading.creation_time), desc(PromptLeading.prompt_leading_id))
        ).all()

    @router.post("")
    def save_prompts(payload: PromptSave, session: Session = Depends(get_session)):
        seen: set[int] = set()
        for row in payload.rows:
            values = row.values.model_dump()
            if values["user_id"] is not None and not values["user_id"].strip():
                values["user_id"] = None
            if row.prompt_leading_id is None:
                if row.original is not None:
                    raise HTTPException(422, "新增行不能包含原始记录。")
                if values["creation_time"] is None:
                    values.pop("creation_time")
                session.add(PromptLeading(**values))
                continue
            if row.prompt_leading_id in seen:
                raise HTTPException(422, "同一记录不能重复提交。")
            seen.add(row.prompt_leading_id)
            if row.original is None or row.original.creation_time is None or row.values.creation_time is None:
                raise HTTPException(422, "修改现有记录时必须提供原始记录和创建时间。")
            result = session.execute(
                update(PromptLeading)
                .where(
                    PromptLeading.prompt_leading_id == row.prompt_leading_id,
                    PromptLeading.subject_id == row.original.subject_id,
                    PromptLeading.prompts == row.original.prompts,
                    PromptLeading.user_id == row.original.user_id,
                    cast(PromptLeading.creation_time, String).in_([
                        row.original.creation_time.isoformat(sep=" "),
                        row.original.creation_time.strftime("%Y-%m-%d %H:%M:%S.%f"),
                    ]),
                )
                .values(**values)
            )
            if result.rowcount != 1:
                session.rollback()
                raise HTTPException(409, "记录已被修改或删除，未保存任何更改；请重新搜索后重试。")
        session.commit()
        return {"saved": len(payload.rows)}

    return router
