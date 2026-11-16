from fastapi.testclient import TestClient

from app.db import Base
from app.main import app
from app.models import ImportItem, ImportSource, KnowledgeBase, PromptLeading
from app.web_source import WebPage
from conftest import authenticate


def test_ark_key_configuration_requires_secret(monkeypatch):
    import pytest
    from app.import_services import ImportServiceError, load_ark_api_key

    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("ALICLOUD_KMS_SECRET_NAME", raising=False)
    with pytest.raises(ImportServiceError, match="ARK_API_KEY"):
        load_ark_api_key()
    monkeypatch.setenv("ARK_API_KEY", "test-not-a-secret")
    assert load_ark_api_key() == "test-not-a-secret"


def test_integrated_blob_model_pipeline_and_retry(tmp_path, monkeypatch):
    import json
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app import main
    from app.db import get_session
    from app.import_services import ImportServices

    class Storage:
        container_name = "private-test"

        def __init__(self):
            self.objects = {}

        def put(self, key, content, content_type):
            self.objects[key] = content

        def get(self, key):
            return self.objects[key]

        def delete(self, key):
            self.objects.pop(key, None)

    class Model:
        def __init__(self):
            self.requests = []
            self.only_questions = False
            self.bad_evidence = False
            self.invalid = None
            self.fail_generation = False

        def complete(self, request, instruction):
            self.requests.append(request)
            if request["Task"] == "generate_course_prompt":
                if self.fail_generation:
                    return json.dumps({"schema_version": "1.0", "request_id": request["RequestID"], "status": "ERROR", "prompts": None, "error": "generation failed"})
                return json.dumps({"schema_version": "1.0", "request_id": request["RequestID"], "status": "OK", "prompts": "按原文提取知识和已有题目。", "error": None})
            source = request["Sources"][0]
            segment = source["Segments"][0]
            excerpt = segment["Text"].splitlines()[0]
            evidence = [{"SegmentID": segment["SegmentID"], "Quote": "虚构原文" if self.bad_evidence else excerpt, "Location": segment["Location"]}]
            knowledge = [] if self.only_questions else [
                {"ModelItemRef": f"K{number}", "SourceID": source["SourceID"], "ExtractionVersion": source["ExtractionVersion"], "KnowledgeTitle": f"知识{number}", "CanonicalContent": excerpt, "SourceExcerpt": excerpt, "Evidence": evidence, "SourceType": "笔记", "Confidence": 0.9}
                for number in (1, 2)
            ]
            question = {"QuestionRef": "Q1", "SourceID": source["SourceID"], "ExtractionVersion": 1, "QuestionText": excerpt, "SourceExcerpt": excerpt, "KnowledgeItemRefs": [item["ModelItemRef"] for item in knowledge], "Evidence": evidence}
            result = {"SchemaVersion": "2.0", "RequestID": request["RequestID"], "ImportBatchID": request["ImportBatchID"], "SourceResults": [{"SourceID": value["SourceID"], "ExtractionVersion": value["ExtractionVersion"], "Status": "OK", "Error": None} for value in request["Sources"]], "KnowledgeItems": knowledge, "QuestionItems": [question], "Warnings": [], "Errors": []}
            if self.invalid == "request":
                result["RequestID"] = "unknown"
            elif self.invalid == "source":
                result["SourceResults"] = []
            elif self.invalid == "reference":
                result["QuestionItems"][0]["KnowledgeItemRefs"] = ["unknown"]
            elif self.invalid == "image":
                result["QuestionItems"][0]["AssociatedImageRefs"] = ["fabricated"]
            elif self.invalid == "json":
                return "not json"
            return json.dumps(result, ensure_ascii=False)

        def recognize(self, images):
            assert all(image.startswith(b"\xff\xd8") for image in images)
            return json.dumps({"Pages": [{"PageNumber": number, "Blocks": [{"Text": "识图原文。", "Confidence": 0.95}]} for number in range(1, len(images) + 1)], "Warnings": []}, ensure_ascii=False)

    engine = create_engine(f"sqlite:///{tmp_path / 'integrated.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    storage, model = Storage(), Model()

    def override_session():
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[main.get_import_services] = lambda: ImportServices(storage, model)
    # 异步化后提交立即返回 PROCESSING：捕获入队，由测试显式驱动后台处理再断言终态。
    monkeypatch.setattr(main, "SessionLocal", testing_session)
    pending_queue: list = []

    def capture_enqueue(batch_id, services=None):
        pending_queue.append((batch_id, services))

    monkeypatch.setattr(main, "enqueue_import_processing", capture_enqueue)

    def process_pending(batch_id):
        assert pending_queue and pending_queue[0][0] == batch_id, "提交后应当恰好入队一次"
        _, captured_services = pending_queue.pop(0)
        main.process_import_batch(batch_id, captured_services)
        return client.get(f"/api/imports/{batch_id}").json()

    client = TestClient(app)
    try:
        authenticate(client, "integrated-user")
        with testing_session() as session:
            session.add(PromptLeading(subject_id="General", user_id="Admin", prompts="按原文提取，不编造。"))
            session.commit()
        original = "北京人使用天然火。\n原始题目。".encode("utf-8")
        response = client.post("/api/imports", headers={"Idempotency-Key": "integrated-success"}, data={"subjectId": "历史", "userComments": "提取知识及题目"}, files={"files": ("lesson.txt", original, "text/plain")})
        assert response.status_code == 200, response.text
        batch = response.json()
        assert batch["status"] == "PROCESSING", batch
        batch = process_pending(batch["importBatchId"])
        assert batch["status"] == "COMPLETED", batch
        assert batch["isPreview"] is False
        assert len(batch["items"]) == 2
        assert all(item["userId"] == "integrated-user" and item["sourceType"] == "笔记" for item in batch["items"])
        assert batch["items"][0]["questionItemsJson"] == batch["items"][1]["questionItemsJson"]
        assert len(model.requests) == 2
        request = model.requests[-1]
        assert request["UserID"] == "integrated-user" and request["UserComments"] == "提取知识及题目"
        assert request["Sources"][0]["Segments"][0]["Text"].encode("utf-8") == original
        assert "Blob" not in request["Sources"][0]
        assert original in storage.objects.values()
        with testing_session() as session:
            from app.models import ImportBatch
            saved = session.get(ImportBatch, batch["importBatchId"])
            assert saved.model_request_object_key in storage.objects
            assert saved.model_response_object_key in storage.objects
            assert saved.sources[0].container_name == "private-test"
            assert saved.prompt_snapshot_version == 2
            assert session.query(KnowledgeBase).count() == 0
        repeated = client.post("/api/imports", headers={"Idempotency-Key": "integrated-success"}, data={"subjectId": "历史", "userComments": "提取知识及题目"}, files={"files": ("lesson.txt", original, "text/plain")})
        assert repeated.json()["importBatchId"] == batch["importBatchId"]
        assert not pending_queue, "幂等重复提交不应再次入队"
        assert len(model.requests) == 2
        model.only_questions = True
        model.bad_evidence = True
        failed = client.post("/api/imports", headers={"Idempotency-Key": "integrated-failure"}, files={"files": ("questions.txt", original, "text/plain")}).json()
        assert failed["status"] == "PROCESSING"
        failed = process_pending(failed["importBatchId"])
        assert failed["status"] == "FAILED" and failed["items"] == [] and len(failed["sources"]) == 1
        first_attempt = model.requests[-1]
        model.bad_evidence = False
        retried = client.post(f"/api/imports/{failed['importBatchId']}/retry")
        assert retried.status_code == 200, retried.text
        assert retried.json()["status"] == "PROCESSING"
        result = process_pending(failed["importBatchId"])
        assert result["status"] == "COMPLETED" and len(result["items"]) == 1
        assert result["items"][0]["knowledgeId"] is None and result["items"][0]["knowledgeTitle"] is None
        assert result["items"][0]["questionItemsJson"]
        assert model.requests[-1]["RequestID"] != first_attempt["RequestID"]
        assert model.requests[-1]["PromptSnapshotHash"] == first_attempt["PromptSnapshotHash"]
        assert client.post(f"/api/imports/{failed['importBatchId']}/retry").status_code == 409
        for invalid in ("request", "source", "reference", "image", "json"):
            model.invalid = invalid
            malformed = client.post("/api/imports", headers={"Idempotency-Key": f"invalid-{invalid}"}, files={"files": ("invalid.txt", original, "text/plain")}).json()
            assert malformed["status"] == "PROCESSING"
            malformed = process_pending(malformed["importBatchId"])
            assert malformed["status"] == "FAILED" and malformed["items"] == []
            assert len(malformed["sources"]) == 1
        model.invalid = None
        unreadable = client.post("/api/imports", headers={"Idempotency-Key": "ocr-not-selected"}, files={"files": ("scan.png", b"image source", "image/png")}).json()
        # 损坏图片在提交阶段即被校验拒绝（不入队、不识图），保持立即失败语义。
        assert unreadable["status"] == "FAILED" and len(unreadable["sources"]) == 1 and unreadable["items"] == []
        assert not pending_queue
        assert b"image source" in storage.objects.values()
        model.fail_generation = True
        generation_failed = client.post("/api/imports", headers={"Idempotency-Key": "generation-failure"}, data={"subjectId": "历史", "userComments": "失败后固定基础快照"}, files={"files": ("generation.txt", original, "text/plain")}).json()
        assert generation_failed["status"] == "PROCESSING"
        generation_failed = process_pending(generation_failed["importBatchId"])
        assert generation_failed["status"] == "FAILED" and generation_failed["items"] == []
        original_generation = model.requests[-1]
        with testing_session() as session:
            from app.models import ImportBatch
            saved = session.get(ImportBatch, generation_failed["importBatchId"])
            assert saved.model_request["Task"] == "generate_course_prompt"
            assert saved.model_response_object_key in storage.objects
            prompt = session.query(PromptLeading).filter_by(user_id="integrated-user").first()
            prompt.prompts = "后来修改的配置"
            session.commit()
        model.fail_generation = False
        generated_retry = client.post(f"/api/imports/{generation_failed['importBatchId']}/retry").json()
        assert generated_retry["status"] == "PROCESSING"
        generated_retry = process_pending(generation_failed["importBatchId"])
        assert generated_retry["status"] == "COMPLETED"
        assert model.requests[-2]["Task"] == "generate_course_prompt"
        assert model.requests[-2]["PromptSnapshotHash"] == original_generation["PromptSnapshotHash"]
        from PIL import Image
        import io
        import pymupdf
        app.dependency_overrides[main.get_import_services] = lambda: ImportServices(storage, model, model)
        image_buffer = io.BytesIO()
        Image.new("RGB", (100, 60), "white").save(image_buffer, format="PNG")
        image = image_buffer.getvalue()
        picture = client.post("/api/imports", headers={"Idempotency-Key": "vision-picture"}, files={"files": ("picture.png", image, "image/png")}).json()
        assert picture["status"] == "PROCESSING", picture
        picture = process_pending(picture["importBatchId"])
        assert picture["status"] == "COMPLETED", picture
        assert picture["items"][0]["knowledgeTitle"] is None
        assert model.requests[-1]["Sources"][0]["Segments"][0]["PageNumber"] == 1
        with pymupdf.open() as document:
            document.new_page().insert_image(pymupdf.Rect(10, 10, 110, 70), stream=image)
            scanned = document.tobytes()
        scanned_result = client.post("/api/imports", headers={"Idempotency-Key": "vision-pdf"}, files={"files": ("scan.pdf", scanned, "application/pdf")}).json()
        assert scanned_result["status"] == "PROCESSING", scanned_result
        scanned_result = process_pending(scanned_result["importBatchId"])
        assert scanned_result["status"] == "COMPLETED", scanned_result
        with testing_session() as session:
            saved_source = session.query(ImportSource).filter_by(import_batch_id=scanned_result["importBatchId"]).one()
            assert saved_source.ocr_result_object_key in storage.objects
            assert saved_source.ocr_result_container_name == "private-test"
            assert saved_source.extracted_segments[0]["Location"] == "第1页/区块1"
        with pymupdf.open() as document:
            document.new_page().insert_text((20, 40), "Text layer")
            document.new_page().insert_image(pymupdf.Rect(10, 10, 110, 70), stream=image)
            mixed = document.tobytes()
        mixed_result = client.post("/api/imports", headers={"Idempotency-Key": "vision-mixed-pdf"}, files={"files": ("mixed.pdf", mixed, "application/pdf")}).json()
        assert mixed_result["status"] == "PROCESSING", mixed_result
        mixed_result = process_pending(mixed_result["importBatchId"])
        assert mixed_result["status"] == "COMPLETED", mixed_result
        assert len(model.requests[-1]["Sources"][0]["Segments"]) == 2
        with testing_session() as session:
            assert session.query(KnowledgeBase).count() == 0
            assert session.query(ImportItem).count() == 7
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_submit_returns_immediately_and_recovery_requeues_interrupted_batches(tmp_path, monkeypatch):
    import json
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app import main
    from app.db import get_session
    from app.import_services import ImportServices

    class Storage:
        container_name = "private-test"

        def __init__(self):
            self.objects = {}

        def put(self, key, content, content_type):
            self.objects[key] = content

        def get(self, key):
            return self.objects[key]

        def delete(self, key):
            self.objects.pop(key, None)

    class Model:
        def __init__(self):
            self.calls = 0

        def complete(self, request, instruction):
            self.calls += 1
            source = request["Sources"][0]
            segment = source["Segments"][0]
            result = {"SchemaVersion": "2.0", "RequestID": request["RequestID"], "ImportBatchID": request["ImportBatchID"], "SourceResults": [{"SourceID": source["SourceID"], "ExtractionVersion": source["ExtractionVersion"], "Status": "OK", "Error": None}], "KnowledgeItems": [], "QuestionItems": [], "Warnings": [], "Errors": []}
            return json.dumps(result, ensure_ascii=False)

        def recognize(self, images):
            return json.dumps({"Pages": [{"PageNumber": number, "Blocks": [{"Text": "识图原文。", "Confidence": 0.95}]} for number in range(1, len(images) + 1)]}, ensure_ascii=False)

    engine = create_engine(f"sqlite:///{tmp_path / 'recovery.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    storage, model = Storage(), Model()

    def override_session():
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[main.get_import_services] = lambda: ImportServices(storage, model, model)
    monkeypatch.setattr(main, "SessionLocal", testing_session)
    captured: list = []

    def capture_enqueue(batch_id, services=None):
        captured.append((batch_id, services))

    monkeypatch.setattr(main, "enqueue_import_processing", capture_enqueue)
    client = TestClient(app)
    try:
        authenticate(client, "recovery-user")
        with testing_session() as session:
            session.add(PromptLeading(subject_id="General", user_id="Admin", prompts="按原文提取，不编造。"))
            session.commit()
        from PIL import Image
        import io
        image_buffer = io.BytesIO()
        Image.new("RGB", (80, 50), "white").save(image_buffer, format="PNG")
        # 提交图片导入：请求内仅校验图片可读，立即返回 PROCESSING。
        submitted = client.post("/api/imports", headers={"Idempotency-Key": "recovery-picture"}, files={"files": ("note.png", image_buffer.getvalue(), "image/png")}).json()
        assert submitted["status"] == "PROCESSING", submitted
        assert submitted["items"] == []
        assert len(captured) == 1
        # 处理中重复提交同幂等键：返回同批次、不再入队、模型未被调用。
        repeated = client.post("/api/imports", headers={"Idempotency-Key": "recovery-picture"}, files={"files": ("note.png", image_buffer.getvalue(), "image/png")}).json()
        assert repeated["importBatchId"] == submitted["importBatchId"]
        assert repeated["status"] == "PROCESSING"
        assert len(captured) == 1 and model.calls == 0
        # 启动恢复：模拟进程重启后扫描孤儿 PROCESSING 批次重新入队。
        captured.clear()
        recovered = main.recover_interrupted_batches()
        assert recovered == [submitted["importBatchId"]]
        assert captured == [(submitted["importBatchId"], None)]
        # 真正驱动后台处理：识图来源被补齐，批次完成。
        main.process_import_batch(submitted["importBatchId"], ImportServices(storage, model, model))
        final = client.get(f"/api/imports/{submitted['importBatchId']}").json()
        assert final["status"] == "COMPLETED", final
        assert model.calls == 1
        with testing_session() as session:
            from app.models import ImportBatch as BatchModel
            saved = session.get(BatchModel, submitted["importBatchId"])
            assert saved.sources[0].extracted_segments[0]["Location"] == "第1页/区块1"
            assert saved.sources[0].ocr_result_object_key in storage.objects
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_enqueue_import_processing_deduplicates_and_runs_once(monkeypatch):
    import asyncio
    import threading
    from app import main

    calls: list[str] = []
    started = threading.Event()
    release = threading.Event()

    def slow_process(batch_id, services=None):
        calls.append(batch_id)
        started.set()
        release.wait(5)

    monkeypatch.setattr(main, "process_import_batch", slow_process)

    async def scenario():
        main.enqueue_import_processing("batch-dup")
        main.enqueue_import_processing("batch-dup")
        await asyncio.to_thread(started.wait, 5)
        main.enqueue_import_processing("batch-dup")  # 处理中的批次不会重复入队
        release.set()
        await asyncio.gather(*list(main._import_tasks))

    asyncio.run(scenario())
    assert calls == ["batch-dup"]


def test_official_ark_and_oss_adapters_use_safe_contract(monkeypatch):
    import json
    import httpx
    import openai
    import pytest
    import oss2
    from app.import_services import ArkModelProvider, OssStorage, ImportServiceError

    captured = {}

    def respond(request):
        captured["request"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 0, "model": "ark-code-latest", "choices": [{"index": 0, "message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}]})

    original = openai.OpenAI
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: original(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond))))
    provider = ArkModelProvider("test-not-a-real-key", "https://ark.cn-beijing.volces.com/api/plan/v3", "ark-code-latest")
    assert provider.complete({"Task": "extract_content"}, "fixed instruction") == "{}"
    assert captured["request"]["model"] == "ark-code-latest"
    assert captured["request"]["messages"][0]["role"] == "system"
    assert captured["request"]["response_format"] == {"type": "json_object"}

    class FakeBucket:
        acl = oss2.BUCKET_ACL_PRIVATE
        upload_error = None

        def get_bucket_info(self):
            return object()

        def get_bucket_acl(self):
            result = type("AclResult", (), {"acl": self.acl})()
            return result

        def put_object(self, key, content, headers=None):
            if self.upload_error is not None:
                raise self.upload_error
            captured["upload"] = (key, content, headers)

        def delete_object(self, key):
            captured["deleted"] = key

    fake_bucket = FakeBucket()

    class FakeAuth:
        pass

    monkeypatch.setenv("ALICLOUD_RAM_ROLE_NAME", "LearningMachineRAM")
    monkeypatch.setattr(oss2, "ProviderAuth", lambda provider: FakeAuth())
    monkeypatch.setattr(oss2, "EcsRamRoleCredentialsProvider", lambda auth_host: object())
    monkeypatch.setattr(oss2, "Bucket", lambda auth, endpoint, bucket, **kwargs: fake_bucket)

    storage = OssStorage("https://oss-cn-hangzhou-internal.aliyuncs.com", "learningmachineoss")
    storage.put("object", b"source", "text/plain")
    assert captured["upload"][0] == "object"
    assert captured["upload"][2]["Content-Type"] == "text/plain"
    assert captured["upload"][2]["x-oss-forbid-overwrite"] == "true"

    class ForbiddenError(oss2.exceptions.OssError):
        status = 403

        def __init__(self):
            super().__init__(403, {}, b"", {"Code": "AccessDenied", "Message": "sensitive response details"})

    fake_bucket.upload_error = ForbiddenError()
    with pytest.raises(ImportServiceError, match="HTTP 403") as error:
        storage.put("forbidden", b"source", "text/plain")
    assert "sensitive response details" not in str(error.value)

    fake_bucket.acl = "public-read"
    with pytest.raises(ImportServiceError, match="私有"):
        OssStorage("https://oss-cn-hangzhou-internal.aliyuncs.com", "learningmachineoss")


def test_vision_rejects_unreliable_text_and_wrong_pages():
    import json
    import pytest
    from app.import_services import ImportServiceError
    from app.vision import vision_segments

    for payload in (
        {"Pages": [{"PageNumber": 2, "Blocks": [{"Text": "text"}]}]},
        {"Pages": [{"PageNumber": 1, "Blocks": [{"Text": "[无法辨认]"}]}]},
        {"Pages": [{"PageNumber": 1, "Blocks": [{"Text": "text", "Confidence": 0.2}]}]},
        {"Pages": [{"PageNumber": 1, "Blocks": []}]},
    ):
        with pytest.raises(ImportServiceError):
            vision_segments(json.dumps(payload), 1)


def test_question_only_read_and_header_copy_updates(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app import main
    from app.db import get_session

    engine = create_engine(f"sqlite:///{tmp_path / 'questions.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_session():
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(main, "UPLOAD_ROOT", tmp_path / "question-uploads")
    main.UPLOAD_ROOT.mkdir()
    client = TestClient(app)
    try:
        authenticate(client, "question-user")
        batch = client.post("/api/imports", headers={"Idempotency-Key": "question-source"}, data={"text": "原始材料"}).json()
        batch_id = batch["importBatchId"]
        source_id = batch["sources"][0]["sourceId"]
        first_id = batch["items"][0]["itemId"]
        questions = [{"QuestionText": "题目", "SourceID": source_id}]
        with testing_session() as session:
            first = session.get(ImportItem, first_id)
            first.ai_request_id = "accepted-request"
            first.ai_response_version = "2.0"
            first.question_items_json = questions
            second = ImportItem(import_batch_id=batch_id, source_id=source_id, knowledge_title="第二个知识", canonical_content="第二个正文", decision="NEW", source_excerpt="证据", ai_request_id="accepted-request", ai_response_version="2.0", question_items_json=questions)
            carrier = ImportItem(import_batch_id=batch_id, source_id=source_id, source_type="试题", question_items_json=questions, question_text="兼容题干", options_json=[{"Label": "A", "Text": "中文"}], correct_answer="答案" * 200, explanation="解析" * 1000)
            session.add_all([second, carrier])
            session.commit()
            carrier_id = carrier.item_id
            second_id = second.item_id
        returned = client.get(f"/api/imports/{batch_id}").json()
        only_questions = next(item for item in returned["items"] if item["itemId"] == carrier_id)
        assert only_questions["knowledgeId"] is None
        assert only_questions["knowledgeTitle"] is None and only_questions["canonicalContent"] is None
        assert only_questions["confidence"] is None and only_questions["decision"] is None
        assert only_questions["userId"] == "question-user" and only_questions["sourceType"] == "试题"
        assert len(only_questions["explanation"]) == 2000
        new_questions = [{"QuestionText": "更新后的题目", "SourceID": source_id}]
        updated = client.patch(f"/api/imports/{batch_id}/items/{first_id}", json={"expectedVersion": 1, "questionItemsJson": new_questions})
        assert updated.status_code == 200, updated.text
        copies = [item for item in updated.json()["items"] if item["itemId"] in {first_id, second_id}]
        assert all(item["questionItemsJson"] == new_questions and item["versionNo"] == 2 for item in copies)
        assert client.patch(f"/api/imports/{batch_id}/items/{first_id}", json={"expectedVersion": 1, "questionItemsJson": new_questions}).status_code == 200
        assert client.patch(f"/api/imports/{batch_id}/items/{carrier_id}", json={"expectedVersion": 1, "questionItemsJson": []}).status_code == 422
        assert client.patch(f"/api/imports/{batch_id}/items/{carrier_id}", json={"expectedVersion": 1, "knowledgeTitle": "伪造知识"}).status_code == 422
        assert client.patch(f"/api/imports/{batch_id}/items/{first_id}", json={"expectedVersion": 2, "questionItemsJson": [{"QuestionText": "越权", "SourceID": "unknown"}]}).status_code == 422
        assert client.patch(f"/api/imports/{batch_id}/items/{first_id}", json={"expectedVersion": 2, "userId": "another-user"}).status_code == 422
        assert client.patch(f"/api/imports/{batch_id}/items/{first_id}", json={"expectedVersion": 2, "questionItemsJson": None}).status_code == 422
        with testing_session() as session:
            session.get(ImportItem, second_id).human_approval = "APPROVED"
            session.commit()
        blocked = client.patch(f"/api/imports/{batch_id}/items/{first_id}", json={"expectedVersion": 2, "questionItemsJson": [{"QuestionText": "不能改变历史副本"}]})
        assert blocked.status_code == 409
        with testing_session() as session:
            assert session.get(ImportItem, first_id).question_items_json == new_questions
            assert session.get(ImportItem, first_id).version_no == 2
            session.get(ImportItem, second_id).human_approval = "PENDING"
            session.commit()
        with testing_session() as session:
            assert session.query(KnowledgeBase).count() == 0
            assert all(item.human_approval == "PENDING" for item in session.query(ImportItem))
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_preview_import_stays_staged(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import main
    from app.db import get_session

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_session():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(main, "UPLOAD_ROOT", tmp_path / "uploads")
    main.UPLOAD_ROOT.mkdir(exist_ok=True)
    client = TestClient(app)
    try:
        authenticate(client, "demo-user")
        response = client.post(
            "/api/imports",
            headers={"Idempotency-Key": "import-multifile-1"},
            data={"subjectId": "历史", "userComments": "请提取课程重点", "text": "北京人\n能够直立行走并使用天然火。"},
            files=[
                ("files", ("history.txt", b"Peking Man\nused natural fire", "text/plain")),
                ("files", ("notes.md", b"Stone tools\nGathering and hunting", "text/markdown")),
            ],
        )
        assert response.status_code == 200
        batch = response.json()
        assert batch["status"] == "COMPLETED"
        assert batch["isPreview"] is True
        assert batch["subjectId"] == "历史"
        manual_source = next(source for source in batch["sources"] if source["sourceType"] == "MANUAL")
        manual_item = next(item for item in batch["items"] if item["sourceId"] == manual_source["sourceId"])
        assert manual_item["knowledgeTitle"] == "北京人"
        assert len(batch["sources"]) == 3
        assert sum(source["sourceType"] == "BLOB" for source in batch["sources"]) == 2
        assert len({source["sourceId"] for source in batch["sources"]}) == 3
        source_ids = {source["sourceId"] for source in batch["sources"]}
        assert all(item["sourceId"] in source_ids for item in batch["items"])
        assert all(item["userId"] == "demo-user" for item in batch["items"])
        assert all(item["version"] == 0 and item["confidence"] is None for item in batch["items"])
        assert all(item["sourceType"] == "UNKNOWN" and item["questionItemsJson"] == [] for item in batch["items"])
        assert {int(item["knowledgeId"]) for item in batch["items"]} == {1, 2, 3}
        assert "sourceObjectKey" not in batch["sources"][0]
        with testing_session() as session:
            sources = session.query(ImportSource).all()
            assert len({source.source_object_key for source in sources}) == 3
            assert all(source.source_sha256 and source.source_size_bytes for source in sources)
            saved_sources = len(sources)
        prompt_session = testing_session()
        try:
            prompt = prompt_session.query(PromptLeading).one()
            assert prompt.user_id == "demo-user"
            assert prompt.subject_id == "历史"
            assert "请提取课程重点" in prompt.prompts
        finally:
            prompt_session.close()

        item_id = manual_item["itemId"]
        response = client.patch(
            f"/api/imports/{batch['importBatchId']}/items/{item_id}",
            json={
                "expectedVersion": 1,
                "decision": "PENDING",
                "knowledgeTitle": "北京人——生活与技术",
                "subjectId": "历史",
                "chapterId": "史前社会",
                "canonicalContent": "能够直立行走，并使用天然火。",
            },
        )
        assert response.status_code == 200
        edited_item = next(item for item in response.json()["items"] if item["itemId"] == item_id)
        assert edited_item["knowledgeTitle"] == "北京人——生活与技术"
        assert edited_item["chapterId"] == "史前社会"
        assert edited_item["versionNo"] == 2
        stale = client.patch(
            f"/api/imports/{batch['importBatchId']}/items/{item_id}",
            json={"expectedVersion": 1, "decision": "PENDING", "knowledgeTitle": "过期编辑"},
        )
        assert stale.status_code == 409
        response = client.patch(
            f"/api/imports/{batch['importBatchId']}/items/{item_id}",
            json={"expectedVersion": 2, "decision": "APPROVED"},
        )
        assert response.status_code == 409
        assert client.patch(
            f"/api/imports/{batch['importBatchId']}/items/{item_id}",
            json={"expectedVersion": 2, "decision": "REJECTED"},
        ).status_code == 409
        assert client.post(f"/api/imports/{batch['importBatchId']}/confirm").status_code == 409
        with testing_session() as session:
            assert session.query(KnowledgeBase).count() == 0
            assert session.get(ImportItem, item_id).human_approval == "PENDING"
        repeated = client.post(
            "/api/imports",
            headers={"Idempotency-Key": "import-multifile-1"},
            data={"subjectId": "历史", "userComments": "请提取课程重点", "text": "北京人\n能够直立行走并使用天然火。"},
            files=[
                ("files", ("history.txt", b"Peking Man\nused natural fire", "text/plain")),
                ("files", ("notes.md", b"Stone tools\nGathering and hunting", "text/markdown")),
            ],
        )
        assert repeated.status_code == 200
        assert repeated.json()["importBatchId"] == batch["importBatchId"]
        assert {item["knowledgeId"] for item in repeated.json()["items"]} == {"1", "2", "3"}
        with testing_session() as session:
            assert session.query(ImportSource).count() == saved_sources
            assert session.query(KnowledgeBase).count() == 0
        assert client.post(
            "/api/imports", headers={"Idempotency-Key": "import-multifile-1"}, data={"text": "different payload"}
        ).status_code == 409
        rejected = client.post(
            "/api/imports",
            headers={"Idempotency-Key": "import-unsupported-file"},
            data={"text": "unsupported"},
            files={"files": ("unsafe.png", b"not an image", "image/png")},
        )
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "FAILED"
        assert "无法读取上传图片" in rejected.json()["errorMessage"]
        import io
        from PIL import Image

        for extension, image_format in (("JPG", "JPEG"), ("jpeg", "JPEG"), ("png", "PNG"), ("bmp", "BMP"), ("gif", "GIF"), ("tiff", "TIFF"), ("webp", "WEBP")):
            buffer = io.BytesIO()
            Image.new("RGB", (40, 40), "white").save(buffer, format=image_format)
            accepted = client.post(
                "/api/imports", headers={"Idempotency-Key": "preview-image-" + extension},
                files={"files": ("lesson." + extension, buffer.getvalue(), "application/octet-stream")},
            )
            assert accepted.status_code == 200
            uploaded = accepted.json()
            assert uploaded["status"] == "UPLOADED", uploaded
            assert len(uploaded["sources"]) == 1
            assert uploaded["items"] == []
            assert "预览模式" in uploaded["errorMessage"]
        assert rejected.json()["sources"] == []
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_public_web_source_is_saved_and_linked_to_candidate(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import main
    from app.db import get_session

    engine = create_engine(
        f"sqlite:///{tmp_path / 'web.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_session():
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(main, "UPLOAD_ROOT", tmp_path / "web-uploads")
    main.UPLOAD_ROOT.mkdir(exist_ok=True)
    monkeypatch.setattr(
        main,
        "fetch_public_page",
        lambda url: WebPage(
            url=url,
            title="Public lesson",
            text="A public lesson body.",
            content=b"<html><body>A public lesson body.</body></html>",
            content_type="text/html",
        ),
    )
    client = TestClient(app)
    try:
        authenticate(client, "web-user")
        monkeypatch.setenv("IMPORT_MODE", "integrated")
        monkeypatch.delenv("OSS_ENDPOINT", raising=False)
        monkeypatch.delenv("OSS_BUCKET", raising=False)
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        monkeypatch.delenv("ALICLOUD_KMS_SECRET_NAME", raising=False)
        unavailable = client.post(
            "/api/imports",
            headers={"Idempotency-Key": "unconfigured-integrated-import"},
            data={"text": "集成模式不能伪装成预览"},
        )
        assert unavailable.status_code == 503
        monkeypatch.setenv("IMPORT_MODE", "preview")
        response = client.post(
            "/api/imports",
            headers={"Idempotency-Key": "public-page-import"},
            data={"urls": ["https://example.org/lesson"]},
        )
        assert response.status_code == 200
        batch = response.json()
        assert batch["status"] == "COMPLETED"
        assert len(batch["sources"]) == 1
        assert batch["items"][0]["humanApproval"] == "PENDING"
        assert batch["items"][0]["confidence"] is None
        source = batch["sources"][0]
        assert source["sourceType"] == "WEB"
        assert source["sourceUrl"] == "https://example.org/lesson"
        assert len(batch["items"]) == 1
        assert batch["items"][0]["sourceId"] == source["sourceId"]
        with testing_session() as session:
            saved_source = session.get(ImportSource, source["sourceId"])
            assert saved_source.source_sha256
            assert saved_source.source_size_bytes == len(
                b"<html><body>A public lesson body.</body></html>"
            )

        def fail_fetch(_url):
            raise main.PublicWebSourceError("无法读取网页内容。")

        monkeypatch.setattr(main, "fetch_public_page", fail_fetch)
        failed = client.post(
            "/api/imports",
            headers={"Idempotency-Key": "failed-public-page-import"},
            data={"urls": ["https://example.org/unavailable"]},
        )
        assert failed.status_code == 200
        assert failed.json()["status"] == "FAILED"
        assert failed.json()["sources"] == []
        assert failed.json()["items"] == []
        assert failed.json()["errorMessage"]
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_integrated_health_checks_dependencies_without_model_calls(monkeypatch):
    from unittest.mock import MagicMock
    from app import main

    monkeypatch.setenv("IMPORT_MODE", "integrated")
    monkeypatch.setenv("DATABASE_NAME", "LearningMachineDB")
    database = MagicMock()
    database.connect.return_value.__enter__.return_value.exec_driver_sql.return_value.scalar_one.return_value = "LearningMachineDB"
    monkeypatch.setattr(main, "engine", database)
    configured = MagicMock(return_value=object())
    monkeypatch.setattr(main, "configured_services", configured)
    client = TestClient(app)
    assert client.get("/api/health").json() == {"status": "ok", "mode": "integrated"}
    configured.assert_called_once()
    database.connect.side_effect = RuntimeError("sensitive connection details")
    failed = client.get("/api/health")
    assert failed.status_code == 503
    assert "sensitive connection details" not in failed.text


def test_office_conversion_and_docx_tables(monkeypatch):
    import io
    import subprocess
    from pathlib import Path
    import pymupdf
    import pytest
    from docx import Document
    from app import processor

    document = Document()
    document.add_paragraph("Lesson")
    document.add_table(rows=1, cols=1).cell(0, 0).text = "Table knowledge"
    buffer = io.BytesIO()
    document.save(buffer)
    assert "Table knowledge" in processor.extract_plain_text("lesson.docx", buffer.getvalue())[0]

    monkeypatch.setattr(processor.shutil, "which", lambda command: None)
    with pytest.raises(processor.UnsupportedSourceError, match="LibreOffice"):
        processor.office_pdf("lesson.ppt", b"slides")

    monkeypatch.setattr(processor.shutil, "which", lambda command: "soffice")
    def convert(command, **options):
        assert options["timeout"] == 60
        assert "--headless" in command
        output_dir = Path(command[command.index("--outdir") + 1])
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((50, 50), "Slide knowledge")
            pdf.save(output_dir / "source.pdf")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(processor.subprocess, "run", convert)
    for extension in ("ppt", "PPTX", "doc"):
        text, source_type = processor.extract_plain_text("lesson." + extension, b"slides")
        assert "Slide knowledge" in text
        assert source_type == "BLOB"

    def timeout(command, **options):
        raise subprocess.TimeoutExpired(command, options["timeout"])

    monkeypatch.setattr(processor.subprocess, "run", timeout)
    with pytest.raises(processor.UnsupportedSourceError, match="超时"):
        processor.office_pdf("lesson.ppt", b"slides")


def test_frontend_fallback_serves_assets_and_preserves_api_404(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.main import _mount_frontend

    distribution_dir = tmp_path / "dist"
    asset_dir = distribution_dir / "assets"
    asset_dir.mkdir(parents=True)
    (distribution_dir / "index.html").write_text("<main>LearningMachine</main>", encoding="utf-8")
    (asset_dir / "app.js").write_text("export {};", encoding="utf-8")

    test_app = FastAPI()
    _mount_frontend(test_app, distribution_dir)
    client = TestClient(test_app)

    assert client.get("/").text == "<main>LearningMachine</main>"
    assert client.get("/assets/app.js").text == "export {};"
    assert client.get("/imports").text == "<main>LearningMachine</main>"
    assert client.get("/api/unknown").status_code == 404
