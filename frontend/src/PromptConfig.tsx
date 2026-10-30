import { useEffect, useRef, useState } from "react";
import { Button, Input } from "@fluentui/react-components";

type Values = {
  subjectId: string;
  prompts: string;
  userId: string | null;
  creationTime: string | null;
};
type Prompt = Values & { promptLeadingId: number };
type Row = { key: string; id: number | null; original: Values | null; values: Values };
type Options = { subjects: string[]; users: string[] };

function rowFromPrompt(prompt: Prompt): Row {
  const { promptLeadingId, ...values } = prompt;
  return { key: `saved-${promptLeadingId}`, id: promptLeadingId, original: values, values: { ...values } };
}

function isDirty(row: Row): boolean {
  return row.original === null || JSON.stringify(row.original) !== JSON.stringify(row.values);
}

async function responseJson(response: Response) {
  const body = await response.json();
  if (!response.ok) {
    throw new Error(typeof body.detail === "string" ? body.detail : "数据无效，请检查课程、提示词和创建时间。");
  }
  return body;
}

export default function PromptConfig({ active }: { active: boolean }) {
  const [subject, setSubject] = useState("");
  const [user, setUser] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [options, setOptions] = useState<Options>({ subjects: [], users: [] });
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const tableRef = useRef<HTMLDivElement>(null);
  const dirty = rows.some(isDirty);

  async function load(subjectFilter: string, userFilter: string) {
    const query = new URLSearchParams({ subjectId: subjectFilter.trim(), userId: userFilter.trim() });
    const [listResponse, optionsResponse] = await Promise.all([
      fetch(`/api/prompt-config?${query}`),
      fetch("/api/prompt-config/options"),
    ]);
    const [list, choices]: [Prompt[], Options] = await Promise.all([
      responseJson(listResponse),
      responseJson(optionsResponse),
    ]);
    setRows(list.map(rowFromPrompt));
    setOptions(choices);
  }

  useEffect(() => {
    if (!active || loaded) return;
    setLoaded(true);
    setBusy(true);
    load("", "")
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "配置加载失败。"))
      .finally(() => setBusy(false));
  }, [active, loaded]);

  async function search(discard = false) {
    if (dirty && !discard) {
      setError("请先保存修改，或选择“放弃修改”后再搜索。");
      return;
    }
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await load(subject, user);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "配置加载失败。");
    } finally {
      setBusy(false);
    }
  }

  function change(key: string, field: keyof Values, value: string) {
    setRows((current) => current.map((row) => row.key === key
      ? { ...row, values: { ...row.values, [field]: (field === "userId" || field === "creationTime") && !value ? null : value } }
      : row));
    setMessage("");
  }

  function addRow() {
    setRows((current) => [...current, {
      key: crypto.randomUUID(),
      id: null,
      original: null,
      values: { subjectId: subject.trim(), prompts: "", userId: user.trim() || null, creationTime: null },
    }]);
    setError("");
    setMessage("");
    requestAnimationFrame(() => tableRef.current?.scrollTo({ top: tableRef.current.scrollHeight }));
  }

  async function save() {
    const changed = rows.filter(isDirty);
    if (!changed.length) {
      setMessage("没有需要保存的修改。");
      return;
    }
    if (changed.some((row) => !row.values.subjectId.trim() || !row.values.prompts.trim() ||
      (row.id !== null && !row.values.creationTime))) {
      setError("课程和提示词不能为空；已有记录的创建时间不能为空。");
      return;
    }
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch("/api/prompt-config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rows: changed.map((row) => ({
          promptLeadingId: row.id, original: row.original, values: row.values,
        })) }),
      });
      await responseJson(response);
      setMessage(`已保存 ${changed.length} 行。`);
      // Reload the visible filter without retrying a successful save if refresh fails.
      try {
        await load(subject, user);
      } catch {
        setRows((current) => current.filter((row) => !isDirty(row)));
        setError("保存已成功，但刷新失败；请搜索以重新加载。");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存失败。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="config-panel" hidden={!active} aria-label="配置管理">
      <div className="history-heading"><div><span className="section-kicker">提示词配置</span><h2>配置管理</h2></div></div>
      <p className="config-note">本地预览数据库；用户留空表示共享。创建时间按 UTC 输入（例如 2026-10-30T12:00:00），新增时留空自动生成。</p>
      <div className="config-filters">
        <label className="field"><span>课程</span>
          <Input list="config-subjects" value={subject} onChange={(_, data) => setSubject(data.value)} placeholder="全部课程" disabled={busy} />
        </label>
        <datalist id="config-subjects">{options.subjects.map((value) => <option key={value} value={value} />)}</datalist>
        <label className="field"><span>用户</span>
          <Input list="config-users" value={user} onChange={(_, data) => setUser(data.value)} placeholder="全部用户" disabled={busy} />
        </label>
        <datalist id="config-users">{options.users.map((value) => <option key={value} value={value} />)}</datalist>
        <Button appearance="primary" onClick={() => void search()} disabled={busy}>搜索</Button>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {message && <p className="config-message" role="status">{message}</p>}
      <div className="config-table-wrap" ref={tableRef}>
        <table className="config-table">
          <thead><tr><th>课程</th><th>提示词</th><th>用户</th><th>创建时间（UTC）</th></tr></thead>
          <tbody>{rows.map((row, index) => (
            <tr key={row.key}>
              <td><input aria-label={`第${index + 1}行课程`} value={row.values.subjectId} maxLength={100} disabled={busy} onChange={(event) => change(row.key, "subjectId", event.target.value)} /></td>
              <td><textarea aria-label={`第${index + 1}行提示词`} value={row.values.prompts} disabled={busy} onChange={(event) => change(row.key, "prompts", event.target.value)} /></td>
              <td><input aria-label={`第${index + 1}行用户`} value={row.values.userId ?? ""} maxLength={100} disabled={busy} onChange={(event) => change(row.key, "userId", event.target.value)} /></td>
              <td><input aria-label={`第${index + 1}行创建时间`} value={row.values.creationTime ?? ""} disabled={busy} placeholder="自动生成 / ISO 时间" onChange={(event) => change(row.key, "creationTime", event.target.value)} /></td>
            </tr>
          ))}</tbody>
        </table>
        {!rows.length && <p className="config-empty">暂无记录，请点击“新增”。</p>}
      </div>
      <div className="config-actions">
        <Button appearance="primary" disabled={busy} onClick={() => void save()}>保存</Button>
        <Button disabled={busy} onClick={addRow}>新增</Button>
        {dirty && <Button appearance="subtle" disabled={busy} onClick={() => { if (window.confirm("确定放弃所有未保存的修改？")) void search(true); }}>放弃修改</Button>}
      </div>
    </section>
  );
}
