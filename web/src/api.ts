// Thin fetch wrapper. The browser authenticates with the HTTP-only session cookie;
// every request carries X-Requested-With so the API's CSRF guard accepts writes.

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
  }
}

export async function api<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("X-Requested-With", "tahgul");
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

  const res = await fetch(`/api/v1${path}`, { ...options, headers, credentials: "same-origin" });
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, body?.detail ?? body);
  return body as T;
}

export const post = <T = unknown>(path: string, data?: unknown) =>
  api<T>(path, { method: "POST", body: data === undefined ? undefined : JSON.stringify(data) });

export const del = <T = unknown>(path: string) => api<T>(path, { method: "DELETE" });

export type Me = {
  kind: "owner" | "token";
  name: string;
  email: string | null;
  scopes: string[];
  totp_enabled: boolean | null;
};

export type ApiToken = {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
};

export const TOKEN_SCOPES = ["read", "write", "invoice"] as const;

const MESSAGES: Record<string, string> = {
  invalid_credentials: "That email and password don't match.",
  invalid_totp: "That code didn't work. Codes change every 30 seconds.",
  too_many_attempts: "Too many failed attempts. Wait a few minutes and try again.",
  invalid_password: "Current password is incorrect.",
};

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (typeof err.detail === "string") return MESSAGES[err.detail] ?? err.detail;
    if (Array.isArray(err.detail)) return err.detail.map((d) => d.msg).join("; ");
  }
  return "Something went wrong. Check the connection and try again.";
}
