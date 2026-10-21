import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, patch, post, type QuickResult, type Task } from "../api";
import { Empty, addDays, dayLabel, todayIn } from "../ui";
import { useWorkspace } from "../workspace";
import { DueLabel, PriorityBadge, useTaskInvalidation } from "./common";

/** The same tasks as a table (SPEC: list and table views). */
export function TaskTable({ tasks, onOpen, showProject = true }: {
  tasks: Task[];
  onOpen: (id: string) => void;
  showProject?: boolean;
}) {
  const { current: ws } = useWorkspace();
  const invalidate = useTaskInvalidation();
  const [optimistic, setOptimistic] = useState<Record<string, boolean>>({});
  const toggle = useMutation({
    mutationFn: (t: Task) => patch(`/tasks/${t.id}`, { done: !t.done }),
    onSettled: async (_d, _e, t) => {
      await invalidate();
      setOptimistic(({ [t.id]: _, ...rest }) => rest);
    },
  });
  if (!tasks.length) return <Empty>No tasks match.</Empty>;
  return (
    <div className="card flush">
      <div className="table-wrap">
        <table className="clickable task-table">
          <thead>
            <tr>
              <th aria-label="Done" />
              <th>Task</th>
              {showProject && <th>Project</th>}
              <th>Status</th>
              <th>Priority</th>
              <th>Due</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((t) => (
              <tr key={t.id} onClick={() => onOpen(t.id)} tabIndex={0}
                onKeyDown={(e) => e.key === "Enter" && onOpen(t.id)} className={t.done ? "is-done" : undefined}>
                <td onClick={(e) => e.stopPropagation()}>
                  <input type="checkbox" checked={optimistic[t.id] ?? t.done} aria-label={`Done: ${t.title}`}
                    onChange={() => {
                      setOptimistic((o) => ({ ...o, [t.id]: !t.done }));
                      toggle.mutate(t);
                    }} />
                </td>
                <td>
                  <span className="muted ref">{t.ref}</span> <strong className="tt">{t.title}</strong>
                  {t.blocked && <span className="blocked"> Blocked</span>}
                  {t.labels.map((l) => <span key={l} className="label">#{l}</span>)}
                </td>
                {showProject && (
                  <td>
                    <span className="dot inline-dot" style={{ background: t.colour }} />
                    {t.project_name}
                  </td>
                )}
                <td>{t.status}</td>
                <td><PriorityBadge p={t.priority} /></td>
                <td><DueLabel task={t} timeZone={ws.timezone} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Tasks on a month grid by due date (SPEC: calendar view over the same tasks). */
export function TaskCalendar({ tasks, onOpen }: { tasks: Task[]; onOpen: (id: string) => void }) {
  const { current: ws } = useWorkspace();
  const today = todayIn(ws.timezone);
  const [month, setMonth] = useState(today.slice(0, 7));
  const first = `${month}-01`;
  const startOffset = (new Date(`${first}T12:00:00Z`).getUTCDay() + 6) % 7; // Monday first
  const gridStart = addDays(first, -startOffset);
  const days = Array.from({ length: 42 }, (_, i) => addDays(gridStart, i));
  const byDay = new Map<string, Task[]>();
  for (const t of tasks) if (t.due_date) byDay.set(t.due_date, [...(byDay.get(t.due_date) ?? []), t]);
  const undated = tasks.filter((t) => !t.due_date && !t.done).length;
  const shift = (n: number) => {
    const d = new Date(`${first}T12:00:00Z`);
    d.setUTCMonth(d.getUTCMonth() + n);
    setMonth(d.toISOString().slice(0, 7));
  };
  return (
    <div className="calendar">
      <div className="row cal-nav">
        <button className="ghost" onClick={() => shift(-1)} aria-label="Previous month">‹</button>
        <strong>{dayLabel(first, { month: "long", year: "numeric" })}</strong>
        <button className="ghost" onClick={() => shift(1)} aria-label="Next month">›</button>
        {month !== today.slice(0, 7) && <button className="ghost" onClick={() => setMonth(today.slice(0, 7))}>Today</button>}
        {undated > 0 && <span className="muted small">{undated} open task(s) have no due date</span>}
      </div>
      <div className="cal-grid" role="grid">
        {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => (
          <div key={d} className="cal-head" role="columnheader">{d}</div>
        ))}
        {days.map((d) => (
          <div key={d} role="gridcell"
            className={`cal-day${d.slice(0, 7) !== month ? " other" : ""}${d === today ? " today" : ""}`}>
            <span className="cal-num">{Number(d.slice(8))}</span>
            {(byDay.get(d) ?? []).map((t) => (
              <button key={t.id} className={`cal-task${t.done ? " is-done" : ""}${t.overdue ? " overdue" : ""}`}
                style={{ borderLeftColor: t.colour }} onClick={() => onOpen(t.id)} title={`${t.ref} ${t.title}`}>
                {t.due_time && <span className="muted">{t.due_time.slice(0, 5)} </span>}
                {t.title}
              </button>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/** "Fix VPN for Acme tomorrow !high #network" with a live preview of what it understood. */
export function QuickAdd({ projectId, onCreated }: { projectId?: string; onCreated?: (t: Task) => void }) {
  const { current: ws } = useWorkspace();
  const invalidate = useTaskInvalidation();
  const [text, setText] = useState("");
  const [debounced, setDebounced] = useState("");
  const [choice, setChoice] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(text), 250);
    return () => clearTimeout(t);
  }, [text]);
  const preview = useQuery({
    queryKey: ["quick-preview", ws.id, projectId, debounced],
    queryFn: () => post<QuickResult>("/tasks/quick", {
      text: debounced, workspace_id: ws.id, project_id: projectId ?? null, dry_run: true,
    }),
    enabled: debounced.trim().length > 0,
  });
  const create = useMutation({
    mutationFn: () => post<QuickResult>("/tasks/quick", {
      text, workspace_id: ws.id, project_id: choice || projectId || null,
    }),
    onSuccess: (r) => {
      if (r.task) {
        setText("");
        setChoice("");
        invalidate();
        onCreated?.(r.task);
      }
    },
  });
  const p = text.trim() && debounced === text ? preview.data : undefined;
  const needsProject = p?.needs_confirmation && !choice;
  return (
    <form className="quick-add" onSubmit={(e) => { e.preventDefault(); if (text.trim() && !needsProject) create.mutate(); }}>
      <input value={text} onChange={(e) => setText(e.target.value)} aria-label="Quick add task"
        placeholder={projectId ? "Add a task… e.g. Patch firmware fri !high #network"
          : "Add a task… e.g. Fix VPN for Acme tomorrow !high #network"} />
      {p && (
        <div className="quick-preview small">
          <span><strong>{p.title}</strong></span>
          {p.project_name && <span className="chip">{p.project_name}</span>}
          {p.due_date && <span className="chip">{dayLabel(p.due_date, { weekday: "short", month: "short", day: "numeric" })}{p.due_time ? ` ${p.due_time.slice(0, 5)}` : ""}</span>}
          {p.priority && <span className="chip">!{p.priority}</span>}
          {p.labels.map((l) => <span key={l} className="chip">#{l}</span>)}
          {p.needs_confirmation && (
            <select aria-label="Pick a project" value={choice} onChange={(e) => setChoice(e.target.value)}>
              <option value="">{p.candidates.length ? `Which ${p.matched} project?` : "Pick a project…"}</option>
              {(p.candidates.length ? p.candidates : []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              {!p.candidates.length && <ProjectOptions />}
            </select>
          )}
        </div>
      )}
      <button disabled={!text.trim() || needsProject || create.isPending}>Add</button>
    </form>
  );
}

function ProjectOptions() {
  const { current: ws } = useWorkspace();
  const q = useQuery({
    queryKey: ["projects", ws.id, ""],
    queryFn: () => api<{ id: string; name: string; status: string }[]>(`/projects?workspace_id=${ws.id}`),
  });
  return <>{q.data?.filter((p) => p.status !== "done").map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</>;
}
