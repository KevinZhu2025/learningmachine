import { useState } from "react";
import type { FormEvent } from "react";
import { Button, Input, Spinner } from "@fluentui/react-components";

type AuthResponse = {
  userId: string;
  csrfToken: string;
  isAdmin: boolean;
};

export default function AuthPage({
  onAuthenticated,
  initialError = "",
}: {
  onAuthenticated: (user: AuthResponse) => void;
  initialError?: string;
}) {
  const [registering, setRegistering] = useState(false);
  const [userId, setUserId] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(initialError);
  const [message, setMessage] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch(`/api/auth/${registering ? "register" : "login"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ userId, password }),
      });
      const body = await response.json();
      if (!response.ok) {
        throw new Error(typeof body.detail === "string" ? body.detail : "认证失败，请检查输入后重试。");
      }
      if (registering) {
        setRegistering(false);
        setPassword("");
        setMessage("账户已创建，请登录。");
      } else {
        onAuthenticated(body as AuthResponse);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "认证服务暂不可用。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="brand-mark auth-brand-mark">学</div>
        <span className="section-kicker">好玩儿科技</span>
        <h1 id="auth-title">{registering ? "创建账户" : "欢迎回来"}</h1>
        <p>{registering ? "使用用户名和密码注册学习账户。" : "登录后继续整理和复习你的知识。"}</p>
        {error && <div className="error-banner" role="alert">{error}</div>}
        {message && <p className="auth-message" role="status">{message}</p>}
        <form className="auth-form" onSubmit={(event) => void submit(event)}>
          <label className="field">
            <span>用户名</span>
            <Input
              autoComplete="username"
              maxLength={100}
              required
              value={userId}
              onChange={(_, data) => setUserId(data.value)}
              disabled={busy}
            />
          </label>
          <label className="field">
            <span>密码</span>
            <Input
              type="password"
              autoComplete={registering ? "new-password" : "current-password"}
              minLength={registering ? 12 : 1}
              maxLength={128}
              required
              value={password}
              onChange={(_, data) => setPassword(data.value)}
              disabled={busy}
            />
            {registering && <small>密码长度为 12–128 个字符。</small>}
          </label>
          <Button appearance="primary" type="submit" disabled={busy}>
            {busy ? <Spinner size="tiny" /> : registering ? "注册" : "登录"}
          </Button>
        </form>
        <button
          className="auth-switch"
          type="button"
          disabled={busy}
          onClick={() => {
            setRegistering((current) => !current);
            setError("");
            setMessage("");
          }}
        >
          {registering ? "已有账户？返回登录" : "没有账户？注册"}
        </button>
      </section>
    </main>
  );
}
