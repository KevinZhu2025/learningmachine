import asyncio
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import desc, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .auth import (
    auth_router,
    current_user_for_write,
    current_user_id,
    get_current_user,
    require_csrf_user,
    settings as auth_settings,
)
from .db import engine, get_session, initialize_database
from .models import ImportBatch, ImportItem, ImportSource
from .import_services import ImportServiceError, configured_services
from .vision import normalized_image, scanned_pdf_images, vision_segments
from .processor import (
    IMAGE_EXTENSIONS,
    OFFICE_EXTENSIONS,
    MAX_FILE_BYTES,
    UnsupportedSourceError,
    create_preview_item,
    create_prompt_and_snapshot,
    extract_plain_text,
    extracted_segments,
    office_pdf,
    run_integrated_import,
)
from .schemas import ImportOut, ItemDecisionIn
from .prompt_config import create_prompt_router
from .web_source import (
    PublicWebSourceError,
    fetch_public_page,
)

UPLOAD_ROOT = Path(os.getenv("UPLOAD_ROOT", "./.data/uploads")).resolve()
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
initialize_database(engine)
app = FastAPI(title="LearningMachine API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(auth_settings.allowed_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key"],
)


app.include_router(auth_router)
app.include_router(
    create_prompt_router(
        get_current_user,
        require_csrf_user,
    )
)


def get_import_services():
    return configured_services()


def get_batch(session: Session, batch_id: str, user_id: str) -> ImportBatch:
    batch = session.scalar(
        select(ImportBatch)
        .options(selectinload(ImportBatch.items), selectinload(ImportBatch.sources))
        .where(ImportBatch.import_batch_id == batch_id, ImportBatch.user_id == user_id)
    )
    if batch is None:
        raise HTTPException(404, "未找到导入记录。")
    return batch


def model_out(batch: ImportBatch) -> ImportOut:
    return ImportOut.model_validate(batch)


@app.get("/api/health")
def health():
    mode = os.getenv("IMPORT_MODE", "preview")
    if mode == "integrated":
        try:
            with engine.connect() as connection:
                if connection.exec_driver_sql("SELECT DB_NAME()").scalar_one() != "MEMPALACE":
                    raise RuntimeError("Wrong database")
            if configured_services() is None:
                raise RuntimeError("Missing integrated services")
        except Exception as exc:
            raise HTTPException(503, "集成服务尚未就绪，请检查数据库、Key Vault 和私有 Blob 访问。") from exc
    return {"status": "ok", "mode": mode}


@app.get("/api/imports", response_model=list[ImportOut])
def list_imports(session: Session = Depends(get_session), user_id: str = Depends(current_user_id)):
    batches = session.scalars(
        select(ImportBatch)
        .options(selectinload(ImportBatch.items), selectinload(ImportBatch.sources))
        .where(ImportBatch.user_id == user_id)
        .order_by(desc(ImportBatch.created_at))
    ).all()
    return [model_out(batch) for batch in batches]


@app.post("/api/imports", response_model=ImportOut)
async def create_import(
    subject_id: str = Form("", alias="subjectId", max_length=100),
    user_comments: str = Form("", alias="userComments", max_length=20000),
    text: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    urls: list[str] = Form(default=[]),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
    session: Session = Depends(get_session),
    user_id: str = Depends(current_user_for_write),
    services=Depends(get_import_services),
):
    normalized_text = text.strip()
    normalized_urls = [url.strip() for url in urls if url.strip()]
    if not normalized_text and not files and not normalized_urls:
        raise HTTPException(422, "请提供文字、文件或公开网页链接。")
    if len(files) + len(normalized_urls) > 10:
        raise HTTPException(422, "单次最多添加 10 个文件或网页链接。")

    documents: list[tuple[str, bytes, str | None]] = []
    fingerprint_files: list[dict[str, str | int]] = []
    total_size = 0
    for upload in files:
        filename = (upload.filename or "upload").replace("\\", "/").rsplit("/", 1)[-1]
        if len(filename) > 255 or (upload.content_type and len(upload.content_type) > 255):
            raise HTTPException(422, "文件名或 MIME 类型超过长度限制。")
        data = await upload.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, f"{filename} 超过 15 MB 限制。")
        total_size += len(data)
        if total_size > 30 * 1024 * 1024:
            raise HTTPException(413, "单次导入的文件和文本总大小不能超过 30 MB。")
        fingerprint_files.append(
            {
                "name": filename,
                "contentType": upload.content_type or "",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        )
        documents.append((filename, data, upload.content_type))

    text_bytes = normalized_text.encode("utf-8") if normalized_text else b""
    total_size += len(text_bytes)
    if total_size > 30 * 1024 * 1024:
        raise HTTPException(413, "单次导入的文件和文本总大小不能超过 30 MB。")
    request_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "subjectId": subject_id.strip(),
                "userComments": user_comments.strip(),
                "text": normalized_text,
                "files": fingerprint_files,
                "urls": normalized_urls,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    existing_batch = session.scalar(
        select(ImportBatch).where(
            ImportBatch.user_id == user_id,
            ImportBatch.idempotency_key == idempotency_key,
        )
    )
    if existing_batch is not None:
        if existing_batch.request_fingerprint != request_fingerprint:
            raise HTTPException(409, "幂等键已用于不同内容，请重新提交导入。")
        return model_out(existing_batch)

    batch = ImportBatch(
        user_id=user_id,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        source_name="知识导入",
        source_type="MANUAL",
        status="PROCESSING",
        subject_id=subject_id.strip() or None,
        user_comments=user_comments.strip() or None,
        is_preview=services is None,
    )
    session.add(batch)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        existing_batch = session.scalar(
            select(ImportBatch).where(
                ImportBatch.user_id == user_id,
                ImportBatch.idempotency_key == idempotency_key,
            )
        )
        if existing_batch is None:
            raise exc
        if existing_batch.request_fingerprint != request_fingerprint:
            raise HTTPException(409, "幂等键已用于不同内容，请重新提交导入。") from exc
        return model_out(existing_batch)

    stored_paths: list[Path] = []
    stored_objects: list[str] = []
    sources_committed = False

    async def store_object(key: str, content: bytes, content_type: str | None) -> None:
        if services is None:
            path = UPLOAD_ROOT / key
            path.write_bytes(content)
            stored_paths.append(path)
        else:
            await asyncio.to_thread(services.storage.put, key, content, content_type or "application/octet-stream")
            stored_objects.append(key)

    try:
        source_parts = [normalized_text] if normalized_text else []
        source_name_parts: list[str] = []
        awaiting_vision = False
        if normalized_text:
            object_name = f"{batch.import_batch_id}-{uuid4().hex}.txt"
            await store_object(object_name, text_bytes, "text/plain; charset=utf-8")
            source_id = str(uuid4())
            session.add(
                ImportSource(
                    source_id=source_id,
                    import_batch_id=batch.import_batch_id,
                    source_type="MANUAL",
                    source_name="文本导入",
                    container_name=services.storage.container_name if services else "local",
                    source_object_key=object_name,
                    source_file_name="文本导入.txt",
                    source_content_type="text/plain; charset=utf-8",
                    source_size_bytes=len(text_bytes),
                    source_sha256=hashlib.sha256(text_bytes).hexdigest(),
                    extracted_segments=extracted_segments(normalized_text) if services else None,
                )
            )
            if services is None:
                session.add(create_preview_item(batch, source_id, normalized_text, "文本内容 / 首段"))
            else:
                session.commit()
                sources_committed = True

        for filename, data, content_type in documents:
            if len(data) > MAX_FILE_BYTES:
                raise UnsupportedSourceError(f"{filename} 超过 15 MB 限制。")
            source_name_parts.append(filename)
            object_name = f"{batch.import_batch_id}-{uuid4().hex}{Path(filename).suffix.lower()}"
            await store_object(object_name, data, content_type)
            source_id = str(uuid4())
            session.add(
                ImportSource(
                    source_id=source_id,
                    import_batch_id=batch.import_batch_id,
                    source_type="BLOB",
                    source_name=filename,
                    container_name=services.storage.container_name if services else "local",
                    source_object_key=object_name,
                    source_file_name=filename,
                    source_content_type=content_type,
                    source_size_bytes=len(data),
                    source_sha256=hashlib.sha256(data).hexdigest(),
                )
            )
            if services:
                session.commit()
                sources_committed = True
            extension = Path(filename).suffix.lower()
            image_extension = extension in IMAGE_EXTENSIONS
            images = None
            if image_extension:
                normalized = await asyncio.to_thread(normalized_image, data)
                if services is None:
                    awaiting_vision = True
                    continue
                images = [normalized]
            else:
                parse_data = data
                parse_filename = filename
                if extension in OFFICE_EXTENSIONS:
                    parse_data = await asyncio.to_thread(office_pdf, filename, data)
                    parse_filename = "converted.pdf"
                try:
                    parsed, _ = extract_plain_text(parse_filename, parse_data)
                except UnsupportedSourceError:
                    if Path(parse_filename).suffix.lower() != ".pdf":
                        raise
                    images = await asyncio.to_thread(scanned_pdf_images, parse_data)
                    if services is None:
                        awaiting_vision = True
                        continue
            if images is not None:
                if not services.vision:
                    raise ImportServiceError("尚未配置支持图像输入的识图模型。")
                raw = await asyncio.to_thread(services.vision.recognize, images)
                if len(raw.encode("utf-8")) > 2 * 1024 * 1024:
                    raise ImportServiceError("识图响应超过审计大小限制。")
                source = session.get(ImportSource, source_id)
                source.ocr_result_object_key = f"{batch.import_batch_id}/ocr/{source_id}-{uuid4().hex}.json"
                source.ocr_result_container_name = services.storage.container_name
                await asyncio.to_thread(services.storage.put, source.ocr_result_object_key, raw.encode("utf-8"), "application/json")
                session.commit()
                source.extracted_segments = vision_segments(raw, len(images))
                parsed = "\n".join(segment["Text"] for segment in source.extracted_segments)
                session.commit()
            source_parts.append(parsed)
            if services is None:
                session.add(create_preview_item(batch, source_id, parsed, "文件内容 / 首段"))
            else:
                if images is None:
                    session.get(ImportSource, source_id).extracted_segments = extracted_segments(parsed)
                session.commit()

        web_pages = []
        for url in normalized_urls:
            page = await asyncio.to_thread(fetch_public_page, url)
            total_size += len(page.content)
            if total_size > 30 * 1024 * 1024:
                raise UnsupportedSourceError("单次导入的文件、网页和文本总大小不能超过 30 MB。")
            web_pages.append(page)
            source_parts.append(page.text)
            source_name_parts.append(page.title)
            object_name = f"{batch.import_batch_id}-{uuid4().hex}.html"
            await store_object(object_name, page.content, page.content_type)
            source_id = str(uuid4())
            session.add(
                ImportSource(
                    source_id=source_id,
                    import_batch_id=batch.import_batch_id,
                    source_type="WEB",
                    source_name=page.title,
                    source_url=page.url,
                    container_name=services.storage.container_name if services else "local",
                    source_object_key=object_name,
                    source_file_name=f"{urlsplit(page.url).hostname or 'webpage'}.html"[:255],
                    source_content_type=page.content_type,
                    source_size_bytes=len(page.content),
                    source_sha256=hashlib.sha256(page.content).hexdigest(),
                    extracted_segments=extracted_segments(page.text) if services else None,
                )
            )
            if services is None:
                session.add(create_preview_item(batch, source_id, page.text, "网页内容 / 正文"))
            else:
                session.commit()
                sources_committed = True

        source_name_parts = source_name_parts or (["文本导入"] if normalized_text else [])
        batch.source_name = (", ".join(source_name_parts) or "知识导入")[:255]
        source_kinds = sum(
            bool(value)
            for value in (normalized_text, documents, web_pages)
        )
        batch.source_type = (
            "MIXED"
            if source_kinds > 1
            else "BLOB"
            if documents
            else "WEB"
            if web_pages
            else "MANUAL"
        )
        combined = "\n\n".join(part for part in source_parts if part).strip()
        if services is None and awaiting_vision:
            batch.status = "UPLOADED"
            batch.error_message = "文件已接收；当前为预览模式，图片或扫描页尚未识别。请启用集成服务后重新导入，不会生成虚构知识点。"
            session.commit()
            return model_out(get_batch(session, batch.import_batch_id, user_id))
        if not combined:
            raise UnsupportedSourceError("没有提取到可用于预览的文本。")
        if services is None:
            create_prompt_and_snapshot(session, batch, user_id)
            batch.status = "COMPLETED"
        else:
            session.commit()
            await asyncio.to_thread(run_integrated_import, session, get_batch(session, batch.import_batch_id, user_id), services)
    except (UnsupportedSourceError, PublicWebSourceError, UnicodeDecodeError, OSError, ImportServiceError, IntegrityError) as exc:
        session.rollback()
        if not sources_committed:
            for path in stored_paths:
                path.unlink(missing_ok=True)
        if services:
            committed_keys = set(session.scalars(select(ImportSource.source_object_key).where(ImportSource.import_batch_id == batch.import_batch_id)).all())
            for key in stored_objects:
                if key not in committed_keys:
                    try:
                        await asyncio.to_thread(services.storage.delete, key)
                    except ImportServiceError:
                        pass
        batch = session.get(ImportBatch, batch.import_batch_id)
        batch.status = "FAILED"
        batch.error_message = "暂存数据库写入失败，结果已回滚。" if isinstance(exc, IntegrityError) else str(exc)
    session.commit()
    return model_out(get_batch(session, batch.import_batch_id, user_id))


@app.post("/api/imports/{batch_id}/retry", response_model=ImportOut)
async def retry_import(
    batch_id: str,
    session: Session = Depends(get_session),
    user_id: str = Depends(current_user_for_write),
    services=Depends(get_import_services),
):
    batch = get_batch(session, batch_id, user_id)
    if services is None or batch.status != "FAILED" or batch.is_preview:
        raise HTTPException(409, "仅失败的集成导入任务可以重试。")
    if not batch.sources or any(not source.extracted_segments for source in batch.sources):
        raise HTTPException(409, "来源尚未完整解析，不能直接重试模型；请处理原始文件。")
    claimed = session.execute(update(ImportBatch).where(ImportBatch.import_batch_id == batch_id, ImportBatch.status == "FAILED").values(status="PROCESSING", error_message=None).execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        session.rollback()
        raise HTTPException(409, "此批次已经被其他请求重试。")
    session.commit()
    session.expire_all()
    batch = get_batch(session, batch_id, user_id)
    try:
        await asyncio.to_thread(run_integrated_import, session, batch, services)
    except (ImportServiceError, IntegrityError) as exc:
        session.rollback()
        batch = session.get(ImportBatch, batch_id)
        batch.status = "FAILED"
        batch.error_message = "暂存数据库写入失败，结果已回滚。" if isinstance(exc, IntegrityError) else str(exc)
        session.commit()
    return model_out(get_batch(session, batch_id, user_id))


@app.patch("/api/imports/{batch_id}/items/{item_id}", response_model=ImportOut)
def update_item(
    batch_id: str,
    item_id: str,
    payload: ItemDecisionIn,
    session: Session = Depends(get_session),
    user_id: str = Depends(current_user_for_write),
):
    batch = get_batch(session, batch_id, user_id)
    item = next((candidate for candidate in batch.items if candidate.item_id == item_id), None)
    if item is None:
        raise HTTPException(404, "未找到候选知识。")
    if payload.decision != "PENDING":
        raise HTTPException(409, "首期仅暂存，不进行人工批准、拒绝或正式入库。")
    if item.human_approval != "PENDING":
        raise HTTPException(409, "历史审核记录不能通过首期暂存接口修改。")
    if batch.status not in {"COMPLETED", "NEEDS_REVIEW"}:
        raise HTTPException(409, "当前批次尚未完成暂存。")

    fields = payload.model_fields_set
    values = {
        "human_approval": payload.decision,
        "version_no": ImportItem.version_no + 1,
    }
    if "knowledge_title" in fields:
        title = (payload.knowledge_title or "").strip()
        if not title:
            raise HTTPException(422, "知识标题不能为空。")
        values["knowledge_title"] = title
    if "subject_id" in fields:
        values["subject_id"] = payload.subject_id.strip() or None if payload.subject_id else None
    if "chapter_id" in fields:
        values["chapter_id"] = payload.chapter_id.strip() or None if payload.chapter_id else None
    if "canonical_content" in fields:
        content = (payload.canonical_content or "").strip()
        if not content:
            raise HTTPException(422, "权威知识不能为空。")
        values["canonical_content"] = content

    if item.knowledge_title is None and fields & {"knowledge_title", "canonical_content", "subject_id", "chapter_id"}:
        raise HTTPException(422, "仅题目承载记录不能通过编辑伪造知识内容。")
    if "question_items_json" in fields:
        questions = payload.question_items_json
        if item.knowledge_title is None and not questions:
            raise HTTPException(422, "仅题目记录不能清空题目数组。")
        source_ids = {source.source_id for source in batch.sources}
        for question in questions:
            if question.get("SourceID") is not None and question["SourceID"] not in source_ids:
                raise HTTPException(422, "题目来源不属于当前批次。")
        values["question_items_json"] = questions

    same_values = all(
        getattr(item, field) == value
        for field, value in values.items()
        if field != "version_no" and field != "human_approval"
    )
    if item.version_no != payload.expected_version:
        if (
            item.version_no == payload.expected_version + 1
            and item.human_approval == payload.decision
            and same_values
        ):
            return model_out(batch)
        raise HTTPException(409, "候选内容已被更新，请刷新后再提交。")

    result = session.execute(
        update(ImportItem)
        .where(
            ImportItem.item_id == item_id,
            ImportItem.import_batch_id == batch_id,
            ImportItem.version_no == payload.expected_version,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(409, "候选内容已被更新，请刷新后再提交。")
    if "question_items_json" in fields:
        for candidate in batch.items:
            if candidate.item_id == item_id:
                continue
            same_result = (
                candidate.ai_request_id == item.ai_request_id and candidate.ai_response_version == item.ai_response_version
                if item.ai_request_id else candidate.ai_request_id is None and candidate.source_id == item.source_id
            )
            if same_result:
                if candidate.human_approval != "PENDING":
                    session.rollback()
                    raise HTTPException(409, "同份结果包含历史审核记录，不能修改其题目副本。")
                copied = session.execute(
                    update(ImportItem).where(
                        ImportItem.item_id == candidate.item_id,
                        ImportItem.version_no == candidate.version_no,
                        ImportItem.human_approval == "PENDING",
                    ).values(question_items_json=payload.question_items_json, version_no=ImportItem.version_no + 1)
                    .execution_options(synchronize_session=False)
                )
                if copied.rowcount != 1:
                    session.rollback()
                    raise HTTPException(409, "题目副本已被更新，请刷新后重试。")
    batch.status = "COMPLETED"
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "候选已被其他请求保存，请刷新后确认状态。") from exc
    session.expire_all()
    return model_out(get_batch(session, batch_id, user_id))


@app.post("/api/imports/{batch_id}/confirm", response_model=ImportOut)
def confirm_import(
    batch_id: str,
    session: Session = Depends(get_session),
    user_id: str = Depends(current_user_for_write),
):
    get_batch(session, batch_id, user_id)
    raise HTTPException(409, "首期仅暂存，不开放正式知识或题库提交。")


@app.get("/api/imports/{batch_id}", response_model=ImportOut)
def get_import(
    batch_id: str,
    session: Session = Depends(get_session),
    user_id: str = Depends(current_user_id),
):
    return model_out(get_batch(session, batch_id, user_id))


def _mount_frontend(web_app: FastAPI, distribution_dir: Path) -> None:
    frontend_root = distribution_dir.resolve()
    index_file = frontend_root / "index.html"
    if not index_file.is_file():
        return

    @web_app.get("/", include_in_schema=False)
    @web_app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str = ""):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(404, "未找到接口。")
        requested_file = (frontend_root / full_path).resolve()
        if requested_file.is_relative_to(frontend_root) and requested_file.is_file():
            return FileResponse(requested_file)
        return FileResponse(index_file)


_mount_frontend(app, Path(__file__).resolve().parents[2] / "frontend" / "dist")
