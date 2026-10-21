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

export const put = <T = unknown>(path: string, data: unknown) =>
  api<T>(path, { method: "PUT", body: JSON.stringify(data) });

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
  rounding_minutes: number;
  rounding_mode: "up" | "nearest" | "down";
  timer_warning_minutes: number;
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
  end_before_start: "The end has to be after the start.",
  entry_too_long: "An entry can't be longer than a week.",
  entry_invoiced: "This time is on an invoice, so it's locked.",
  start_in_future: "The start can't be in the future.",
  no_timer_running: "No timer is running.",
  link_cycle: "That would make the two tasks wait on each other.",
  parent_cycle: "A task can't be inside its own subtask.",
  column_has_tasks: "Move this column's tasks somewhere first.",
  last_column: "A board needs at least one column.",
  cannot_shrink_tracked_time:
    "That would cut into timer entries. Edit those entries directly instead.",
};

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (typeof err.detail === "string") return MESSAGES[err.detail] ?? err.detail;
    const d = err.detail as { code?: string; choices?: string[] } | null;
    if (d?.code === "unknown_status") return `Unknown status. Use one of: ${d.choices?.join(", ")}.`;
    if (Array.isArray(err.detail))
      return err.detail
        .map((d) => `${d.loc?.slice(-1)[0] ?? ""}: ${String(d.msg).replace(/^Value error, /, "")}`)
        .join("; ");
  }
  return "Something went wrong. Check the connection and try again.";
}

export type TimeEntry = {
  id: string;
  workspace_id: string;
  project_id: string | null;
  project_name: string | null;
  client_id: string | null;
  client_name: string | null;
  activity_id: string | null;
  activity_name: string | null;
  start_at: string;
  end_at: string | null;
  duration_seconds: number;
  hours: string;
  notes: string | null;
  billable: boolean;
  rate_override: string | null;
  rate: string | null;
  rate_source: string | null;
  amount: string | null;
  source: string;
  invoice_id: string | null;
  running: boolean;
  overlaps: string[] | null;
};

export type Timer = {
  running: TimeEntry | null;
  elapsed_seconds: number;
  long_running: boolean;
  warning_after_minutes: number | null;
  stopped: TimeEntry | null;
};

export type WeekRow = {
  project_id: string | null;
  project_name: string | null;
  client_name: string | null;
  colour: string | null;
  activity_id: string | null;
  activity_name: string | null;
  hours: string[];
  total: string;
};

export type Week = {
  workspace_id: string;
  week_start: string;
  days: string[];
  rows: WeekRow[];
  day_totals: string[];
  total: string;
  running_entry_id: string | null;
};

export type Totals = {
  hours: string;
  billable_hours: string;
  amount: string;
  unbilled_amount: string;
  effective_rate: string | null;
};

export type Summary = {
  workspace_id: string;
  currency: string;
  from: string;
  to: string;
  totals: Totals;
  by_client: (Totals & { client_id: string | null; name: string })[];
  by_project: (Totals & {
    project_id: string | null;
    name: string;
    client_name: string | null;
    colour: string | null;
    budget_hours: string | null;
    budget_amount: string | null;
    used_hours_all_time: string;
    used_amount_all_time: string;
    budget_hours_left: string | null;
    budget_amount_left: string | null;
  })[];
  by_week: (Totals & { week_start: string })[];
  entry_count: number;
};

export type Column = {
  id: string;
  project_id: string;
  name: string;
  position: number;
  is_done: boolean;
  task_count: number;
};

export const TASK_TYPES = ["task", "bug", "idea", "request"] as const;
export const PRIORITIES = ["urgent", "high", "medium", "low", "none"] as const;
export type Priority = (typeof PRIORITIES)[number];

export type Task = {
  id: string;
  number: number;
  ref: string;
  workspace_id: string;
  project_id: string;
  project_name: string;
  client_name: string | null;
  colour: string;
  column_id: string;
  status: string;
  done: boolean;
  title: string;
  description: string | null;
  type: (typeof TASK_TYPES)[number];
  priority: Priority;
  labels: string[];
  due_date: string | null;
  due_time: string | null;
  overdue: boolean;
  estimate_minutes: number | null;
  parent_id: string | null;
  position: number;
  completed_at: string | null;
  subtask_count: number;
  subtasks_done: number;
  blocked_by: string[];
  blocks: string[];
  blocked: boolean;
  tracked_seconds: number;
  comment_count: number;
  created_at: string;
  updated_at: string;
};

export type TaskDetail = Task & { subtasks: Task[]; blockers: Task[]; blocking: Task[] };

export type HistoryItem = {
  kind: "comment" | "change";
  at: string;
  actor_type: string;
  actor_name: string;
  action: string | null;
  changes: Record<string, unknown> | null;
  body: string | null;
  comment_id: string | null;
};

export type QuickResult = {
  title: string;
  priority: string | null;
  labels: string[];
  due_date: string | null;
  due_time: string | null;
  project_id: string | null;
  project_name: string | null;
  matched: string | null;
  needs_confirmation: boolean;
  candidates: { id: string; name: string }[];
  task: Task | null;
};

export type TaskFilters = {
  project?: string;
  client?: string;
  status?: string;
  open?: boolean;
  due_before?: string;
  due_after?: string;
  overdue?: boolean;
  label?: string;
  priority?: string[];
  type?: string[];
  q?: string;
  top_level?: boolean;
  sort?: "board" | "due" | "priority" | "created" | "updated" | "number";
  due_within_days?: number;
};

export type SavedView = {
  id: string;
  workspace_id: string;
  name: string;
  filters: TaskFilters;
  position: number;
};

/** Filters → query string; due_within_days becomes due_before relative to today. */
export function taskQuery(f: TaskFilters, today: string, extra: Record<string, string> = {}) {
  const p = new URLSearchParams(extra);
  for (const [k, v] of Object.entries(f)) {
    if (v === undefined || v === "" || v === null || k === "due_within_days") continue;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, String(x)));
    else p.set(k, String(v));
  }
  if (f.due_within_days !== undefined && !f.due_before) {
    const d = new Date(`${today}T12:00:00Z`);
    d.setUTCDate(d.getUTCDate() + Number(f.due_within_days));
    p.set("due_before", d.toISOString().slice(0, 10));
  }
  return p.toString();
}
