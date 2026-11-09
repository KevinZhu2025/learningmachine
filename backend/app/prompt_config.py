from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import String, and_, cast, desc, or_, select, update
from sqlalchemy.orm import Session

from .auth import AuthenticatedUser
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

    prompt_leading_id: str
    creation_time: datetime


class PromptEdit(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    prompt_leading_id: str | None = Field(default=None, min_length=36, max_length=36)
    original: PromptValues | None = None
    values: PromptValues


class PromptSave(BaseModel):
    rows: list[PromptEdit] = Field(min_length=1, max_length=500)


def create_prompt_router(current_user_dependency, current_user_write_dependency):
    router = APIRouter(prefix="/api/prompt-config")

    @router.get("/options")
    def options(
        session: Session = Depends(get_session),
        user: AuthenticatedUser = Depends(current_user_dependency),
    ):
        query = select(PromptLeading.subject_id)
        if not user.is_admin:
            query = query.where(
                or_(PromptLeading.user_id == user.user_id, PromptLeading.user_id.is_(None))
            )
        subjects = session.scalars(query.distinct().order_by(PromptLeading.subject_id)).all()
        if user.is_admin:
            users = session.scalars(
                select(PromptLeading.user_id)
                .where(PromptLeading.user_id.is_not(None))
                .distinct()
                .order_by(PromptLeading.user_id)
            ).all()
        else:
            users = [user.user_id]
        return {"subjects": subjects, "users": users, "userId": user.user_id, "isAdmin": user.is_admin}

    @router.get("", response_model=list[PromptOut])
    def list_prompts(
        subject_id: Annotated[str, Query(alias="subjectId", max_length=100)] = "",
        user_id: Annotated[str, Query(alias="userId", max_length=100)] = "",
        session: Session = Depends(get_session),
        user: AuthenticatedUser = Depends(current_user_dependency),
    ):
        query = select(PromptLeading)
        if not user.is_admin:
            if user_id.strip() and user_id.strip() != user.user_id:
                raise HTTPException(403, "无权查看其他用户的提示词。")
            query = query.where(
                or_(PromptLeading.user_id == user.user_id, PromptLeading.user_id.is_(None))
            )
        if subject_id.strip():
            query = query.where(PromptLeading.subject_id == subject_id.strip())
        if user_id.strip():
            query = query.where(PromptLeading.user_id == user_id.strip())
        return session.scalars(
            query.order_by(desc(PromptLeading.creation_time), desc(PromptLeading.prompt_leading_id))
        ).all()

    @router.post("")
    def save_prompts(
        payload: PromptSave,
        session: Session = Depends(get_session),
        user: AuthenticatedUser = Depends(current_user_write_dependency),
    ):
        seen: set[str] = set()
        for row in payload.rows:
            values = row.values.model_dump()
            if values["user_id"] is not None and not values["user_id"].strip():
                values["user_id"] = None
            if row.prompt_leading_id is None:
                if row.original is not None:
                    raise HTTPException(422, "新增行不能包含原始记录。")
                if values["creation_time"] is not None:
                    raise HTTPException(422, "新提示词的创建时间由服务端生成。")
                if not user.is_admin:
                    if values["user_id"] not in (None, user.user_id):
                        raise HTTPException(403, "只能为当前账户创建个人提示词。")
                    values["user_id"] = user.user_id
                if values["creation_time"] is None:
                    values.pop("creation_time")
                session.add(PromptLeading(**values))
                continue
            if row.prompt_leading_id in seen:
                raise HTTPException(422, "同一记录不能重复提交。")
            seen.add(row.prompt_leading_id)
            if row.original is None or row.original.creation_time is None or row.values.creation_time is None:
                raise HTTPException(422, "修改现有记录时必须提供原始记录和创建时间。")
            if row.values.creation_time != row.original.creation_time:
                raise HTTPException(422, "提示词创建时间不可修改。")
            original_user_id = row.original.user_id
            existing = session.get(PromptLeading, row.prompt_leading_id)
            if existing is None or (not user.is_admin and existing.user_id != user.user_id):
                raise HTTPException(404, "未找到可编辑的提示词。")
            if not user.is_admin and (
                original_user_id != user.user_id or values["user_id"] != user.user_id
            ):
                raise HTTPException(403, "个人用户不能编辑共享提示词或转移提示词所有者。")

            conditions = [
                PromptLeading.prompt_leading_id == row.prompt_leading_id,
                PromptLeading.subject_id == row.original.subject_id,
                PromptLeading.prompts == row.original.prompts,
                PromptLeading.user_id == row.original.user_id
                if row.original.user_id is not None
                else PromptLeading.user_id.is_(None),
                cast(PromptLeading.creation_time, String).in_([
                    row.original.creation_time.isoformat(sep=" "),
                    row.original.creation_time.strftime("%Y-%m-%d %H:%M:%S.%f"),
                ]),
            ]
            if not user.is_admin:
                conditions.append(PromptLeading.user_id == user.user_id)
            result = session.execute(update(PromptLeading).where(and_(*conditions)).values(**values))
            if result.rowcount != 1:
                session.rollback()
                raise HTTPException(409, "记录已被修改或删除，未保存任何更改；请重新搜索后重试。")
        session.commit()
        return {"saved": len(payload.rows)}

    return router
