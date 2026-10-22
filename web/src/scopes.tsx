import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  SCOPE_STATUSES,
  SCOPE_TYPES,
  api,
  del,
  errorMessage,
  patch,
  post,
  type Scope,
  type ScopeReport,
} from "./api";
import { Dialog, Empty, hoursLabel, money, orNull, str } from "./ui";
import { useWorkspace } from "./workspace";
import { Select } from "./Select";

export function useScopes(projectId: string | null | undefined) {
  return useQuery({
    queryKey: ["scopes", projectId],
    queryFn: () => api<ScopeReport>(`/projects/${projectId}/scopes`),
    enabled: !!projectId,
  });
}

/** Pick one of a project's scopes; renders nothing when the project has none. */
export function ScopeSelect({
  projectId,
  value,
  onChange,
  withLabel = true,
}: {
  projectId: string | null | undefined;
  value: string;
  onChange: (id: string) => void;
  withLabel?: boolean;
}) {
  const scopes = useScopes(projectId);
  const list = (scopes.data?.scopes ?? []).filter((s) => s.status !== "done" || s.id === value);
  if (!projectId || !list.length) return null;
  const select = (
    <Select aria-label="Scope" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Outside any scope</option>
      {list.map((s) => (
        <option key={s.id} value={s.id}>{s.title}{s.status === "draft" ? " (draft)" : ""}</option>
      ))}
    </Select>
  );
  return withLabel ? <label>Scope{select}</label> : select;
}

const TYPE_LABEL: Record<Scope["type"], string> = {
  hourly: "Hourly",
  fixed: "Fixed fee",
  retainer: "Monthly retainer",
};

export function BurnBars({ s, currency }: { s: Scope; currency: string }) {
  const b = s.burn;
  if (!b) return null;
  const parts: { pct: number; label: string }[] = [];
  if (s.budget_hours)
    parts.push({ pct: Number(b.hours_pct), label: `${hoursLabel(b.hours) || 0} of ${hoursLabel(s.budget_hours)} h` });
  if (s.budget_amount)
    parts.push({ pct: Number(b.amount_pct), label: `${money(b.amount, currency)} of ${money(s.budget_amount, currency)}` });
  if (!parts.length) return <span className="small muted">{hoursLabel(b.hours) || 0} h logged · no budget</span>;
  return (
    <span className="budgets">
      {parts.map((p) => {
        const level = p.pct >= 100 ? "over" : p.pct >= 80 ? "warn" : "ok";
        return (
          <span key={p.label} className="budget">
            <span className={`bar ${level}`} role="meter" aria-valuenow={Math.round(p.pct)}
              aria-valuemin={0} aria-valuemax={100} aria-label={p.label}>
              <span style={{ width: `${Math.min(p.pct, 100)}%` }} />
            </span>
            <span className="small">{p.label}{p.pct >= 100 ? " · over budget" : p.pct >= 80 ? " · nearly used" : ""}</span>
          </span>
        );
      })}
    </span>
  );
}

export function ScopesPanel({ projectId }: { projectId: string }) {
  const { current: ws } = useWorkspace();
  const report = useScopes(projectId);
  const [editing, setEditing] = useState<Scope | "new" | null>(null);
  const r = report.data;
  return (
    <div className="scopes">
      <div className="row">
        <p className="muted small">
          Scopes are the agreed pieces of the project: a statement of work, a phase or a retainer.
          Tag time and tasks with them to track burn against the budget.
        </p>
        <span className="spacer" />
        <button onClick={() => setEditing("new")}>New scope</button>
      </div>
      {r?.creep && (
        <p className="notice">
          {hoursLabel(r.outside_scope_hours)} h ({money(r.outside_scope_amount, ws.currency)}) logged
          outside every scope. Is that scope creep worth a conversation, or a new scope?
        </p>
      )}
      {r && !r.scopes.length && <Empty>No scopes yet.</Empty>}
      {r && r.scopes.length > 0 && (
        <div className="card flush table-wrap">
          <table className="clickable">
            <thead>
              <tr><th>Scope</th><th>Type</th><th>Price</th><th>Burn</th><th>Status</th></tr>
            </thead>
            <tbody>
              {r.scopes.map((s) => (
                <tr key={s.id} onClick={() => setEditing(s)} tabIndex={0}
                  onKeyDown={(e) => e.key === "Enter" && setEditing(s)}>
                  <td>
                    <strong>{s.title}</strong>
                    {(s.start_date || s.end_date) && (
                      <div className="small muted">{s.start_date ?? "…"} → {s.end_date ?? "…"}</div>
                    )}
                  </td>
                  <td>{TYPE_LABEL[s.type]}</td>
                  <td className="small">
                    {s.type === "fixed" && money(s.fee, ws.currency)}
                    {s.type === "retainer" && <>{money(s.fee, ws.currency)}/mo{s.included_hours && <> · {hoursLabel(s.included_hours)} h incl.</>}</>}
                    {s.rate && <div className="muted">{money(s.rate, ws.currency, true)}</div>}
                    {s.type === "hourly" && !s.rate && <span className="muted">project rate</span>}
                  </td>
                  <td><BurnBars s={s} currency={ws.currency} /></td>
                  <td>
                    {s.status}
                    {s.invoiced_at && <span className="tag">INVOICED</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Dialog open={editing !== null} title={editing === "new" ? "New scope" : "Edit scope"}
        onClose={() => setEditing(null)}>
        {editing && (
          <ScopeForm projectId={projectId} scope={editing === "new" ? null : editing}
            onDone={() => setEditing(null)} />
        )}
      </Dialog>
    </div>
  );
}

function ScopeForm({ projectId, scope, onDone }: { projectId: string; scope: Scope | null; onDone: () => void }) {
  const qc = useQueryClient();
  const { current: ws } = useWorkspace();
  const [f, setF] = useState({
    title: scope?.title ?? "",
    description: scope?.description ?? "",
    type: scope?.type ?? ("hourly" as Scope["type"]),
    status: scope?.status ?? ("agreed" as Scope["status"]),
    rate: str(scope?.rate),
    fee: str(scope?.fee),
    budget_hours: str(scope?.budget_hours),
    budget_amount: str(scope?.budget_amount),
    included_hours: str(scope?.included_hours),
    start_date: scope?.start_date ?? "",
    end_date: scope?.end_date ?? "",
  });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) =>
    setF((c) => ({ ...c, [k]: e.target.value }));
  const done = async () => {
    await qc.invalidateQueries({ queryKey: ["scopes", projectId] });
    await qc.invalidateQueries({ queryKey: ["time"] });
    onDone();
  };
  const body = () => ({
    title: f.title.trim(),
    description: orNull(f.description),
    type: f.type,
    status: f.status,
    rate: orNull(f.rate),
    fee: f.type === "hourly" ? null : orNull(f.fee),
    budget_hours: orNull(f.budget_hours),
    budget_amount: orNull(f.budget_amount),
    included_hours: f.type === "retainer" ? orNull(f.included_hours) : null,
    start_date: f.start_date || null,
    end_date: f.end_date || null,
  });
  const save = useMutation({
    mutationFn: () => (scope ? patch(`/scopes/${scope.id}`, body()) : post(`/projects/${projectId}/scopes`, body())),
    onSuccess: done,
  });
  const remove = useMutation({ mutationFn: () => del(`/scopes/${scope!.id}`), onSuccess: done });
  const err = save.error ?? remove.error;

  return (
    <form onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
      <label>
        Title
        <input value={f.title} onChange={set("title")} required maxLength={200} placeholder="Phase 1: network audit" />
      </label>
      <div className="grid-2">
        <label>
          Type
          <Select value={f.type} onChange={set("type")} disabled={!!scope?.invoiced_at}>
            {SCOPE_TYPES.map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}
          </Select>
        </label>
        <label>
          Status
          <Select value={f.status} onChange={set("status")}>
            {SCOPE_STATUSES.map((t) => <option key={t} value={t}>{t[0].toUpperCase() + t.slice(1)}</option>)}
          </Select>
        </label>
      </div>
      {f.type === "fixed" && (
        <p className="muted small">The fee goes on the next invoice once the scope is marked done. Time logged in the scope is covered by the fee.</p>
      )}
      {f.type === "retainer" && (
        <p className="muted small">The fee is billed each month between the start and end dates. Hours past the included amount are billed at the rate.</p>
      )}
      <div className="grid-2">
        {f.type !== "hourly" && (
          <label>
            {f.type === "fixed" ? "Fixed fee" : "Monthly fee"} ({ws.currency})
            <input inputMode="decimal" value={f.fee} onChange={set("fee")} required disabled={!!scope?.invoiced_at} />
          </label>
        )}
        <label>
          {f.type === "retainer" ? "Overage rate" : "Hourly rate"} <span className="muted small">(blank = project rate)</span>
          <input inputMode="decimal" value={f.rate} onChange={set("rate")} />
        </label>
        {f.type === "retainer" && (
          <label>
            Included hours / month
            <input inputMode="decimal" value={f.included_hours} onChange={set("included_hours")} />
          </label>
        )}
      </div>
      <div className="grid-2">
        <label>
          Budget (hours)
          <input inputMode="decimal" value={f.budget_hours} onChange={set("budget_hours")} />
        </label>
        <label>
          Budget ({ws.currency})
          <input inputMode="decimal" value={f.budget_amount} onChange={set("budget_amount")} />
        </label>
        <label>
          Starts
          <input type="date" value={f.start_date} onChange={set("start_date")} />
        </label>
        <label>
          Ends
          <input type="date" value={f.end_date} onChange={set("end_date")} />
        </label>
      </div>
      <label>
        What's included
        <textarea rows={3} value={f.description} onChange={set("description")} placeholder="Deliverables, assumptions, what's out of scope" />
      </label>
      {err && <p className="error">{errorMessage(err)}</p>}
      <div className="dialog-actions">
        {scope && !scope.invoiced_at && (
          <button type="button" className="danger" disabled={remove.isPending}
            onClick={() => confirm(`Delete "${scope.title}"? Its time and tasks stay on the project.`) && remove.mutate()}>
            Delete
          </button>
        )}
        <span className="spacer" />
        <button type="button" className="ghost" onClick={onDone}>Cancel</button>
        <button disabled={save.isPending}>{scope ? "Save" : "Add scope"}</button>
      </div>
    </form>
  );
}
