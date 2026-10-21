import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, type Project, type Task } from "../api";
import { Board } from "../tasks/Board";
import { TaskDrawer } from "../tasks/TaskDrawer";
import { QuickAdd, TaskCalendar, TaskTable } from "../tasks/views";
import { ScopesPanel } from "../scopes";
import { AddVoiceNote, VoiceNotes } from "../voice";
import { Dialog, money } from "../ui";
import { useWorkspace } from "../workspace";
import { ProjectForm } from "./Projects";

type View = "board" | "list" | "calendar" | "scopes" | "notes";

export default function ProjectPage() {
  const { id = "" } = useParams();
  const { current: ws } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const view = (params.get("view") as View) ?? "board";
  const openTask = params.get("task");
  const setParam = (k: string, v: string | null) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    setParams(next, { replace: k === "task" });
  };
  const project = useQuery({ queryKey: ["projects", "one", id], queryFn: () => api<Project>(`/projects/${id}`) });
  const tasks = useQuery({
    queryKey: ["tasks", "project", id],
    queryFn: () => api<Task[]>(`/tasks?project=${id}&sort=board`),
    enabled: view === "list" || view === "calendar",
  });
  const [editing, setEditing] = useState(false);
  const p = project.data;
  if (project.isError) return <section className="page"><p>Project not found. <Link to="/projects">All projects</Link></p></section>;
  if (!p) return <section className="page"><p className="muted">Loading…</p></section>;

  return (
    <section className="page full">
      <div className="page-head">
        <div>
          <p className="muted small crumbs"><Link to="/projects">Projects</Link>{p.client_name && ` · ${p.client_name}`}</p>
          <h1><span className="dot inline-dot big" style={{ background: p.colour }} />{p.name}</h1>
        </div>
        <div className="row">
          <span className="muted small">{money(p.effective_rate, ws.currency, true)} · {p.status}</span>
          <button className="ghost" onClick={() => setEditing(true)}>Edit project</button>
        </div>
      </div>
      <div className="row view-bar">
        <nav className="tabs compact" aria-label="Views">
          {(["board", "list", "calendar", "scopes", "notes"] as View[]).map((v) => (
            <a key={v} href={`?view=${v}`} className={view === v ? "active" : undefined}
              onClick={(e) => { e.preventDefault(); setParam("view", v === "board" ? null : v); }}>
              {v[0].toUpperCase() + v.slice(1)}
            </a>
          ))}
        </nav>
        {(view === "board" || view === "list" || view === "calendar") && <QuickAdd projectId={id} />}
      </div>

      {view === "board" && <Board projectId={id} onOpen={(t) => setParam("task", t)} />}
      {view === "list" && tasks.data && (
        <TaskTable tasks={tasks.data} onOpen={(t) => setParam("task", t)} showProject={false} />
      )}
      {view === "scopes" && <ScopesPanel projectId={id} />}
      {view === "notes" && (
        <div className="notes-panel">
          <AddVoiceNote target={{ project_id: id }} />
          <VoiceNotes where={{ projectId: id }} empty="No voice notes on this project yet." />
        </div>
      )}
      {view === "calendar" && tasks.data && <TaskCalendar tasks={tasks.data} onOpen={(t) => setParam("task", t)} />}

      <TaskDrawer taskId={openTask} onClose={() => setParam("task", null)} onOpen={(t) => setParam("task", t)} />
      <Dialog open={editing} title="Edit project" onClose={() => setEditing(false)}>
        {editing && <ProjectForm project={p} onDone={() => setEditing(false)} />}
      </Dialog>
    </section>
  );
}
