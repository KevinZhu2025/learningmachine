from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .import_services import ImportServiceError


def contract_name(name: str) -> str:
    names = {"request_id": "RequestID", "import_batch_id": "ImportBatchID", "source_id": "SourceID", "segment_id": "SegmentID", "block_id": "BlockID", "subject_id": "SubjectID", "chapter_id": "ChapterID", "user_id": "UserID", "input_subject_id": "InputSubjectID"}
    return names.get(name, "".join(part.capitalize() for part in name.split("_")))


NonBlank = Annotated[str, Field(min_length=1, pattern=r"\S")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, alias_generator=contract_name)


class InputSegment(Contract):
    segment_id: NonBlank
    location: str | None
    page_number: int | None = Field(ge=1)
    block_id: str | None
    bounding_box: dict | None
    text: NonBlank


class InputSource(Contract):
    source_id: NonBlank
    source_type: Literal["BLOB", "WEB", "MANUAL"]
    source_name: NonBlank
    file_name: str | None
    content_type: str | None
    source_url: str | None
    fetched_at: str | None
    extraction_version: int = Field(ge=1)
    segments: list[InputSegment] = Field(min_length=1)


class ModelRequest(Contract):
    schema_version: Literal["1.0", "2.0"]
    request_id: NonBlank
    task: Literal["generate_course_prompt", "extract_content"]
    import_batch_id: NonBlank
    user_id: NonBlank = Field(max_length=100)
    input_subject_id: NonBlank = Field(max_length=100)
    user_comments: str = Field(max_length=20000)
    prompts: NonBlank
    prompt_snapshot_version: int = Field(ge=1)
    prompt_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_fallback_level: Literal["USER_SUBJECT", "SHARED_SUBJECT", "ADMIN_GENERAL"]
    prompt_history: list[dict] = Field(min_length=1, max_length=3)
    sources: list[InputSource]
    knowledge_candidates: list = Field(max_length=0)
    new_candidates: list = Field(max_length=0)


def validate_request(request: dict) -> dict:
    import hashlib
    from uuid import UUID

    try:
        parsed = ModelRequest.model_validate(request)
        UUID(parsed.request_id)
        UUID(parsed.import_batch_id)
        if parsed.task == "extract_content" and (parsed.schema_version != "2.0" or not parsed.sources):
            raise ValueError("抽取任务缺少来源或版本错误。")
        if parsed.task == "generate_course_prompt" and (parsed.schema_version != "1.0" or parsed.sources or parsed.input_subject_id == "General" or not parsed.user_comments.strip()):
            raise ValueError("提示词生成任务输入错误。")
        ids = [source.source_id for source in parsed.sources]
        if len(set(ids)) != len(ids):
            raise ValueError("来源 ID 重复。")
        segment_ids = []
        for source in parsed.sources:
            UUID(source.source_id)
            for segment in source.segments:
                UUID(segment.segment_id)
                segment_ids.append(segment.segment_id)
        if len(set(segment_ids)) != len(segment_ids):
            raise ValueError("片段 ID 重复。")
        if hashlib.sha256(parsed.prompts.encode("utf-8")).hexdigest() != parsed.prompt_snapshot_hash:
            raise ValueError("提示词哈希错误。")
        if "\n\n---\n\n".join(item["Prompts"] for item in parsed.prompt_history) != parsed.prompts:
            raise ValueError("提示词历史与正文不一致。")
        for item in parsed.prompt_history:
            UUID(item["Prompts_SN"])
            if hashlib.sha256(item["Prompts"].encode("utf-8")).hexdigest() != item["ContentHash"]:
                raise ValueError("历史提示词哈希错误。")
    except (ValidationError, ValueError, KeyError, TypeError) as exc:
        raise ImportServiceError("模型输入结构、来源或提示词快照校验失败。") from exc
    return parsed.model_dump(by_alias=True)


class Evidence(Contract):
    segment_id: str
    quote: NonBlank
    location: str | None = None


class SourceResult(Contract):
    source_id: str
    extraction_version: int = Field(ge=1)
    status: Literal["OK", "ERROR"]
    error: str | None = None


class KnowledgeResult(Contract):
    model_item_ref: NonBlank
    source_id: str
    extraction_version: int = Field(ge=1)
    knowledge_title: NonBlank = Field(max_length=255)
    canonical_content: NonBlank
    source_excerpt: NonBlank
    subject_id: str | None = Field(default=None, max_length=100)
    chapter_id: str | None = Field(default=None, max_length=255)
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: list[Evidence] = Field(min_length=1)
    associated_image_refs: list[str] = Field(default_factory=list)
    source_type: str = Field(default="UNKNOWN", min_length=1, max_length=50, pattern=r"\S")


class Option(Contract):
    label: NonBlank
    text: NonBlank


class QuestionResult(Contract):
    question_ref: NonBlank
    source_id: str
    extraction_version: int = Field(ge=1)
    question_text: NonBlank
    source_excerpt: NonBlank
    knowledge_item_refs: list[str] = Field(default_factory=list)
    subject_id: str | None = Field(default=None, max_length=100)
    chapter_id: str | None = Field(default=None, max_length=255)
    question_type: str | None = Field(default=None, max_length=100)
    options_json: list[Option] = Field(default_factory=list)
    correct_answer: str | None = None
    explanation: str | None = None
    difficulty: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: list[Evidence] = Field(min_length=1)
    associated_image_refs: list[str] = Field(default_factory=list)


class ModelResult(Contract):
    schema_version: Literal["2.0"]
    request_id: str
    import_batch_id: str
    source_results: list[SourceResult]
    knowledge_items: list[KnowledgeResult]
    question_items: list[QuestionResult]
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def validate_result(raw: str, request: dict) -> ModelResult:
    if len(raw.encode("utf-8")) > 2 * 1024 * 1024:
        raise ImportServiceError("模型返回超过 2 MB 限制。")
    try:
        result = ModelResult.model_validate_json(raw)
    except ValidationError as exc:
        raise ImportServiceError("模型返回不符合结果 JSON Schema，不能暂存。") from exc
    if result.request_id != request["RequestID"] or result.import_batch_id != request["ImportBatchID"]:
        raise ImportServiceError("模型返回的请求或批次 ID 不匹配。")
    sources = {source["SourceID"]: source for source in request["Sources"]}
    returned_ids = [source.source_id for source in result.source_results]
    if len(set(returned_ids)) != len(returned_ids) or set(returned_ids) != set(sources):
        raise ImportServiceError("模型返回存在未知、重复或遗漏来源。")
    if result.errors or any(source.status != "OK" or source.error is not None for source in result.source_results):
        raise ImportServiceError("模型未成功识别全部来源，结果未暂存。")
    for source in result.source_results:
        if source.extraction_version != sources[source.source_id]["ExtractionVersion"]:
            raise ImportServiceError("模型返回的解析版本不匹配。")
    refs = [item.model_item_ref for item in result.knowledge_items]
    question_refs = [item.question_ref for item in result.question_items]
    if len(set(refs)) != len(refs) or len(set(question_refs)) != len(question_refs):
        raise ImportServiceError("模型结果的局部引用重复。")
    for item in [*result.knowledge_items, *result.question_items]:
        source = sources.get(item.source_id)
        if source is None or item.extraction_version != source["ExtractionVersion"]:
            raise ImportServiceError("候选来源或解析版本不匹配。")
        segments = {segment["SegmentID"]: segment for segment in source["Segments"]}
        if not any(item.source_excerpt in segment["Text"] for segment in segments.values()):
            raise ImportServiceError("候选原文引用不匹配。")
        for evidence in item.evidence:
            segment = segments.get(evidence.segment_id)
            if segment is None or evidence.quote not in segment["Text"] or evidence.location != segment.get("Location"):
                raise ImportServiceError("候选证据或定位不匹配。")
        if item.associated_image_refs:
            raise ImportServiceError("本次请求没有可验证的图片对象，不能接受图片引用。")
    for question in result.question_items:
        if len(set(question.knowledge_item_refs)) != len(question.knowledge_item_refs) or not set(question.knowledge_item_refs) <= set(refs):
            raise ImportServiceError("题目关联了不存在或重复的知识引用。")
        if len({option.label for option in question.options_json}) != len(question.options_json):
            raise ImportServiceError("题目选项标号重复。")
    return result