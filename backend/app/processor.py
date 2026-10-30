import io
from pathlib import Path

from docx import Document
from pypdf import PdfReader
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .models import ImportBatch, ImportItem, PromptLeading

MAX_FILE_BYTES = 15 * 1024 * 1024
ALLOWED_EXTENSIONS = {".txt", ".md", ".docx", ".pdf"}


class UnsupportedSourceError(ValueError):
    pass


def generate_preview_prompt(subject_id: str, user_comments: str) -> str:
    return (
        "[本地预览提示词草稿；未调用大模型]\n"
        f"课程/学科：{subject_id.strip()}\n"
        f"用户说明：{user_comments.strip()}\n"
        "请在正式接入模型前，将课程背景转化为可复用的知识抽取规则。"
    )


def select_prompt_history(
    session: Session, subject_id: str | None, user_id: str, limit: int = 3
) -> tuple[list[PromptLeading], str]:
    if subject_id:
        own = session.scalars(
            select(PromptLeading)
            .where(PromptLeading.subject_id == subject_id, PromptLeading.user_id == user_id)
            .order_by(desc(PromptLeading.creation_time), desc(PromptLeading.prompt_leading_id))
            .limit(limit)
        ).all()
        if own:
            return list(own), "USER_SUBJECT"

        shared = session.scalars(
            select(PromptLeading)
            .where(PromptLeading.subject_id == subject_id, PromptLeading.user_id.is_(None))
            .order_by(desc(PromptLeading.creation_time), desc(PromptLeading.prompt_leading_id))
            .limit(limit)
        ).all()
        if shared:
            return list(shared), "SHARED_SUBJECT"

    admin_id = "Admin"
    general = session.scalars(
        select(PromptLeading)
        .where(PromptLeading.subject_id == "General", PromptLeading.user_id == admin_id)
        .order_by(desc(PromptLeading.creation_time), desc(PromptLeading.prompt_leading_id))
        .limit(limit)
    ).all()
    return list(general), "GENERAL"


def create_prompt_and_snapshot(
    session: Session, batch: ImportBatch, user_id: str
) -> None:
    if batch.subject_id and batch.user_comments:
        prompt = PromptLeading(
            subject_id=batch.subject_id,
            prompts=generate_preview_prompt(batch.subject_id, batch.user_comments),
            user_id=user_id,
        )
        session.add(prompt)
        session.flush()

    prompts, source = select_prompt_history(session, batch.subject_id, user_id)
    batch.prompt_snapshot = "\n\n---\n\n".join(item.prompts for item in prompts)
    batch.prompt_snapshot_source = source if prompts else "NONE"
    batch.prompt_snapshot_ids = ",".join(str(item.prompt_leading_id) for item in prompts)


def extract_plain_text(filename: str, content: bytes) -> tuple[str, str]:
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise UnsupportedSourceError(f"暂不支持 {extension or '无扩展名'} 文件；当前仅支持 TXT、Markdown、DOCX 和可提取文本的 PDF。")
    if len(content) > MAX_FILE_BYTES:
        raise UnsupportedSourceError("单个文件超过 15 MB 限制。")
    if extension in {".txt", ".md"}:
        return content.decode("utf-8-sig", errors="strict"), "BLOB"
    if extension == ".docx":
        document = Document(io.BytesIO(content))
        return "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()), "BLOB"
    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted:
        raise UnsupportedSourceError("PDF 已加密，暂时无法读取。")
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    if not text.strip():
        raise UnsupportedSourceError("此 PDF 未提取到可读文本；扫描件 OCR 尚未接入。")
    return text, "BLOB"


def create_preview_item(batch: ImportBatch, content: str, location: str) -> ImportItem:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    title = lines[0][:255] if lines else Path(batch.source_name).stem[:255]
    excerpt = "\n".join(lines[:4])[:1200]
    body = content.strip()[:12000]
    return ImportItem(
        import_batch_id=batch.import_batch_id,
        knowledge_title=title or "待命名知识",
        canonical_content=body or "没有可提取的正文。",
        decision="REVIEW",
        confidence=0.0,
        source_excerpt=excerpt or "未获取到原文片段。",
        source_location=location,
        human_approval="PENDING",
    )
