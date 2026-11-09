let csrfToken: string | null = null;

export function setCsrfToken(value: string | null) {
  csrfToken = value;
}

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  const method = (init.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    if (!csrfToken) {
      throw new Error("登录状态已失效，请重新登录。");
    }
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(input, {
    ...init,
    headers,
    credentials: "include",
  });
  if (response.status === 401) {
    csrfToken = null;
    window.dispatchEvent(new Event("auth-expired"));
  }
  return response;
}
