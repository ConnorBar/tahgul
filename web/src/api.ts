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

export const patch = <T = unknown>(path: string, data: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(data) });

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

export const TOKEN_SCOPES = ["read", "write", "invoice", "admin"] as const;

// Money, rates and hours arrive as decimal strings ("150.00").
export type Workspace = {
  id: string;
  name: string;
  currency: string;
  default_rate: string | null;
  tax_rate: string | null;
  payment_terms_days: number;
  invoice_number_pattern: string;
  timezone: string;
};

export type Client = {
  id: string;
  workspace_id: string;
  name: string;
  code: string | null;
  contact_email: string | null;
  billing_address: string | null;
  rate: string | null;
  payment_terms_days: number | null;
  tax_rate: string | null;
  notes: string | null;
  project_count: number;
};

export const PROJECT_STATUSES = ["active", "paused", "idea", "done"] as const;
export type ProjectStatus = (typeof PROJECT_STATUSES)[number];

export type Project = {
  id: string;
  workspace_id: string;
  client_id: string | null;
  client_name: string | null;
  name: string;
  description: string | null;
  status: ProjectStatus;
  rate: string | null;
  budget_hours: string | null;
  budget_amount: string | null;
  colour: string;
  effective_rate: string | null;
  rate_source: "project" | "client" | "workspace" | null;
};

export type Activity = {
  id: string;
  workspace_id: string;
  name: string;
  rate: string | null;
  billable: boolean;
};

export type RateInfo = {
  rate: string | null;
  source: string | null;
  currency: string;
  levels: { level: string; name: string | null; rate: string | null }[];
};

export type NumberPreset = { pattern: string; label: string; resets: string; examples: string[] };
export type PatternCheck = { pattern: string; valid: boolean; errors: string[]; examples: string[] };

const MESSAGES: Record<string, string> = {
  invalid_credentials: "That email and password don't match.",
  invalid_totp: "That code didn't work. Codes change every 30 seconds.",
  too_many_attempts: "Too many failed attempts. Wait a few minutes and try again.",
  invalid_password: "Current password is incorrect.",
  name_taken: "That name is already used here.",
  client_has_projects: "This client still has projects. Move or delete them first.",
  workspace_not_empty: "This workspace still has clients, projects or activities.",
  workspace_required: "Pick a workspace first.",
  client_in_other_workspace: "That client belongs to another workspace.",
};

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (typeof err.detail === "string") return MESSAGES[err.detail] ?? err.detail;
    if (Array.isArray(err.detail))
      return err.detail
        .map((d) => `${d.loc?.slice(-1)[0] ?? ""}: ${String(d.msg).replace(/^Value error, /, "")}`)
        .join("; ");
  }
  return "Something went wrong. Check the connection and try again.";
}
