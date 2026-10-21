import { useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api, errorMessage, post, type Client, type Idea, type Project } from "../api";
import { Dialog, Empty } from "../ui";
import { AddVoiceNote, VoiceNotes, useVoiceInvalidation, useVoiceNotes } from "../voice";
import { useWorkspace } from "../workspace";

/** The ideas inbox: loose ideas (projects in status "idea") and their voice notes. */
export default function Ideas() {
  const { current: ws } = useWorkspace();
  const ideas = useQuery({
    queryKey: ["ideas", ws.id],
    queryFn: () => api<Idea[]>(`/ideas?workspace_id=${ws.id}`),
  });
  const unfiled = useVoiceNotes({ unfiled: true });
  const [open, setOpen] = useState<string | null>(null);
  const [promoting, setPromoting] = useState<Idea | null>(null);

  return (
    <section className="page wide">
      <div className="page-head">
        <h1>Ideas</h1>
      </div>
      <Capture onCreated={(id) => setOpen(id)} />

      {ideas.isPending && <p className="muted">Loading…</p>}
      {ideas.data?.length === 0 && (
        <Empty>No ideas yet. Type one above, record a note, or send one from Yog or Siri.</Empty>
      )}
      <div className="ideas">
        {ideas.data?.map((i) => (
          <article key={i.id} className={`card idea${open === i.id ? " open" : ""}`}>
            <button className="idea-head linkish" aria-expanded={open === i.id}
              onClick={() => setOpen(open === i.id ? null : i.id)}>
              <span>
                <strong>{i.name}</strong>
                {i.description && <span className="muted small idea-desc">{i.description}</span>}
              </span>
              <span className="muted small idea-counts">
                {i.voice_note_count > 0 && <span>🎙 {i.voice_note_count}</span>}
                {i.open_task_count > 0 && <span>{i.open_task_count} to-do{i.open_task_count === 1 ? "" : "s"}</span>}
              </span>
            </button>
            {open === i.id && (
              <div className="idea-body">
                <VoiceNotes where={{ projectId: i.id }} empty={null} />
                <AddVoiceNote target={{ project_id: i.id }} />
                <div className="row idea-actions">
                  <Link to={`/projects/${i.id}`}>Open board</Link>
                  <span className="spacer" />
                  <button onClick={() => setPromoting(i)}>Make it a project</button>
                </div>
              </div>
            )}
          </article>
        ))}
      </div>

      {unfiled.data && unfiled.data.length > 0 && (
        <div className="group">
          <h2 className="group-title">Unfiled notes</h2>
          <p className="muted small">Their project or task was deleted. The audio is kept until you delete it.</p>
          <VoiceNotes where={{ unfiled: true }} />
        </div>
      )}

      <Dialog open={promoting !== null} title="Make it a project" onClose={() => setPromoting(null)}>
        {promoting && <Promote idea={promoting} onDone={() => setPromoting(null)} />}
      </Dialog>
    </section>
  );
}

function Capture({ onCreated }: { onCreated: (id: string) => void }) {
  const { current: ws } = useWorkspace();
  const invalidate = useVoiceInvalidation();
  const [title, setTitle] = useState("");
  const create = useMutation({
    mutationFn: () => post<Project>("/ideas", { workspace_id: ws.id, title: title.trim() }),
    onSuccess: async (p) => {
      setTitle("");
      await invalidate();
      onCreated(p.id);
    },
  });
  return (
    <div className="card capture">
      <form className="row" onSubmit={(e: FormEvent) => { e.preventDefault(); if (title.trim()) create.mutate(); }}>
        <input value={title} onChange={(e) => setTitle(e.target.value)} aria-label="New idea"
          placeholder="An idea… (drone surveys for grain farms)" />
        <button disabled={!title.trim() || create.isPending}>Add idea</button>
      </form>
      <div className="row capture-voice">
        <span className="muted small">or talk it through:</span>
        <AddVoiceNote target={{}} compact onDone={(n) => n.project_id && onCreated(n.project_id)} />
      </div>
      {create.isError && <p className="error small">{errorMessage(create.error)}</p>}
    </div>
  );
}

function Promote({ idea, onDone }: { idea: Idea; onDone: () => void }) {
  const { current: ws } = useWorkspace();
  const navigate = useNavigate();
  const invalidate = useVoiceInvalidation();
  const clients = useQuery({
    queryKey: ["clients", ws.id],
    queryFn: () => api<Client[]>(`/clients?workspace_id=${ws.id}`),
  });
  const [clientId, setClientId] = useState("");
  const promote = useMutation({
    mutationFn: () => post<Project>(`/ideas/${idea.id}/promote`, { client_id: clientId || null }),
    onSuccess: async (p) => {
      await invalidate();
      onDone();
      navigate(`/projects/${p.id}`);
    },
  });
  return (
    <form onSubmit={(e) => { e.preventDefault(); promote.mutate(); }}>
      <p>
        <strong>{idea.name}</strong> becomes an active project. Its voice notes and tasks come
        with it.
      </p>
      <label>
        Client
        <select value={clientId} onChange={(e) => setClientId(e.target.value)}>
          <option value="">No client (internal)</option>
          {clients.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </label>
      {promote.isError && <p className="error">{errorMessage(promote.error)}</p>}
      <div className="dialog-actions">
        <span className="spacer" />
        <button type="button" className="ghost" onClick={onDone}>Cancel</button>
        <button disabled={promote.isPending}>Make it a project</button>
      </div>
    </form>
  );
}
