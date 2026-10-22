import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  PRIORITIES,
  TASK_TYPES,
  api,
  del,
  errorMessage,
  patch,
  post,
  type Column,
  type HistoryItem,
  type Task,
  type TaskDetail,
} from "../api";
import { Markdown } from "../markdown";
import { ScopeSelect } from "../scopes";
import { AddVoiceNote, VoiceNotes, useVoiceNotes } from "../voice";
import { useTimeInvalidation } from "../timer";
import { Dialog, hm } from "../ui";
import { useWorkspace } from "../workspace";
import { PRIORITY_LABEL, TaskMeta, useTaskInvalidation } from "./common";
import { Select } from "../Select";

export function TaskDrawer({ taskId, onClose, onOpen }: {
  taskId: string | null;
  onClose: () => void;
  onOpen: (id: string) => void;
}) {
  return (
    <Dialog open={taskId !== null} title="Task" onClose={onClose}>
      {taskId && <TaskBody key={taskId} id={taskId} onClose={onClose} onOpen={onOpen} />}
    </Dialog>
  );
}

function TaskBody({ id, onClose, onOpen }: { id: string; onClose: () => void; onOpen: (id: string) => void }) {
  const { current: ws } = useWorkspace();
  const invalidate = useTaskInvalidation();
  const invalidateTime = useTimeInvalidation();
  const q = useQuery({ queryKey: ["task", id], queryFn: () => api<TaskDetail>(`/tasks/${id}`) });
  const t = q.data;
  const cols = useQuery({
    queryKey: ["columns", t?.project_id],
    queryFn: () => api<Column[]>(`/projects/${t!.project_id}/columns`),
    enabled: !!t,
  });

  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => patch<Task>(`/tasks/${id}`, body),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: () => del(`/tasks/${id}`),
    onSuccess: async () => {
      await invalidate();
      onClose();
    },
  });
  const timer = useMutation({
    mutationFn: () => post("/timer/start", { task_id: id }),
    onSuccess: invalidateTime,
  });
  const block = useMutation({
    mutationFn: (on: boolean) => (on ? post(`/tasks/${id}/block`) : del(`/tasks/${id}/block`)),
    onSuccess: invalidate,
  });

  if (!t) return <p className="muted">Loading…</p>;

  return (
    <div className="task-body">
      <div className="task-main">
        <div className="task-crumbs muted small">
          <span className="dot inline-dot" style={{ background: t.colour }} />
          {t.project_name}
          {t.client_name && ` · ${t.client_name}`} · {t.ref}
          {t.parent_id && (
            <>
              {" · "}
              <button className="linkish" onClick={() => onOpen(t.parent_id!)}>parent task</button>
            </>
          )}
        </div>
        <TitleEdit value={t.title} onSave={(title) => save.mutate({ title })} done={t.done} />
        <TaskMeta task={t} timeZone={ws.timezone} />
        <Description value={t.description} onSave={(description) => save.mutate({ description })} />
        <Subtasks parent={t} onOpen={onOpen} />
        <TaskNotes taskId={t.id} />
        <Blockers task={t} onOpen={onOpen} />
        <Timeline taskId={id} />
      </div>

      <aside className="task-side">
        <label>
          Status
          <Select aria-label="Status" value={t.column_id}
            onChange={(e) => save.mutate({ status: e.target.value })}>
            {cols.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select>
        </label>
        <label>
          Priority
          <Select aria-label="Priority" value={t.priority}
            onChange={(e) => save.mutate({ priority: e.target.value })}>
            {PRIORITIES.map((p) => <option key={p} value={p}>{PRIORITY_LABEL[p]}</option>)}
          </Select>
        </label>
        <label>
          Type
          <Select aria-label="Type" value={t.type} onChange={(e) => save.mutate({ type: e.target.value })}>
            {TASK_TYPES.map((x) => <option key={x} value={x}>{x[0].toUpperCase() + x.slice(1)}</option>)}
          </Select>
        </label>
        <ScopeSelect projectId={t.project_id} value={t.scope_id ?? ""}
          onChange={(v) => save.mutate({ scope_id: v || null })} />
        <label>
          Due date
          <input type="date" value={t.due_date ?? ""}
            onChange={(e) => save.mutate({ due_date: e.target.value || null, ...(e.target.value ? {} : { due_time: null }) })} />
        </label>
        {t.due_date && (
          <label>
            Due time
            <input type="time" value={t.due_time?.slice(0, 5) ?? ""}
              onChange={(e) => save.mutate({ due_time: e.target.value || null })} />
          </label>
        )}
        <label>
          Estimate (h)
          <BlurInput value={t.estimate_minutes ? String(t.estimate_minutes / 60) : ""}
            onSave={(v) => save.mutate({ estimate_minutes: v ? Math.round(Number(v) * 60) : null })}
            inputMode="decimal" />
        </label>
        <label>
          Labels
          <BlurInput value={t.labels.join(", ")} placeholder="network, vpn"
            onSave={(v) => save.mutate({ labels: v.split(/[,\s]+/).filter(Boolean) })} />
        </label>
        <div className="side-stat muted small">
          Logged {hm(t.tracked_seconds)}
          {t.estimate_minutes ? ` of ${hm(t.estimate_minutes * 60)}` : ""}
        </div>
        <button onClick={() => timer.mutate()} disabled={timer.isPending}>▶ Start timer</button>
        {t.due_date && t.due_time && (
          <button className="ghost" disabled={block.isPending}
            onClick={() => block.mutate(!t.calendar_block_id)}
            title="A time block on your calendar at the due time, as long as the estimate">
            {t.calendar_block_id ? "Remove from calendar" : "Block time on calendar"}
          </button>
        )}
        {(save.isError || timer.isError || remove.isError || block.isError) && (
          <p className="error small">{errorMessage(save.error ?? timer.error ?? remove.error ?? block.error)}</p>
        )}
        <button className="danger" disabled={remove.isPending}
          onClick={() => confirm(`Delete ${t.ref} and its subtasks?`) && remove.mutate()}>
          Delete
        </button>
      </aside>
    </div>
  );
}

function BlurInput({ value, onSave, ...rest }: { value: string; onSave: (v: string) => void } &
  Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <input {...rest} value={v} onChange={(e) => setV(e.target.value)}
      onBlur={() => v.trim() !== value && onSave(v.trim())}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
  );
}

function TitleEdit({ value, onSave, done }: { value: string; onSave: (v: string) => void; done: boolean }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <textarea className={`title-edit${done ? " is-done" : ""}`} aria-label="Title" rows={1} value={v}
      onChange={(e) => setV(e.target.value.replace(/\n/g, " "))}
      onBlur={() => v.trim() && v.trim() !== value && onSave(v.trim())}
      onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), (e.target as HTMLTextAreaElement).blur())} />
  );
}

function Description({ value, onSave }: { value: string | null; onSave: (v: string | null) => void }) {
  const [editing, setEditing] = useState(false);
  const [v, setV] = useState(value ?? "");
  useEffect(() => setV(value ?? ""), [value]);
  if (editing)
    return (
      <div className="desc">
        <textarea rows={8} value={v} autoFocus aria-label="Description"
          onChange={(e) => setV(e.target.value)} placeholder="Markdown supported" />
        <div className="row">
          <button onClick={() => { onSave(v.trim() || null); setEditing(false); }}>Save</button>
          <button className="ghost" onClick={() => { setV(value ?? ""); setEditing(false); }}>Cancel</button>
        </div>
      </div>
    );
  return (
    <div className="desc clickable" role="button" tabIndex={0} onClick={() => setEditing(true)}
      onKeyDown={(e) => e.key === "Enter" && setEditing(true)} aria-label="Edit description">
      {value ? <Markdown text={value} /> : <p className="muted">Add a description…</p>}
    </div>
  );
}

function Subtasks({ parent, onOpen }: { parent: TaskDetail; onOpen: (id: string) => void }) {
  const invalidate = useTaskInvalidation();
  const [title, setTitle] = useState("");
  const add = useMutation({
    mutationFn: () => post("/tasks", { project_id: parent.project_id, parent_id: parent.id, title }),
    onSuccess: () => { setTitle(""); invalidate(); },
  });
  // Tick immediately; the server catches up.
  const [optimistic, setOptimistic] = useState<Record<string, boolean>>({});
  const setDone = useMutation({
    mutationFn: ({ s, done }: { s: Task; done: boolean }) => patch(`/tasks/${s.id}`, { done }),
    onSettled: async (_d, _e, { s }) => {
      await invalidate();
      setOptimistic(({ [s.id]: _, ...rest }) => rest);
    },
  });
  return (
    <section className="task-section">
      <h3>Subtasks {parent.subtask_count > 0 && <span className="muted">{parent.subtasks_done}/{parent.subtask_count}</span>}</h3>
      <ul className="subtasks">
        {parent.subtasks.map((s) => (
          <li key={s.id}>
            <input type="checkbox" checked={optimistic[s.id] ?? s.done} aria-label={`Done: ${s.title}`}
              onChange={(e) => {
                setOptimistic((o) => ({ ...o, [s.id]: e.target.checked }));
                setDone.mutate({ s, done: e.target.checked });
              }} />
            <button className={`linkish${s.done ? " is-done" : ""}`} onClick={() => onOpen(s.id)}>{s.title}</button>
            <span className="muted small">{s.status}</span>
          </li>
        ))}
      </ul>
      <form className="row" onSubmit={(e) => { e.preventDefault(); if (title.trim()) add.mutate(); }}>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Add a subtask"
          aria-label="New subtask" />
        <button className="ghost" disabled={!title.trim() || add.isPending}>Add</button>
      </form>
    </section>
  );
}

function Blockers({ task, onOpen }: { task: TaskDetail; onOpen: (id: string) => void }) {
  const invalidate = useTaskInvalidation();
  const candidates = useQuery({
    queryKey: ["tasks", "project-open", task.project_id],
    queryFn: () => api<Task[]>(`/tasks?project=${task.project_id}&open=true&sort=number`),
  });
  const [pick, setPick] = useState("");
  const add = useMutation({
    mutationFn: () => post(`/tasks/${task.id}/blocked-by`, { task_id: pick }),
    onSuccess: () => { setPick(""); invalidate(); },
  });
  const remove = useMutation({
    mutationFn: (bid: string) => del(`/tasks/${task.id}/blocked-by/${bid}`),
    onSuccess: invalidate,
  });
  const options = (candidates.data ?? []).filter(
    (c) => c.id !== task.id && !task.blocked_by.includes(c.id),
  );
  return (
    <section className="task-section">
      <h3>Blocked by</h3>
      {task.blockers.length === 0 && <p className="muted small">Nothing. This task can start.</p>}
      <ul className="subtasks">
        {task.blockers.map((b) => (
          <li key={b.id}>
            <span className={b.done ? "ok" : "blocked"}>{b.done ? "✓" : "●"}</span>
            <button className="linkish" onClick={() => onOpen(b.id)}>{b.ref} {b.title}</button>
            <button className="icon" aria-label={`Remove blocker ${b.ref}`} onClick={() => remove.mutate(b.id)}>×</button>
          </li>
        ))}
      </ul>
      {task.blocking.length > 0 && (
        <p className="muted small">
          Blocks: {task.blocking.map((b) => b.ref).join(", ")}
        </p>
      )}
      {options.length > 0 && (
        <div className="row">
          <Select aria-label="Add blocker" value={pick} onChange={(e) => setPick(e.target.value)}>
            <option value="">Add a task this waits on…</option>
            {options.map((o) => <option key={o.id} value={o.id}>{o.ref} {o.title}</option>)}
          </Select>
          <button className="ghost" disabled={!pick || add.isPending} onClick={() => add.mutate()}>Add</button>
        </div>
      )}
      {add.isError && <p className="error small">{errorMessage(add.error)}</p>}
    </section>
  );
}

function describe(h: HistoryItem): string {
  if (h.action === "create") return "created this task";
  if (h.action === "link") return `marked it blocked by ${String(h.changes?.blocked_by ?? "")}`;
  if (h.action === "unlink") return "removed a blocker";
  if (h.action === "delete") return "deleted it";
  const parts = Object.entries(h.changes ?? {}).map(([k, v]) => {
    if (Array.isArray(v)) {
      const [, to] = v as unknown[];
      if (k === "description") return "edited the description";
      return `set ${k.replace("_", " ")} to ${to === null || to === "" ? "nothing" : String(to)}`;
    }
    return `changed ${k}`;
  });
  return parts.join(", ") || "updated it";
}

function Timeline({ taskId }: { taskId: string }) {
  const invalidate = useTaskInvalidation();
  const hist = useQuery({
    queryKey: ["history", taskId],
    queryFn: () => api<HistoryItem[]>(`/tasks/${taskId}/history`),
  });
  const [body, setBody] = useState("");
  const add = useMutation({
    mutationFn: () => post(`/tasks/${taskId}/comments`, { body }),
    onSuccess: () => { setBody(""); invalidate(); },
  });
  return (
    <section className="task-section">
      <h3>Activity</h3>
      <ol className="timeline">
        {hist.data?.map((h, i) => (
          <li key={i} className={h.kind}>
            <span className={`who who-${h.actor_type}`}>{h.actor_type === "owner" ? "You" : h.actor_name}</span>
            {h.kind === "comment" ? (
              <div className="comment"><Markdown text={h.body ?? ""} /></div>
            ) : (
              <span className="muted"> {describe(h)}</span>
            )}
            <time className="muted small" dateTime={h.at}>
              {new Date(h.at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}
            </time>
          </li>
        ))}
      </ol>
      <form onSubmit={(e) => { e.preventDefault(); if (body.trim()) add.mutate(); }}>
        <textarea rows={2} value={body} onChange={(e) => setBody(e.target.value)}
          placeholder="Write a comment (Markdown)" aria-label="Comment" />
        <button disabled={!body.trim() || add.isPending}>Comment</button>
      </form>
    </section>
  );
}

function TaskNotes({ taskId }: { taskId: string }) {
  const notes = useVoiceNotes({ taskId });
  const [adding, setAdding] = useState(false);
  if (!notes.data?.length && !adding)
    return (
      <button className="linkish muted small add-audio" onClick={() => setAdding(true)}>🎙 Attach a voice note</button>
    );
  return (
    <section className="task-section">
      <h3>Voice notes</h3>
      <VoiceNotes where={{ taskId }} empty={null} />
      <AddVoiceNote target={{ task_id: taskId }} compact />
    </section>
  );
}
