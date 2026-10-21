import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type Client, type Project, type Me } from "../api";
import { money } from "../ui";
import { useWorkspace } from "../workspace";
import { PendingLog } from "./CalendarPage";

export default function Today({ me }: { me: Me }) {
  const { current: ws } = useWorkspace();
  const projects = useQuery({
    queryKey: ["projects", ws.id, ""],
    queryFn: () => api<Project[]>(`/projects?workspace_id=${ws.id}`),
  });
  const clients = useQuery({
    queryKey: ["clients", ws.id],
    queryFn: () => api<Client[]>(`/clients?workspace_id=${ws.id}`),
  });
  const active = projects.data?.filter((p) => p.status === "active") ?? [];
  const ideas = projects.data?.filter((p) => p.status === "idea") ?? [];

  return (
    <section className="page">
      <h1>Today</h1>
      <p className="muted">Signed in as {me.email}.</p>

      {!me.totp_enabled && (
        <div className="notice">
          Two-factor sign-in is off. <Link to="/settings/security">Turn it on</Link> before
          putting real client data in.
        </div>
      )}

      <PendingLog />

      {!ws.default_rate && (
        <div className="notice">
          No default hourly rate yet. <Link to="/settings/workspace">Set one</Link> so every
          project has a rate to fall back on.
        </div>
      )}

      <div className="stats">
        <Link to="/projects" className="stat">
          <span className="stat-n">{projects.data ? active.length : "…"}</span>
          <span className="muted">active projects</span>
        </Link>
        <Link to="/clients" className="stat">
          <span className="stat-n">{clients.data?.length ?? "…"}</span>
          <span className="muted">clients</span>
        </Link>
        <Link to="/projects" className="stat">
          <span className="stat-n">{projects.data ? ideas.length : "…"}</span>
          <span className="muted">ideas</span>
        </Link>
        <Link to="/settings/workspace" className="stat">
          <span className="stat-n">{money(ws.default_rate, ws.currency)}</span>
          <span className="muted">default rate</span>
        </Link>
      </div>

      <div className="card">
        <h2>Coming next</h2>
        <p className="muted">
          Scopes and invoicing arrive next, then voice notes. Siri, agent and iPhone calendar
          setup are in the repo's docs; the full API is at <a href="/api/docs">/api/docs</a>.
        </p>
      </div>
    </section>
  );
}
