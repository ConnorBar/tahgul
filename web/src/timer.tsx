import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, del, errorMessage, patch, post, type Project, type Timer } from "./api";
import { hm, hms } from "./ui";
import { useWorkspace } from "./workspace";
import { Select } from "./Select";

/** Projects you can log time to, grouped by client. */
export function useProjects() {
  const { current: ws } = useWorkspace();
  return useQuery({
    queryKey: ["projects", ws.id, ""],
    queryFn: () => api<Project[]>(`/projects?workspace_id=${ws.id}`),
  });
}

export function ProjectSelect({
  value,
  onChange,
  label = "Project",
  includeDone = false,
  className,
}: {
  value: string;
  onChange: (id: string) => void;
  label?: string;
  includeDone?: boolean;
  className?: string;
}) {
  const projects = useProjects();
  const usable = (projects.data ?? []).filter(
    (p) => includeDone || p.status !== "done" || p.id === value,
  );
  const groups = new Map<string, Project[]>();
  for (const p of usable) {
    const k = p.client_name ?? (p.status === "idea" ? "Ideas" : "No client");
    groups.set(k, [...(groups.get(k) ?? []), p]);
  }
  return (
    <Select aria-label={label} className={className} value={value}
      onChange={(e) => onChange(e.target.value)}>
      <option value="">No project</option>
      {[...groups.entries()].map(([group, list]) => (
        <optgroup key={group} label={group}>
          {list.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </optgroup>
      ))}
    </Select>
  );
}

/** Invalidate everything that a change to time can affect. */
export function useTimeInvalidation() {
  const qc = useQueryClient();
  return () =>
    Promise.all(
      ["timer", "timesheet", "entries", "summary", "projects"].map((k) =>
        qc.invalidateQueries({ queryKey: [k] }),
      ),
    );
}

function useTicker(active: boolean) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [active]);
}

/** The always-visible timer in the top bar (SPEC: one-click running timer). */
export function TimerBar() {
  const { current: ws } = useWorkspace();
  const invalidate = useTimeInvalidation();
  const timer = useQuery({
    queryKey: ["timer"],
    queryFn: () => api<Timer>("/timer"),
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
  const running = timer.data?.running ?? null;
  useTicker(!!running);

  const [project, setProject] = useState("");
  const [notes, setNotes] = useState("");

  const start = useMutation({
    mutationFn: () =>
      post<Timer>("/timer/start", {
        workspace_id: ws.id,
        project_id: project || null,
        notes: notes.trim() || null,
      }),
    onSuccess: () => {
      setNotes("");
      invalidate();
    },
  });
  const stop = useMutation({
    mutationFn: (end_at?: string) => post("/timer/stop", end_at ? { end_at } : {}),
    onSuccess: invalidate,
  });
  const change = useMutation({
    mutationFn: (body: Record<string, unknown>) => patch("/timer", body),
    onSuccess: invalidate,
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    start.mutate();
  };

  const err = start.error ?? stop.error ?? change.error;

  if (!running) {
    return (
      <form className="timer" onSubmit={submit}>
        <input className="timer-notes" value={notes} onChange={(e) => setNotes(e.target.value)}
          placeholder="What are you working on?" aria-label="Timer notes" maxLength={500} />
        <ProjectSelect className="timer-project" value={project} onChange={setProject}
          label="Timer project" />
        <button type="submit" className="timer-btn start" disabled={start.isPending}
          aria-label="Start timer" title="Start timer">
          ▶
        </button>
        {err && <span className="timer-err">{errorMessage(err)}</span>}
      </form>
    );
  }

  const elapsed = (Date.now() - new Date(running.start_at).getTime()) / 1000;
  return (
    <div className="timer on">
      <span className="dot pulse" style={{ background: "var(--accent)" }} aria-hidden="true" />
      <input className="timer-notes" defaultValue={running.notes ?? ""} key={running.id}
        placeholder="Add notes" aria-label="Timer notes" maxLength={500}
        onBlur={(e) => {
          const v = e.target.value.trim();
          if (v !== (running.notes ?? "")) change.mutate({ notes: v || null });
        }} />
      <ProjectSelect className="timer-project" value={running.project_id ?? ""}
        onChange={(id) => change.mutate({ project_id: id || null })} label="Timer project" />
      <span className="timer-clock" aria-live="off">{hms(elapsed)}</span>
      <button className="timer-btn stop" onClick={() => stop.mutate(undefined)}
        disabled={stop.isPending} aria-label="Stop timer" title="Stop timer">
        ■
      </button>
      {err && <span className="timer-err">{errorMessage(err)}</span>}
    </div>
  );
}

/** "Forgot to stop?" prompt when the timer runs past the workspace limit. */
export function LongTimerPrompt() {
  const invalidate = useTimeInvalidation();
  const timer = useQuery({ queryKey: ["timer"], queryFn: () => api<Timer>("/timer") });
  const [dismissed, setDismissed] = useState<string | null>(null);
  const stop = useMutation({
    mutationFn: (end_at: string) => post("/timer/stop", { end_at }),
    onSuccess: invalidate,
  });
  const discard = useMutation({ mutationFn: () => del("/timer"), onSuccess: invalidate });
  const t = timer.data;
  if (!t?.running || !t.long_running || dismissed === t.running.id) return null;

  const limit = (t.warning_after_minutes ?? 240) * 60;
  const trimmedEnd = new Date(new Date(t.running.start_at).getTime() + limit * 1000);
  const at = trimmedEnd.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return (
    <div className="banner" role="status">
      <span>
        The timer has been running for <strong>{hm(t.elapsed_seconds)}</strong>. Forgot to stop
        it?
      </span>
      <div className="row">
        <button onClick={() => stop.mutate(trimmedEnd.toISOString())} disabled={stop.isPending}>
          Stop at {at} ({hm(limit)})
        </button>
        <button className="ghost" onClick={() => setDismissed(t.running!.id)}>Keep running</button>
        <button className="danger" disabled={discard.isPending}
          onClick={() => confirm("Throw this timer away without logging it?") && discard.mutate()}>
          Discard
        </button>
      </div>
    </div>
  );
}
