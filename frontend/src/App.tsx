import { useEffect, useRef, useState } from "react";
import {
  ArrowLeftRegular,
  ArrowClockwiseRegular,
  ArrowUploadRegular,
  ChevronDownRegular,
  DocumentRegular,
  DismissRegular,
  EyeRegular,
  HistoryRegular,
  LightbulbRegular,
  SendRegular,
  SparkleRegular,
  SettingsRegular,
  WarningRegular,
} from "@fluentui/react-icons";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  Input,
  Spinner,
  Textarea,
} from "@fluentui/react-components";
import AuthPage from "./AuthPage";
import PromptConfig from "./PromptConfig";
import { apiFetch, setCsrfToken } from "./api";

type ImportRecord = {
  importBatchId: string;
  sourceName: string;
  sourceType: string;
  subjectId: string | null;
  status: string;
  createdAt: string;
  userComments: string | null;
  isPreview: boolean;
  errorMessage: string | null;
  sources: Array<{
    sourceId: string;
    sourceType: string;
    sourceName: string;
    sourceUrl: string | null;
    sourceFileName: string | null;
  }>;
  items: Array<{
    itemId: string;
    knowledgeTitle: string | null;
    canonicalContent: string | null;
    subjectId: string | null;
    chapterId: string | null;
    decision: string | null;
    confidence: number | null;
    sourceExcerpt: string | null;
    sourceLocation: string | null;
    sourceId: string;
    aiRequestId: string | null;
    aiResponseVersion: string | null;
    questionItemsJson: Array<{ QuestionRef?: string; QuestionText: string; QuestionType?: string | null; OptionsJson?: Array<{ Label: string; Text: string }>; CorrectAnswer?: string | null; Explanation?: string | null }>;
    humanApproval: string;
    knowledgeId: string | null;
    createdAt: string;
    versionNo: number;
  }>;
};

type ItemDraft = {
  knowledgeTitle: string;
  subjectId: string;
  chapterId: string;
  canonicalContent: string;
  humanApproval: string;
};

type Health = { mode: string };
type AuthUser = { userId: string; csrfToken: string; isAdmin: boolean };
type Tab = "new" | "history" | "config";

const API = "/api";
const statusLabels: Record<string, string> = {
  UPLOADED: "已上传",
  PROCESSING: "处理中",
  NEEDS_REVIEW: "待审核",
  COMPLETED: "暂存完成",
  FAILED: "处理失败",
  CANCELLED: "已取消",
};
const decisionLabels: Record<string, string> = {
  NEW: "新知识",
  SUPPLEMENT: "知识补充",
  DUPLICATE: "疑似重复",
  CONFLICT: "存在冲突",
  REVIEW: "需要复核",
};

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function App() {
  const [tab, setTab] = useState<Tab>("new");
  const [health, setHealth] = useState<Health | null>(null);
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [authError, setAuthError] = useState("");
  const [records, setRecords] = useState<ImportRecord[]>([]);
  const [subjectId, setSubjectId] = useState("");
  const [text, setText] = useState("");
  const [urls, setUrls] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<ImportRecord | null>(null);
  const [itemDrafts, setItemDrafts] = useState<Record<string, ItemDraft>>({});
  const [filter, setFilter] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const idempotencyKey = useRef<string | null>(null);

  function invalidateImportKey() {
    idempotencyKey.current = null;
  }

  async function loadHealth() {
    try {
      const response = await apiFetch(`${API}/health`);
      if (!response.ok) throw new Error("无法连接本地服务");
      setHealth(await response.json());
    } catch {
      setHealth(null);
    }
  }

  async function loadRecords() {
    try {
      const response = await apiFetch(`${API}/imports`);
      if (!response.ok) throw new Error("导入记录加载失败");
      setRecords(await response.json());
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "导入记录加载失败");
    }
  }

  useEffect(() => {
    void loadHealth();
    let active = true;
    fetch(`${API}/auth/me`, { credentials: "include" })
      .then(async (response) => {
        if (response.status === 401) {
          setCsrfToken(null);
          return null;
        }
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail ?? "无法恢复登录状态。");
        return body as AuthUser;
      })
      .then((user) => {
        if (!active || !user) return;
        setCsrfToken(user.csrfToken);
        setAuthUser(user);
        void loadRecords();
      })
      .catch((reason: unknown) => {
        if (active) setAuthError(reason instanceof Error ? reason.message : "无法恢复登录状态。");
      })
      .finally(() => {
        if (active) setAuthReady(true);
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    function handleAuthExpired() {
      setAuthUser(null);
      setCsrfToken(null);
      setRecords([]);
      setSelected(null);
      setItemDrafts({});
    }
    window.addEventListener("auth-expired", handleAuthExpired);
    return () => window.removeEventListener("auth-expired", handleAuthExpired);
  }, []);

  function onAuthenticated(user: AuthUser) {
    setCsrfToken(user.csrfToken);
    setAuthUser(user);
    setAuthError("");
    void loadRecords();
  }

  async function logout() {
    try {
      const response = await apiFetch(`${API}/auth/logout`, { method: "POST" });
      if (!response.ok) throw new Error("退出登录失败，请重试。");
      setCsrfToken(null);
      setAuthUser(null);
      setRecords([]);
      setSelected(null);
      setItemDrafts({});
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "退出登录失败。");
    }
  }

  function addFiles(incoming: FileList | null) {
    if (!incoming) return;
    invalidateImportKey();
    setFiles((current) => [...current, ...Array.from(incoming)]);
  }

  async function submitImport() {
    if (!text.trim() && files.length === 0) {
      setError("请先输入导入说明，或添加附件。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.set("subjectId", subjectId.trim());
      form.set("userComments", text.trim());
      form.set("text", files.length === 0 ? text.trim() : "");
      files.forEach((file) => form.append("files", file));
      urls.split(/\r?\n/).map((url) => url.trim()).filter(Boolean)
        .forEach((url) => form.append("urls", url));
      idempotencyKey.current ??= crypto.randomUUID();
      const response = await apiFetch(`${API}/imports`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey.current },
        body: form,
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "提交失败，请稍后重试。");
      idempotencyKey.current = null;
      setText("");
      setUrls("");
      setFiles([]);
      setSelected(body);
      setItemDrafts({});
      setTab("history");
      await loadRecords();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "提交失败，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  function getItemDraft(item: ImportRecord["items"][number]): ItemDraft {
    return itemDrafts[item.itemId] ?? {
      knowledgeTitle: item.knowledgeTitle ?? "",
      subjectId: item.subjectId ?? "",
      chapterId: item.chapterId ?? "",
      canonicalContent: item.canonicalContent ?? "",
      humanApproval: item.humanApproval,
    };
  }

  function hasItemChanges(item: ImportRecord["items"][number]) {
    const draft = getItemDraft(item);
    return draft.knowledgeTitle !== (item.knowledgeTitle ?? "")
      || draft.subjectId !== (item.subjectId ?? "")
      || draft.chapterId !== (item.chapterId ?? "")
      || draft.canonicalContent !== (item.canonicalContent ?? "")
      || draft.humanApproval !== item.humanApproval;
  }

  function updateItemDraft<K extends keyof ItemDraft>(
    item: ImportRecord["items"][number],
    field: K,
    value: ItemDraft[K],
  ) {
    setItemDrafts((current) => {
      const previous = current[item.itemId] ?? {
        knowledgeTitle: item.knowledgeTitle ?? "",
        subjectId: item.subjectId ?? "",
        chapterId: item.chapterId ?? "",
        canonicalContent: item.canonicalContent ?? "",
        humanApproval: item.humanApproval,
      };
      return {
        ...current,
        [item.itemId]: { ...previous, [field]: value },
      };
    });
  }

  function undoItemChanges(itemId: string) {
    setItemDrafts((current) => {
      const remaining = { ...current };
      delete remaining[itemId];
      return remaining;
    });
  }

  async function saveItem(item: ImportRecord["items"][number]) {
    if (!selected) return;
    const draft = getItemDraft(item);
    const response = await apiFetch(`${API}/imports/${selected.importBatchId}/items/${item.itemId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expectedVersion: item.versionNo,
        decision: draft.humanApproval,
        knowledgeTitle: draft.knowledgeTitle,
        subjectId: draft.subjectId.trim() || null,
        chapterId: draft.chapterId.trim() || null,
        canonicalContent: draft.canonicalContent,
      }),
    });
    const body = await response.json();
    if (!response.ok) {
      setError(body.detail ?? "无法保存候选内容。");
      return;
    }
    setSelected(body);
    undoItemChanges(item.itemId);
    await loadRecords();
  }

  async function retryImport() {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch(`${API}/imports/${selected.importBatchId}/retry`, { method: "POST" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "无法重试导入。");
      setSelected(body);
      await loadRecords();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "重试失败，请稍后再试。");
    } finally {
      setBusy(false);
    }
  }

  const filtered = records.filter((record) => {
    const search = filter.trim().toLocaleLowerCase();
    if (!search) return true;
    return [record.sourceName, record.subjectId ?? "", ...record.items.map((item) => item.knowledgeTitle)]
      .join(" ")
      .toLocaleLowerCase()
      .includes(search);
  });

  if (!authReady) {
    return <main className="auth-loading"><Spinner size="large" label="正在检查登录状态…" /></main>;
  }
  if (!authUser) {
    return <AuthPage onAuthenticated={onAuthenticated} initialError={authError} />;
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="#" onClick={() => setSelected(null)}>
          <span className="brand-mark"><LightbulbRegular /></span>
          <span>好玩儿科技</span>
        </a>
        <div className="topbar-right">
          {health?.mode === "preview" && <Badge color="informative" appearance="tint">本地预览模式</Badge>}
          <span className="signed-in-user">{authUser.userId}</span>
          <Button appearance="subtle" onClick={() => void logout()}>退出登录</Button>
          <div className="avatar" title={authUser.userId}>
            {authUser.userId.slice(0, 1).toUpperCase()}
          </div>
        </div>
      </header>

      <section className="welcome-banner">
        <div className="welcome-orb orb-one" />
        <div className="welcome-orb orb-two" />
        <div className="welcome-copy">
          <div className="welcome-eyebrow"><SparkleRegular /> 每天进步一点点</div>
          <h1>{authUser.userId}，开启快乐学习之旅！</h1>
          <p>聚沙成塔，一问一答，强化理解和记忆。</p>
        </div>
        <div className="welcome-illustration" aria-hidden="true">
          <div className="book book-back" />
          <div className="book book-front"><span>学</span></div>
          <SparkleRegular className="welcome-sparkle" />
        </div>
      </section>

      {health?.mode === "preview" && (
        <div className="preview-notice">
          <SparkleRegular />
          预览模式：识别结果为本地演示数据，不代表真实大模型结论；未连接 Azure SQL、Blob 或火山方舟。
        </div>
      )}
      {!health && (
        <div className="preview-notice notice-error">
          <WarningRegular /> 尚未连接后端服务。请按项目 README 启动 FastAPI 后端。
        </div>
      )}
      {error && <div className="error-banner" role="alert"><WarningRegular /> {error}</div>}

      {selected ? (
        <section className="content-area detail-view">
          <Button appearance="subtle" icon={<ArrowLeftRegular />} onClick={() => setSelected(null)}>
            返回导入记录
          </Button>
          <div className="detail-heading">
            <div>
              <span className="section-kicker">知识内容</span>
              <h2>{selected.sourceName}</h2>
              <p>{selected.subjectId || "通用课程"} · {formatTime(selected.createdAt)}</p>
            </div>
            <StatusBadge status={selected.status} />
          </div>
          {selected.isPreview && <div className="preview-inline">本地预览生成的候选知识，仅供界面和流程验证。</div>}
          {selected.errorMessage && <div className="error-banner" role="alert">{selected.errorMessage}</div>}
          {selected.status === "FAILED" && !selected.isPreview && <Button icon={<ArrowClockwiseRegular />} disabled={busy} onClick={() => void retryImport()}>重试</Button>}
          {selected.sources.length > 0 && (
            <section className="source-list" aria-label="导入来源">
              <h3>导入来源</h3>
              {selected.sources.map((source) => (
                <div className="source-list-item" key={source.sourceId}>
                  <DocumentRegular />
                  <span>{source.sourceFileName || source.sourceName}</span>
                  {source.sourceType === "WEB" && <Badge appearance="tint">网页</Badge>}
                  {source.sourceUrl && <small>{source.sourceUrl}</small>}
                </div>
              ))}
            </section>
          )}
          <div className="candidate-list">
            {selected.items.map((item) => {
              const draft = getItemDraft(item);
              const editable = ["COMPLETED", "NEEDS_REVIEW"].includes(selected.status) && item.humanApproval === "PENDING" && item.knowledgeTitle !== null;
              const changed = hasItemChanges(item);
              return (
              <Card className="candidate-card" key={item.itemId}>
                <CardHeader
                  header={<h3>{item.knowledgeTitle ?? "题目材料"}</h3>}
                  description={<span>{item.sourceLocation ?? "位置未确定"} · 置信度 {item.confidence === null ? "未确定" : `${Math.round(item.confidence * 100)}%`} · 生成于 {formatTime(item.createdAt)}</span>}
                  action={<Badge color={item.decision === "CONFLICT" ? "danger" : "brand"} appearance="tint">{item.decision ? decisionLabels[item.decision] ?? item.decision : "题目"}</Badge>}
                />
                {item.knowledgeTitle !== null && <p className="candidate-knowledge-id">预留知识序号：{item.knowledgeId ?? "未分配"}</p>}
                {item.knowledgeTitle !== null && <div className="candidate-edit-grid">
                  <label>知识标题
                    <Input value={draft.knowledgeTitle} disabled={!editable} onChange={(_, data) => updateItemDraft(item, "knowledgeTitle", data.value)} />
                  </label>
                  <label>学科 AI 识别
                    <Input value={draft.subjectId} disabled={!editable} onChange={(_, data) => updateItemDraft(item, "subjectId", data.value)} />
                  </label>
                  <label>学科章节
                    <Input value={draft.chapterId} disabled={!editable} onChange={(_, data) => updateItemDraft(item, "chapterId", data.value)} />
                  </label>
                  <label className="candidate-content-field">权威知识
                    <Textarea value={draft.canonicalContent} disabled={!editable} resize="vertical" onChange={(_, data) => updateItemDraft(item, "canonicalContent", data.value)} />
                  </label>
                  <div className="candidate-edit-actions">
                    <Button appearance="primary" disabled={!editable || !changed} onClick={() => void saveItem(item)}>保存</Button>
                    <Button appearance="secondary" disabled={!editable || !changed} onClick={() => undoItemChanges(item.itemId)}>撤销</Button>
                  </div>
                </div>}
                {item.sourceExcerpt && <div className="source-quote"><strong>来源摘录</strong><p>{item.sourceExcerpt}</p></div>}
              </Card>
              );
            })}
          </div>
          {selected.items.filter((item, index, all) => item.questionItemsJson.length > 0 && all.findIndex((other) => (other.aiRequestId ?? other.sourceId) === (item.aiRequestId ?? item.sourceId) && other.aiResponseVersion === item.aiResponseVersion) === index).map((item) => (
            <section className="source-list" key={`questions-${item.itemId}`} aria-label="题目">
              <h3>题目</h3>
              {item.questionItemsJson.map((question, index) => (
                <div className="source-quote" key={question.QuestionRef ?? index}>
                  <strong>{index + 1}. {question.QuestionText}</strong>
                  {question.OptionsJson?.map((option) => <p key={option.Label}>{option.Label}. {option.Text}</p>)}
                  {question.CorrectAnswer && <p>答案：{question.CorrectAnswer}</p>}
                  {question.Explanation && <p>解析：{question.Explanation}</p>}
                </div>
              ))}
            </section>
          ))}
        </section>
      ) : (
        <section className="content-area">
          <nav className="tabs" aria-label="知识导入页面">
            <button className={`tab ${tab === "new" ? "active" : ""}`} onClick={() => setTab("new")}>
              <SparkleRegular /> 新建知识导入
            </button>
            <button className={`tab ${tab === "history" ? "active" : ""}`} onClick={() => { setTab("history"); void loadRecords(); }}>
              <HistoryRegular /> 导入记录 <span className="tab-count">{records.length}</span>
            </button>
            <button className={`tab ${tab === "config" ? "active" : ""}`} onClick={() => setTab("config")}>
              <SettingsRegular /> 配置管理
            </button>
          </nav>

          {tab === "new" ? (
            <section className="new-import-panel">
              <div className="panel-title">
                <span className="title-icon"><SparkleRegular /></span>
                <div><h2>与 AI 一起整理知识</h2><p>告诉我课程背景和重点，再添加需要整理的学习资料。</p></div>
              </div>
              <div className="context-grid">
                <label className="field">
                  <span>课程 / 学科 <em>可选</em></span>
                  <Input value={subjectId} onChange={(_, data) => { setSubjectId(data.value); invalidateImportKey(); }} placeholder="例如：历史、初中物理" />
                </label>
              </div>
              <div className="prompt-hint">
                <SparkleRegular />
                {subjectId.trim() && text.trim()
                  ? "本次会生成一条课程提示词，并按课程历史选择最近的提示词。"
                  : "填写课程和下方说明时才新增课程提示词；否则继续使用已有提示词。"}
                <ChevronDownRegular />
              </div>
              <div className="composer">
                <Textarea
                  className="composer-text"
                  resize="vertical"
                  aria-label="导入说明或知识内容"
                  value={text}
                  onChange={(_, data) => { setText(data.value); invalidateImportKey(); }}
                  placeholder="输入导入说明；不添加附件时，也可直接输入知识内容……"
                />
                <label className="field web-url-field">
                  <span>公开网页链接 <em>可选，每行一个</em></span>
                  <Textarea
                    value={urls}
                    onChange={(_, data) => { setUrls(data.value); invalidateImportKey(); }}
                    placeholder="https://example.com/learning-material"
                    resize="vertical"
                    aria-label="公开网页链接，每行一个"
                  />
                  <small>每行一个公开 HTTP/HTTPS 链接；不支持登录页面。文件和链接合计最多 10 项。</small>
                </label>
                {files.length > 0 && (
                  <div className="attachment-list">
                    {files.map((file, index) => (
                      <span className="attachment-chip" key={`${file.name}-${index}`}>
                        <DocumentRegular /> {file.name}
                        <button aria-label={`移除 ${file.name}`} onClick={() => { invalidateImportKey(); setFiles((current) => current.filter((_, i) => i !== index)); }}><DismissRegular /></button>
                      </span>
                    ))}
                  </div>
                )}
                <div className="composer-toolbar">
                  <input
                    ref={fileInput}
                    className="visually-hidden"
                    type="file"
                    multiple
                    accept=".txt,.md,.pdf,.docx,.png,.jpg,.jpeg,.webp"
                    onChange={(event) => addFiles(event.target.files)}
                  />
                  <Button
                    appearance="subtle"
                    className="attach-button"
                    icon={<span className="plus-icon">+</span>}
                    onClick={() => fileInput.current?.click()}
                  >
                    添加附件
                  </Button>
                  <span className="format-hint">TXT、Markdown、PDF、图片 · 图片与扫描 PDF 将进行多模态识别</span>
                  <Button appearance="primary" icon={busy ? <Spinner size="tiny" /> : <SendRegular />} disabled={busy} onClick={() => void submitImport()}>
                    {busy ? "正在提交" : "提交导入"}
                  </Button>
                </div>
              </div>
              <p className="async-hint"><ArrowUploadRegular /> 提交完成后可在“导入记录”查看来源和待审核状态。</p>
            </section>
          ) : tab === "history" ? (
            <section className="history-panel">
              <div className="history-heading">
                <div><span className="section-kicker">知识整理历史</span><h2>导入记录</h2></div>
                <Input value={filter} onChange={(_, data) => setFilter(data.value)} placeholder="搜索来源、课程或知识点" />
              </div>
              {filtered.length === 0 ? (
                <div className="empty-state"><HistoryRegular /><h3>{records.length ? "没有匹配的记录" : "还没有导入记录"}</h3><p>新建一次知识导入，处理进度和结果会显示在这里。</p><Button appearance="primary" onClick={() => setTab("new")}>新建知识导入</Button></div>
              ) : (
                <div className="record-list">
                  {filtered.map((record) => (
                    <Card className="record-card" key={record.importBatchId} onClick={() => { setItemDrafts({}); setSelected(record); }}>
                      <div className="record-main">
                        <div className="record-file-icon"><DocumentRegular /></div>
                        <div className="record-info">
                          <h3>{record.items[0]?.knowledgeTitle ?? record.sourceName}</h3>
                          <p>{record.sourceName} · {record.subjectId || "通用课程"}</p>
                        </div>
                      </div>
                      <div className="record-eval">{record.items[0]?.decision ? decisionLabels[record.items[0].decision] : record.status === "FAILED" ? "请查看处理错误" : "等待识别"}</div>
                      <StatusBadge status={record.status} />
                      <time>{formatTime(record.createdAt)}</time>
                      <Button appearance="subtle" icon={<EyeRegular />} onClick={(event) => { event.stopPropagation(); setItemDrafts({}); setSelected(record); }}>Detail</Button>
                    </Card>
                  ))}
                </div>
              )}
            </section>
          ) : null}
        </section>
      )}
      <PromptConfig
        active={tab === "config" && selected === null}
        isAdmin={authUser.isAdmin}
        userId={authUser.userId}
      />
      <footer className="page-footer">把每次学习，变成看得见的积累 <span>✦</span></footer>
    </main>
  );
}

function StatusBadge({ status }: { status: string }) {
  const color = status === "COMPLETED" ? "success" : status === "FAILED" ? "danger" : status === "NEEDS_REVIEW" ? "warning" : "informative";
  return <Badge color={color} appearance="tint">{statusLabels[status] ?? status}</Badge>;
}

export default App;
