import { useEffect, useRef, useState } from "react";
import {
  ArrowLeftRegular,
  ArrowUploadRegular,
  CheckmarkCircleRegular,
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
import PromptConfig from "./PromptConfig";

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
  items: Array<{
    itemId: string;
    knowledgeTitle: string;
    canonicalContent: string;
    decision: string;
    confidence: number;
    sourceExcerpt: string;
    sourceLocation: string;
    humanApproval: string;
    knowledgeId: string | null;
  }>;
};

type Health = { mode: string; userId: string };
type Tab = "new" | "history" | "config";

const API = "/api";
const statusLabels: Record<string, string> = {
  UPLOADED: "已上传",
  PROCESSING: "处理中",
  NEEDS_REVIEW: "待审核",
  COMPLETED: "已完成",
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
  const [records, setRecords] = useState<ImportRecord[]>([]);
  const [subjectId, setSubjectId] = useState("");
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<ImportRecord | null>(null);
  const [filter, setFilter] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  async function loadHealth() {
    try {
      const response = await fetch(`${API}/health`);
      if (!response.ok) throw new Error("无法连接本地服务");
      setHealth(await response.json());
    } catch {
      setHealth(null);
    }
  }

  async function loadRecords() {
    try {
      const response = await fetch(`${API}/imports`);
      if (!response.ok) throw new Error("导入记录加载失败");
      setRecords(await response.json());
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "导入记录加载失败");
    }
  }

  useEffect(() => {
    void loadHealth();
    void loadRecords();
  }, []);

  function addFiles(incoming: FileList | null) {
    if (!incoming) return;
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
      const response = await fetch(`${API}/imports`, { method: "POST", body: form });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "提交失败，请稍后重试。");
      setText("");
      setFiles([]);
      setSelected(body);
      setTab("history");
      await loadRecords();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "提交失败，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  async function updateItem(itemId: string, decision: string) {
    if (!selected) return;
    const response = await fetch(`${API}/imports/${selected.importBatchId}/items/${itemId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    });
    const body = await response.json();
    if (!response.ok) {
      setError(body.detail ?? "无法更新审核状态。");
      return;
    }
    setSelected(body);
    await loadRecords();
  }

  async function confirmImport() {
    if (!selected) return;
    const response = await fetch(`${API}/imports/${selected.importBatchId}/confirm`, {
      method: "POST",
    });
    const body = await response.json();
    if (!response.ok) {
      setError(body.detail ?? "保存失败。");
      return;
    }
    setSelected(body);
    await loadRecords();
  }

  const filtered = records.filter((record) => {
    const search = filter.trim().toLocaleLowerCase();
    if (!search) return true;
    return [record.sourceName, record.subjectId ?? "", ...record.items.map((item) => item.knowledgeTitle)]
      .join(" ")
      .toLocaleLowerCase()
      .includes(search);
  });

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="#" onClick={() => setSelected(null)}>
          <span className="brand-mark"><LightbulbRegular /></span>
          <span>好玩儿科技</span>
        </a>
        <div className="topbar-right">
          {health?.mode === "preview" && <Badge color="informative" appearance="tint">本地预览模式</Badge>}
          <div className="avatar" title={health?.userId ?? "连接本地服务"}>
            {(health?.userId ?? "学").slice(0, 1).toUpperCase()}
          </div>
        </div>
      </header>

      <section className="welcome-banner">
        <div className="welcome-orb orb-one" />
        <div className="welcome-orb orb-two" />
        <div className="welcome-copy">
          <div className="welcome-eyebrow"><SparkleRegular /> 每天进步一点点</div>
          <h1>{health?.userId ?? "同学"}，开启快乐学习之旅！</h1>
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
          <div className="candidate-list">
            {selected.items.map((item) => (
              <Card className="candidate-card" key={item.itemId}>
                <CardHeader
                  header={<h3>{item.knowledgeTitle}</h3>}
                  description={<span>{item.sourceLocation} · 置信度 {Math.round(item.confidence * 100)}%</span>}
                  action={<Badge color={item.decision === "CONFLICT" ? "danger" : "brand"} appearance="tint">{decisionLabels[item.decision] ?? item.decision}</Badge>}
                />
                <div className="candidate-content">{item.canonicalContent}</div>
                <div className="source-quote"><strong>来源摘录</strong><p>{item.sourceExcerpt}</p></div>
                {selected.status === "NEEDS_REVIEW" && (
                  <div className="candidate-actions">
                    <Button appearance={item.humanApproval === "APPROVED" ? "primary" : "secondary"} onClick={() => void updateItem(item.itemId, "APPROVED")}>确认保留</Button>
                    <Button appearance="secondary" onClick={() => void updateItem(item.itemId, "REJECTED")}>忽略</Button>
                  </div>
                )}
              </Card>
            ))}
          </div>
          {selected.status === "NEEDS_REVIEW" && (
            <div className="detail-footer">
              <span>确认后才会写入正式知识库及来源追踪。</span>
              <Button appearance="primary" icon={<CheckmarkCircleRegular />} onClick={() => void confirmImport()}>确认并保存已选知识</Button>
            </div>
          )}
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
                  <Input value={subjectId} onChange={(_, data) => setSubjectId(data.value)} placeholder="例如：历史、初中物理" />
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
                  onChange={(_, data) => setText(data.value)}
                  placeholder="输入导入说明；不添加附件时，也可直接输入知识内容……"
                />
                {files.length > 0 && (
                  <div className="attachment-list">
                    {files.map((file, index) => (
                      <span className="attachment-chip" key={`${file.name}-${index}`}>
                        <DocumentRegular /> {file.name}
                        <button aria-label={`移除 ${file.name}`} onClick={() => setFiles((current) => current.filter((_, i) => i !== index))}><DismissRegular /></button>
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
                    accept=".txt,.md,.pdf,.docx"
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
                  <span className="format-hint">TXT、Markdown、Word、文本 PDF · 扫描件 OCR 尚未接入</span>
                  <Button appearance="primary" icon={busy ? <Spinner size="tiny" /> : <SendRegular />} disabled={busy} onClick={() => void submitImport()}>
                    {busy ? "正在提交" : "提交导入"}
                  </Button>
                </div>
              </div>
              <p className="async-hint"><ArrowUploadRegular /> 提交后可在“导入记录”查看上传、识别和待审核状态。</p>
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
                    <Card className="record-card" key={record.importBatchId} onClick={() => setSelected(record)}>
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
                      <Button appearance="subtle" icon={<EyeRegular />} onClick={(event) => { event.stopPropagation(); setSelected(record); }}>Detail</Button>
                    </Card>
                  ))}
                </div>
              )}
            </section>
          ) : null}
        </section>
      )}
      <PromptConfig active={tab === "config" && selected === null} />
      <footer className="page-footer">把每次学习，变成看得见的积累 <span>✦</span></footer>
    </main>
  );
}

function StatusBadge({ status }: { status: string }) {
  const color = status === "COMPLETED" ? "success" : status === "FAILED" ? "danger" : status === "NEEDS_REVIEW" ? "warning" : "informative";
  return <Badge color={color} appearance="tint">{statusLabels[status] ?? status}</Badge>;
}

export default App;
