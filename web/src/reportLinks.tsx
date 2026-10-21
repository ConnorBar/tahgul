import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, del, errorMessage, post, type Client, type Project } from "./api";
import { useWorkspace } from "./workspace";

type Link = {
  id: string;
  label: string;
  project_id: string | null;
  project_name: string | null;
  show_amounts: boolean;
  show_tasks: boolean;
  show_notes: boolean;
  expires_at: string | null;
  active: boolean;
  last_viewed_at: string | null;
  view_count: number;
  path: string;
  public_url: string | null;
  created_at: string;
};

const fmt = (iso: string) =>
  new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(new Date(iso));

/** Read-only progress report links for one client. */
export function ReportLinks({ client }: { client: Client }) {
  const { current: ws } = useWorkspace();
  const qc = useQueryClient();
  const key = ["report-links", client.id];
  const links = useQuery({ queryKey: key, queryFn: () => api<Link[]>(`/clients/${client.id}/report-links`) });
  const projects = useQuery({
    queryKey: ["projects", ws.id, ""],
    queryFn: () => api<Project[]>(`/projects?workspace_id=${ws.id}`),
  });
  const own = projects.data?.filter((p) => p.client_id === client.id && p.status !== "idea") ?? [];
  const [f, setF] = useState({ project_id: "", show_amounts: false, show_tasks: true, show_notes: false, expires: "90" });
  const [copied, setCopied] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () =>
      post<Link>(`/clients/${client.id}/report-links`, {
        project_id: f.project_id || null,
        show_amounts: f.show_amounts,
        show_tasks: f.show_tasks,
        show_notes: f.show_notes,
        expires_in_days: f.expires === "never" ? null : Number(f.expires),
        label: f.project_id ? `${own.find((p) => p.id === f.project_id)?.name} report` : "Progress report",
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: key }),
  });
  const revoke = useMutation({
    mutationFn: (id: string) => del(`/report-links/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: key }),
  });
  const url = (l: Link) => l.public_url ?? `${location.origin}${l.path}`;
  const copy = async (l: Link) => {
    await navigator.clipboard?.writeText(url(l));
    setCopied(l.id);
  };
  const check = (k: "show_amounts" | "show_tasks" | "show_notes") => ({
    checked: f[k],
    onChange: (e: { target: { checked: boolean } }) => setF((c) => ({ ...c, [k]: e.target.checked })),
  });

  return (
    <div className="report-links">
      <p className="muted small">
        A read-only page for {client.name}: hours by week, budget used per agreed scope, and
        tasks finished and coming up. Rates, other clients and internal notes are never on it.
        {!links.data?.some((l) => l.public_url) && " Links open on your tailnet; to send one to a client, export the PDF from the page or set up public report links (docs/client-reports.md)."}
      </p>
      <form className="link-form" onSubmit={(e: FormEvent) => { e.preventDefault(); create.mutate(); }}>
        <label>
          Covers
          <select value={f.project_id} onChange={(e) => setF((c) => ({ ...c, project_id: e.target.value }))}>
            <option value="">All of {client.name}'s projects</option>
            {own.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <label className="check"><input type="checkbox" {...check("show_tasks")} /><span>Tasks finished and coming up</span></label>
        <label className="check"><input type="checkbox" {...check("show_amounts")} /><span>Money: billable amounts and invoices</span></label>
        <label className="check"><input type="checkbox" {...check("show_notes")} /><span>Work log (time-entry notes)</span></label>
        <label>
          Expires
          <select value={f.expires} onChange={(e) => setF((c) => ({ ...c, expires: e.target.value }))}>
            <option value="30">In 30 days</option>
            <option value="90">In 90 days</option>
            <option value="365">In a year</option>
            <option value="never">Never</option>
          </select>
        </label>
        <button disabled={create.isPending}>Create link</button>
      </form>
      {(create.error || revoke.error) && <p className="error">{errorMessage(create.error ?? revoke.error)}</p>}
      {links.data && links.data.length > 0 && (
        <ul className="link-list">
          {links.data.map((l) => (
            <li key={l.id} className={l.active ? undefined : "inactive"}>
              <div>
                <strong>{l.project_name ?? "All projects"}</strong>
                <span className="muted small">
                  {" "}· {[l.show_tasks && "tasks", l.show_amounts && "money", l.show_notes && "work log"].filter(Boolean).join(", ") || "hours only"}
                  {" "}· {l.active ? (l.expires_at ? `until ${fmt(l.expires_at)}` : "no expiry") : "off"}
                  {" "}· {l.view_count ? `viewed ${l.view_count}×, last ${fmt(l.last_viewed_at!)}` : "not opened yet"}
                </span>
              </div>
              {l.active && (
                <div className="row">
                  <a className="button ghost" href={l.path} target="_blank" rel="noreferrer">Open</a>
                  <a className="button ghost" href={`${l.path}/pdf`} target="_blank" rel="noreferrer">PDF</a>
                  <button className="ghost" onClick={() => copy(l)}>{copied === l.id ? "Copied" : "Copy link"}</button>
                  <button className="ghost danger-text" onClick={() => confirm("Turn this link off? It stops working right away.") && revoke.mutate(l.id)}>Turn off</button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
