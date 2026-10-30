import os
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from .db import Base, engine, get_session
from .models import ImportBatch, KnowledgeBase, KnowledgeTrace
from .processor import (
    MAX_FILE_BYTES,
    UnsupportedSourceError,
    create_preview_item,
    create_prompt_and_snapshot,
    extract_plain_text,
)
from .schemas import ImportOut, ItemDecisionIn
from .prompt_config import create_prompt_router

APP_ENV = os.getenv("APP_ENV", "development")
DEV_USER_ID = os.getenv("DEV_USER_ID", "demo-user")
UPLOAD_ROOT = Path(os.getenv("UPLOAD_ROOT", "./.data/uploads")).resolve()
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
if engine.dialect.name != "sqlite":
    raise RuntimeError(
        "自动建表仅允许本地 SQLite。云端数据库必须先审核并执行单独的 schema migration。"
    )
Base.metadata.create_all(bind=engine)
app = FastAPI(title="LearningMachine API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type"],
)


def current_user_id() -> str:
    if APP_ENV != "development":
        raise HTTPException(503, "当前仅配置本地开发身份；生产身份认证尚未接入。")
    return DEV_USER_ID


app.include_router(create_prompt_router(current_user_id))


def get_batch(session: Session, batch_id: str, user_id: str) -> ImportBatch:
    batch = session.scalar(
        select(ImportBatch)
        .options(selectinload(ImportBatch.items))
        .where(ImportBatch.import_batch_id == batch_id, ImportBatch.user_id == user_id)
    )
    if batch is None:
        raise HTTPException(404, "未找到导入记录。")
    return batch


def model_out(batch: ImportBatch) -> ImportOut:
    return ImportOut.model_validate(batch)


@app.get("/api/health")
def health():
    return {"status": "ok", "mode": "preview", "userId": DEV_USER_ID}


@app.get("/api/imports", response_model=list[ImportOut])
def list_imports(session: Session = Depends(get_session), user_id: str = Depends(current_user_id)):
    batches = session.scalars(
        select(ImportBatch)
        .options(selectinload(ImportBatch.items))
        .where(ImportBatch.user_id == user_id)
        .order_by(desc(ImportBatch.created_at))
    ).all()
    return [model_out(batch) for batch in batches]


@app.post("/api/imports", response_model=ImportOut)
async def create_import(
    subject_id: str = Form("", alias="subjectId"),
    user_comments: str = Form("", alias="userComments"),
    text: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    session: Session = Depends(get_session),
    user_id: str = Depends(current_user_id),
):
    if not text.strip() and not files:
        raise HTTPException(422, "请提供文字或至少一个支持的文件。")
    batch = ImportBatch(
        user_id=user_id,
        source_name="知识导入",
        source_type="BLOB" if files else "MANUAL",
        status="PROCESSING",
        subject_id=subject_id.strip() or None,
        user_comments=user_comments.strip() or None,
        is_preview=True,
    )
    session.add(batch)
    session.flush()

    stored_paths: list[Path] = []
    try:
        if len(files) > 10:
            raise UnsupportedSourceError("单次最多添加 10 个文件。")
        parsed_documents: list[tuple[str, bytes, str]] = []
        total_size = 0
        for upload in files:
            filename = Path(upload.filename or "upload").name
            data = await upload.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise UnsupportedSourceError(f"{filename} 超过 15 MB 限制。")
            total_size += len(data)
            if total_size > 30 * 1024 * 1024:
                raise UnsupportedSourceError("单次导入的文件总大小不能超过 30 MB。")
            parsed, _ = extract_plain_text(filename, data)
            parsed_documents.append((filename, data, parsed))

        source_parts = [text.strip()] if text.strip() else []
        source_name_parts: list[str] = []
        object_names: list[str] = []
        for filename, data, parsed in parsed_documents:
            source_parts.append(parsed)
            source_name_parts.append(filename)
            object_name = f"{batch.import_batch_id}-{uuid4().hex}{Path(filename).suffix.lower()}"
            object_path = UPLOAD_ROOT / object_name
            object_path.write_bytes(data)
            stored_paths.append(object_path)
            object_names.append(object_name)

        batch.source_name = (", ".join(source_name_parts) or "文本导入")[:255]
        batch.source_type = "BLOB" if parsed_documents else "MANUAL"
        batch.source_object_key = "\n".join(object_names) or None
        combined = "\n\n".join(part for part in source_parts if part).strip()
        if not combined:
            raise UnsupportedSourceError("没有提取到可用于预览的文本。")
        create_prompt_and_snapshot(session, batch, user_id)
        session.add(create_preview_item(batch, combined, "文本内容 / 首段"))
        batch.status = "NEEDS_REVIEW"
    except (UnsupportedSourceError, UnicodeDecodeError, OSError) as exc:
        for path in stored_paths:
            path.unlink(missing_ok=True)
        batch.status = "FAILED"
        batch.error_message = str(exc)
    session.commit()
    return model_out(get_batch(session, batch.import_batch_id, user_id))


@app.patch("/api/imports/{batch_id}/items/{item_id}", response_model=ImportOut)
def update_item(
    batch_id: str,
    item_id: str,
    payload: ItemDecisionIn,
    session: Session = Depends(get_session),
    user_id: str = Depends(current_user_id),
):
    batch = get_batch(session, batch_id, user_id)
    if batch.status != "NEEDS_REVIEW":
        raise HTTPException(409, "当前批次不处于待审核状态。")
    item = next((candidate for candidate in batch.items if candidate.item_id == item_id), None)
    if item is None:
        raise HTTPException(404, "未找到候选知识。")
    if payload.decision not in {"APPROVED", "REJECTED"}:
        raise HTTPException(422, "审核决定无效。")
    item.human_approval = payload.decision
    session.commit()
    return model_out(get_batch(session, batch_id, user_id))


@app.post("/api/imports/{batch_id}/confirm", response_model=ImportOut)
def confirm_import(
    batch_id: str,
    session: Session = Depends(get_session),
    user_id: str = Depends(current_user_id),
):
    batch = get_batch(session, batch_id, user_id)
    if batch.status != "NEEDS_REVIEW":
        raise HTTPException(409, "当前批次不处于待审核状态。")
    approved = [item for item in batch.items if item.human_approval == "APPROVED"]
    if not approved:
        raise HTTPException(422, "请至少确认一个候选知识后再保存。")
    for item in approved:
        knowledge = KnowledgeBase(
            title=item.knowledge_title,
            canonical_content=item.canonical_content,
            subject_id=batch.subject_id,
            user_id=user_id,
        )
        session.add(knowledge)
        session.flush()
        item.knowledge_id = knowledge.knowledge_id
        session.add(
            KnowledgeTrace(
                knowledge_id=knowledge.knowledge_id,
                contents_added=item.canonical_content,
                change_type="CREATE",
                knowledge_source=batch.source_name,
                subject_id=batch.subject_id,
                human_approval=True,
                ai_eval_returned="本地预览模式：未调用真实大模型。",
                source_type=batch.source_type,
                import_batch_id=batch.import_batch_id,
                user_id=user_id,
                user_comments=batch.user_comments,
            )
        )
    batch.status = "COMPLETED"
    session.commit()
    return model_out(get_batch(session, batch_id, user_id))


@app.get("/api/imports/{batch_id}", response_model=ImportOut)
def get_import(
    batch_id: str,
    session: Session = Depends(get_session),
    user_id: str = Depends(current_user_id),
):
    return model_out(get_batch(session, batch_id, user_id))


@app.get("/api/knowledge-traces")
def list_knowledge_traces(session: Session = Depends(get_session), user_id: str = Depends(current_user_id)):
    traces = session.scalars(
        select(KnowledgeTrace)
        .options(selectinload(KnowledgeTrace.knowledge))
        .where(KnowledgeTrace.user_id == user_id)
        .order_by(desc(KnowledgeTrace.created_at))
    ).all()
    return [
        {
            "traceId": trace.trace_id,
            "knowledgeTrace": trace.knowledge.title,
            "knowledgeSource": trace.knowledge_source,
            "aiEvalReturned": trace.ai_eval_returned,
            "humanApproval": trace.human_approval,
            "importTime": trace.created_at,
            "knowledgeId": trace.knowledge_id,
            "importBatchId": trace.import_batch_id,
        }
        for trace in traces
    ]
