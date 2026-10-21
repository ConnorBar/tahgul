import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import {
  PRIORITIES,
  api,
  del,
  errorMessage,
  post,
  taskQuery,
  type Client,
  type SavedView,
  type Task,
  type TaskFilters,
} from "../api";
import { PRIORITY_LABEL } from "../tasks/common";
import { TaskDrawer } from "../tasks/TaskDrawer";
import { QuickAdd, TaskCalendar, TaskTable } from "../tasks/views";
import { todayIn } from "../ui";
import { useWorkspace } from "../workspace";

// Built-in views; saved ones appear after these.
const BUILT_IN: { key: string; name: string; filters: TaskFilters }[] = [
  { key: "open", name: "All open", filters: { open: true, top_level: true, sort: "due" } },
  { key: "week", name: "Due this week", filters: { open: true, due_within_days: 7, sort: "due" } },
  { key: "overdue", name: "Overdue", filters: { overdue: true, sort: "due" } },
  { key: "urgent", name: "High priority", filters: { open: true, priority: ["urgent", "high"], sort: "priority" } },
  { key: "done", name: "Recently done", filters: { open: false, sort: "updated" } },
];

/** URL params ↔ filters, so a view is a shareable link. */
function readFilters(p: URLSearchParams): TaskFilters {
  const f: TaskFilters = {};
  for (const k of ["project", "client", "status", "label", "q", "due_before", "due_after"] as const) {
    const v = p.get(k);
    if (v) f[k] = v;
  }
  for (const k of ["open", "overdue", "top_level"] as const) {
    const v = p.get(k);
    if (v !== null) f[k] = v === "true";
  }
  const prio = p.getAll("priority");
  if (prio.length) f.priority = prio;
  const dw = p.get("due_within_days");
  if (dw) f.due_within_days = Number(dw);
  const sort = p.get("sort");
  if (sort) f.sort = sort as TaskFilters["sort"];
  return f;
}

function writeFilters(f: TaskFilters, extra: Record<string, string> = {}) {
  const p = new URLSearchParams(extra);
  for (const [k, v] of Object.entries(f)) {
    if (v === undefined || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, String(x)));
    else p.set(k, String(v));
  }
  return p;
}

export default function Tasks() {
  const { current: ws } = useWorkspace();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const today = todayIn(ws.timezone);
  const display = params.get("display") === "calendar" ? "calendar" : "list";
  const viewKey = params.get("view") ?? (params.toString() ? "" : "open");
  const builtIn = BUILT_IN.find((b) => b.key === viewKey);
  const saved = useQuery({ queryKey: ["views", ws.id], queryFn: () => api<SavedView[]>(`/views?workspace_id=${ws.id}`) });
  const savedView = saved.data?.find((v) => v.id === viewKey);
  const filters: TaskFilters = builtIn?.filters ?? savedView?.filters ?? readFilters(params);

  const tasks = useQuery({
    queryKey: ["tasks", "list", ws.id, filters],
    queryFn: () => api<Task[]>(`/tasks?${taskQuery(filters, today, { workspace_id: ws.id })}`),
  });
  const clients = useQuery({ queryKey: ["clients", ws.id], queryFn: () => api<Client[]>(`/clients?workspace_id=${ws.id}`) });

  const setFilter = (patch: Partial<TaskFilters>) => {
    const next = { ...filters, ...patch };
    for (const k of Object.keys(next) as (keyof TaskFilters)[]) if (next[k] === undefined || next[k] === "") delete next[k];
    setParams(writeFilters(next, display === "calendar" ? { display } : {}));
  };
  const open = (id: string | null) => {
    const next = new URLSearchParams(params);
    if (id) next.set("task", id);
    else next.delete("task");
    setParams(next, { replace: true });
  };
  const setView = (key: string) => setParams(new URLSearchParams({ view: key, ...(display === "calendar" ? { display } : {}) }));

  const saveView = useMutation({
    mutationFn: (name: string) => post<SavedView>("/views", { workspace_id: ws.id, name, filters }),
    onSuccess: (v) => { qc.invalidateQueries({ queryKey: ["views"] }); setView(v.id); },
  });
  const deleteView = useMutation({
    mutationFn: (id: string) => del(`/views/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["views"] }); setView("open"); },
  });

  const [showFilters, setShowFilters] = useState(false);
  const activeName = builtIn?.name ?? savedView?.name ?? "Custom filter";
  const list = useMemo(() => tasks.data ?? [], [tasks.data]);

  return (
    <section className="page wide">
      <div className="page-head">
        <h1>Tasks</h1>
        <nav className="tabs compact" aria-label="Display">
          {(["list", "calendar"] as const).map((d) => (
            <a key={d} href={`?display=${d}`} className={display === d ? "active" : undefined}
              onClick={(e) => {
                e.preventDefault();
                const next = new URLSearchParams(params);
                if (d === "list") next.delete("display");
                else next.set("display", d);
                setParams(next);
              }}>
              {d === "list" ? "List" : "Calendar"}
            </a>
          ))}
        </nav>
      </div>
      <QuickAdd onCreated={(t) => open(t.id)} />

      <div className="views">
        {BUILT_IN.map((b) => (
          <button key={b.key} className={`view-chip${viewKey === b.key ? " on" : ""}`} onClick={() => setView(b.key)}>
            {b.name}
          </button>
        ))}
        {saved.data?.map((v) => (
          <span key={v.id} className={`view-chip saved${viewKey === v.id ? " on" : ""}`}>
            <button className="linkish" onClick={() => setView(v.id)}>{v.name}</button>
            {viewKey === v.id && (
              <button className="icon" aria-label={`Delete view ${v.name}`}
                onClick={() => confirm(`Delete the "${v.name}" view?`) && deleteView.mutate(v.id)}>×</button>
            )}
          </span>
        ))}
      </div>

      <button className="ghost filters-toggle" aria-expanded={showFilters}
        onClick={() => setShowFilters((v) => !v)}>
        {showFilters ? "Hide filters" : "Filters"}
      </button>
      <div className={`filters row${showFilters ? "" : " collapsed"}`}>
        <input type="search" placeholder="Search" aria-label="Search tasks" defaultValue={filters.q ?? ""}
          key={`q-${viewKey}`} onKeyDown={(e) => e.key === "Enter" && setFilter({ q: (e.target as HTMLInputElement).value })} />
        <select aria-label="Client" value={filters.client ?? ""} onChange={(e) => setFilter({ client: e.target.value })}>
          <option value="">All clients</option>
          {clients.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <select aria-label="Priority" value={filters.priority?.[0] ?? ""}
          onChange={(e) => setFilter({ priority: e.target.value ? [e.target.value] : undefined })}>
          <option value="">Any priority</option>
          {PRIORITIES.filter((p) => p !== "none").map((p) => <option key={p} value={p}>{PRIORITY_LABEL[p]}</option>)}
        </select>
        <select aria-label="Done" value={filters.open === undefined ? "" : String(filters.open)}
          onChange={(e) => setFilter({ open: e.target.value === "" ? undefined : e.target.value === "true" })}>
          <option value="">Open and done</option>
          <option value="true">Open</option>
          <option value="false">Done</option>
        </select>
        <select aria-label="Sort" value={filters.sort ?? "board"} onChange={(e) => setFilter({ sort: e.target.value as TaskFilters["sort"] })}>
          <option value="due">Sort: due date</option>
          <option value="priority">Sort: priority</option>
          <option value="updated">Sort: recently updated</option>
          <option value="created">Sort: newest</option>
          <option value="board">Sort: board order</option>
        </select>
        {!savedView && (
          <button className="ghost" onClick={() => {
            const name = prompt("Save this filter as a view", builtIn ? `${builtIn.name} (copy)` : "");
            if (name?.trim()) saveView.mutate(name.trim());
          }}>Save view</button>
        )}
      </div>
      {saveView.isError && <p className="error">{errorMessage(saveView.error)}</p>}
      <p className="muted small">{activeName} · {list.length} task{list.length === 1 ? "" : "s"}</p>

      {display === "list" ? <TaskTable tasks={list} onOpen={open} /> : <TaskCalendar tasks={list} onOpen={open} />}
      <TaskDrawer taskId={params.get("task")} onClose={() => open(null)} onOpen={open} />
    </section>
  );
}
