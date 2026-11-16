# 「提交导入」异步化改造设计

> 2026-11-16 定稿。目标：提交导入秒级返回，识图与大模型抽取转入后台，用户在「导入记录」页随时查看状态，不再在提交页面阻塞等待。

## 一、现状与问题

改造前 `POST /api/imports` 在一次 HTTP 请求内串行完成全部处理：

```
验证/幂等 → 建 ImportBatch(PROCESSING) → 传 OSS + 建 ImportSource
→ 识图 OCR（阻塞，单图 20~90s）→ run_integrated_import（大模型提示词生成+知识抽取，再 30~90s+）
→ 建 ImportItem → 返回最终 COMPLETED/FAILED
```

问题：

1. 前端 `busy=true` 全程锁死，按钮转圈"正在提交"，用户只能干等（密集图片可能 2-3 分钟）。
2. Nginx `proxy_read_timeout 120s` 会先掐断长请求 → 用户看到 504，但后端还在跑，状态不可知。
3. 浏览器/网络中断 = 提交失败假象，但服务端可能已完成，幂等键状态混乱。
4. `retry_import` 同样是同步阻塞的。

## 二、目标行为

```
用户点击提交 ──► 快速返回（仅 OSS 上传 + 建库记录，秒级）
                     │
                     └─► 后台任务：识图 → 大模型抽取 → 写 ImportItem → 状态 COMPLETED/FAILED
用户随时在「导入记录」页看状态（轮询刷新），可离开提交页面、可提交新批次
```

## 三、方案选型

**进程内异步任务 + 数据库状态作队列**（asyncio 后台协程 + 有界并发 + 启动恢复），不引入 Celery/Redis/RabbitMQ。

理由：

- 部署形态是单 ECS + 单 uvicorn 进程（learningmachine.service，无 `--workers`），进程内队列天然成立。
- 批次记录先落库再执行，任务本身有持久化状态，重启后靠启动恢复兜底，不需要独立消息中间件的可靠性保证。
- 个人学习应用的消息量级，引中间件属于过度设计。
- 状态值复用 `PROCESSING`（含义=已提交/排队/处理中），不新增 `QUEUED`，避免 DB 迁移。

**演进触发条件**：将来 uvicorn 加多 worker 或多实例部署时，必须把入队改成 DB 抢占式领取（参考 retry 端点的行级 UPDATE ... WHERE status 抢占写法），或引入独立 worker 进程。

## 四、改动内容

### 后端

- `backend/app/main.py`
  - `create_import` 拆两段：请求内保留验证/幂等/指纹、OSS 上传、建 ImportBatch+ImportSource、快速文本类解析（extract_plain_text/office_pdf/extracted_segments，纯本地 CPU）；识图 OCR 与 `run_integrated_import` 移出请求。段落完成后置 `PROCESSING`、入队、立即返回。
  - 新增 `_process_batch(batch_id)` 后台协程：`SessionLocal()` 新开会话（不复用请求作用域 session），完成 OCR → `run_integrated_import`；异常时置 FAILED + error_message + 清理未提交 OSS 对象（语义与原同步路径一致）。
  - 入队基础设施：模块级 `asyncio.Semaphore(2~3)` 限并发（防打爆 Ark 配额）+ in-flight set 防同批次重复入队。
  - 启动恢复：lifespan 钩子扫描孤儿 PROCESSING 批次 → 重新入队或置 FAILED。
  - `retry_import` 改为置 PROCESSING → 入队 → 立即返回，并放宽"来源未完整解析不能重试"限制（后台重跑 OCR 补齐）。
- `backend/app/processor.py`：抽取 `prepare_source_segments()` 承载来源 OCR/文本解析，main.py 只做编排。
- `backend/tests/test_import_flow.py`：适配异步语义（POST 返回即 PROCESSING，再同步执行后台函数断言终态）；新增幂等重复提交、启动恢复、并发上限用例。

### 前端（`frontend/src/App.tsx`）

- `submitImport`：`busy` 只覆盖秒级上传请求；PROCESSING 响应走成功分支跳转历史页。
- 新增轮询：存在 PROCESSING 批次时每 5s 拉取 `GET /api/imports` 刷新，无处理中批次时停止；登出/卸载清理。纯 fetch 轮询，兼容 HTTP+IP 非安全上下文（不上 SSE/WebSocket）。
- 详情页 PROCESSING 状态显示进行中提示，隐藏编辑与重试。

## 五、关键设计点与边界情况

1. 后台会话管理：`SessionLocal()` 自建会话并确保 close；`expire_on_commit=False` 对长任务友好。
2. 用户身份：入队时捕获 batch_id 即可，批次 user_id 是归属依据。
3. 失败清理：原请求内"未提交 OSS 对象回滚"逻辑原样搬进后台错误路径，避免失败批次在 OSS 留孤儿文件。
4. 双击/重复提交：幂等键（DB 唯一约束 + 指纹校验）防重复建批；in-flight set 防重复入队。
5. 服务重启丢任务：启动恢复兜底；极端情况置 FAILED，用户点重试，原始文件都在 OSS 不丢。
6. Ark 并发：Semaphore 限 2~3；Agent Plan 端点有配额分层，并发过高触发限流。
7. 后台异常：错误写 error_message（用户可见）+ logger.exception 进 journald。

## 六、明确不改的部分

- `PATCH items` / `confirm` 接口（状态守卫已够）。
- 预览模式全链路（保持同步、立即出结果）。
- 数据库表结构（无迁移，复用现有状态机）。
- Nginx `client_max_body_size 20m` 与应用层 30MB 的差异是既有独立问题，另行对齐。

## 七、风险与缓解

| 风险 | 缓解 |
|---|---|
| 单进程内存队列随进程消失 | 启动恢复 + 批次持久化，最坏 FAILED 可重试 |
| 将来多 worker 部署 | 见"演进触发条件"，先改 DB 抢占式领取 |
| 轮询给 DB 增压 | 5s 间隔 + 仅处理中批次轮询 + 单用户场景，量级可忽略 |
| 后台异常吞没 | error_message 用户可见 + journald 日志 |
