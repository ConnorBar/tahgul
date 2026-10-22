import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  PROJECT_STATUSES,
  api,
  del,
  errorMessage,
  patch,
  post,
  type Activity,
  type Client,
  type Project,
  type ProjectStatus,
  type RateInfo,
} from "../api";
import { Dialog, Empty, money, orNull, str } from "../ui";
import { useWorkspace } from "../workspace";
import { Select } from "../Select";

const STATUS_LABEL: Record<ProjectStatus, string> = {
  active: "Active",
  paused: "Paused",
  idea: "Ideas",
  done: "Done",
};

const SOURCE_LABEL: Record<string, string> = {
  activity: "activity rate",
  project: "project rate",
  client: "client rate",
  workspace: "workspace default",
};

const COLOURS = ["#2f5d50", "#3b6ea5", "#8a5a9e", "#b5523b", "#c28a1e", "#4d7c2f", "#6b6f76"];

export default function Projects() {
  const { current: ws } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const clientFilter = params.get("client") ?? "";
  const projects = useQuery({
    queryKey: ["projects", ws.id, clientFilter],
    queryFn: () =>
      api<Project[]>(
        `/projects?workspace_id=${ws.id}${clientFilter ? `&client_id=${clientFilter}` : ""}`,
      ),
  });
  const clients = useQuery({
    queryKey: ["clients", ws.id],
    queryFn: () => api<Client[]>(`/clients?workspace_id=${ws.id}`),
  });
  const [editing, setEditing] = useState<Project | "new" | null>(null);
  const navigate = useNavigate();

  const byStatus = (s: ProjectStatus) => projects.data?.filter((p) => p.status === s) ?? [];

  return (
    <section className="page wide">
      <div className="page-head">
        <h1>Projects</h1>
        <div className="row">
          <Select aria-label="Filter by client" value={clientFilter}
            onChange={(e) => setParams(e.target.value ? { client: e.target.value } : {})}>
            <option value="">All clients</option>
            {clients.data?.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </Select>
          <button onClick={() => setEditing("new")}>New project</button>
        </div>
      </div>

      {projects.isPending && <p className="muted">Loading…</p>}
      {projects.data?.length === 0 && (
        <Empty>
          {clientFilter ? "No projects for this client yet." : "No projects yet."} Ideas are
          projects too: set the status to Idea and leave the client blank.
        </Empty>
      )}

      {PROJECT_STATUSES.map((s) => {
        const list = byStatus(s);
        if (!list.length) return null;
        return (
          <div key={s} className="group">
            <h2 className="group-title">
              {STATUS_LABEL[s]} <span className="muted">{list.length}</span>
            </h2>
            <div className="cards">
              {list.map((p) => (
                <button key={p.id} className="project-card" onClick={() => navigate(`/projects/${p.id}`)}>
                  <span className="dot" style={{ background: p.colour }} aria-hidden="true" />
                  <span className="pc-main">
                    <strong>{p.name}</strong>
                    <span className="muted">{p.client_name ?? "No client"}</span>
                  </span>
                  <span className="pc-rate">
                    {money(p.effective_rate, ws.currency, true)}
                    {p.rate_source && (
                      <span className="muted small">{SOURCE_LABEL[p.rate_source]}</span>
                    )}
                  </span>
                  {(p.budget_hours || p.budget_amount) && (
                    <span className="pc-budget muted small">
                      Budget{" "}
                      {[p.budget_hours && `${Number(p.budget_hours)} h`,
                        p.budget_amount && money(p.budget_amount, ws.currency)]
                        .filter(Boolean).join(" · ")}
                    </span>
                  )}
                </button>
              ))}
            </div>
          </div>
        );
      })}

      <Dialog
        open={editing !== null}
        title={editing === "new" ? "New project" : "Edit project"}
        onClose={() => setEditing(null)}
      >
        {editing !== null && (
          <ProjectForm
            project={editing === "new" ? null : editing}
            defaultClient={clientFilter}
            onDone={(created) => {
              setEditing(null);
              if (created) navigate(`/projects/${created.id}`);
            }}
          />
        )}
      </Dialog>
    </section>
  );
}

export function ProjectForm({
  project,
  defaultClient = "",
  onDone,
}: {
  project: Project | null;
  defaultClient?: string;
  onDone: (created?: Project) => void;
}) {
  const qc = useQueryClient();
  const { current: ws } = useWorkspace();
  const clientsQ = useQuery({
    queryKey: ["clients", ws.id],
    queryFn: () => api<Client[]>(`/clients?workspace_id=${ws.id}`),
  });
  const clients = clientsQ.data ?? [];
  const [f, setF] = useState({
    name: project?.name ?? "",
    client_id: project ? str(project.client_id) : defaultClient,
    status: project?.status ?? ("active" as ProjectStatus),
    rate: str(project?.rate),
    budget_hours: str(project?.budget_hours),
    budget_amount: str(project?.budget_amount),
    colour: project?.colour ?? COLOURS[0],
    description: str(project?.description),
  });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) =>
    setF((cur) => ({ ...cur, [k]: e.target.value }));

  const body = () => ({
    name: f.name,
    client_id: orNull(f.client_id),
    status: f.status,
    rate: orNull(f.rate),
    budget_hours: orNull(f.budget_hours),
    budget_amount: orNull(f.budget_amount),
    colour: f.colour,
    description: orNull(f.description),
  });

  const done = (created?: Project) => {
    qc.invalidateQueries({ queryKey: ["projects"] });
    qc.invalidateQueries({ queryKey: ["clients"] });
    qc.invalidateQueries({ queryKey: ["rate"] });
    onDone(created);
  };
  const save = useMutation({
    mutationFn: () =>
      project
        ? patch<Project>(`/projects/${project.id}`, body())
        : post<Project>("/projects", { ...body(), workspace_id: ws.id }),
    onSuccess: (saved) => done(project ? undefined : saved),
  });
  const remove = useMutation({
    mutationFn: () => del(`/projects/${project!.id}`),
    onSuccess: () => {
      done();
      window.location.assign("/projects");
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };

  return (
    <form onSubmit={submit}>
      <div className="grid-2">
        <label className="span-2">
          Name
          <input value={f.name} onChange={set("name")} required maxLength={200} autoFocus />
        </label>
        <label>
          Client
          <Select aria-label="Client" value={f.client_id} onChange={set("client_id")}>
            <option value="">No client</option>
            {clients.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </Select>
        </label>
        <label>
          Status
          <Select aria-label="Status" value={f.status} onChange={set("status")}>
            {PROJECT_STATUSES.map((s) => (
              <option key={s} value={s}>{s === "idea" ? "Idea" : STATUS_LABEL[s]}</option>
            ))}
          </Select>
        </label>
        <label>
          Rate override ({ws.currency}/h)
          <input inputMode="decimal" value={f.rate} onChange={set("rate")}
            placeholder="Use client or default" />
        </label>
        <fieldset className="colours">
          <legend>Colour</legend>
          {COLOURS.map((c) => (
            <label key={c} className="swatch" style={{ background: c }}>
              <input type="radio" name="colour" value={c} checked={f.colour === c}
                onChange={set("colour")} aria-label={c} />
            </label>
          ))}
        </fieldset>
        <label>
          Budget (hours)
          <input inputMode="decimal" value={f.budget_hours} onChange={set("budget_hours")} />
        </label>
        <label>
          Budget ({ws.currency})
          <input inputMode="decimal" value={f.budget_amount} onChange={set("budget_amount")} />
        </label>
        <label className="span-2">
          Description
          <textarea rows={3} value={f.description} onChange={set("description")} />
        </label>
      </div>

      {project && <RateBreakdown project={project} />}

      {(save.isError || remove.isError) && (
        <p className="error">{errorMessage(save.error ?? remove.error)}</p>
      )}
      <div className="dialog-actions">
        {project && (
          <button type="button" className="danger" disabled={remove.isPending}
            onClick={() => confirm(`Delete ${project.name}?`) && remove.mutate()}>
            Delete
          </button>
        )}
        <span className="spacer" />
        <button type="button" className="ghost" onClick={() => onDone()}>Cancel</button>
        <button type="submit" disabled={save.isPending}>{project ? "Save" : "Add project"}</button>
      </div>
    </form>
  );
}

/** Shows which level the rate comes from, optionally for one activity. */
function RateBreakdown({ project }: { project: Project }) {
  const { current: ws } = useWorkspace();
  const [activity, setActivity] = useState("");
  const activities = useQuery({
    queryKey: ["activities", ws.id],
    queryFn: () => api<Activity[]>(`/activities?workspace_id=${ws.id}`),
  });
  const info = useQuery({
    queryKey: ["rate", project.id, activity],
    queryFn: () =>
      api<RateInfo>(`/projects/${project.id}/rate${activity ? `?activity_id=${activity}` : ""}`),
  });

  return (
    <div className="rate-box">
      <div className="rate-head">
        <span>
          Effective rate{" "}
          <strong>{info.data ? money(info.data.rate, info.data.currency, true) : "…"}</strong>
        </span>
        {activities.data && activities.data.length > 0 && (
          <Select aria-label="For activity" value={activity}
            onChange={(e) => setActivity(e.target.value)}>
            <option value="">Any activity</option>
            {activities.data.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </Select>
        )}
      </div>
      {info.data && (
        <ol className="levels">
          {info.data.levels
            .filter((l) => l.level !== "activity" || activity)
            .map((l) => (
              <li key={l.level} className={l.level === info.data.source ? "winner" : undefined}>
                <span className="lvl">{l.level}</span>
                <span className="muted">{l.name ?? "none"}</span>
                <span className="num">{l.rate ? money(l.rate, info.data.currency, true) : "—"}</span>
              </li>
            ))}
        </ol>
      )}
      <p className="muted small">The most specific rate wins. Saved changes update this.</p>
    </div>
  );
}
