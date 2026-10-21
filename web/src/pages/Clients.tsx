import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, del, errorMessage, patch, post, type Client } from "../api";
import { Dialog, Empty, intOrNull, money, orNull, str } from "../ui";
import { useWorkspace } from "../workspace";
import { ReportLinks } from "../reportLinks";

export default function Clients() {
  const { current: ws } = useWorkspace();
  const clients = useQuery({
    queryKey: ["clients", ws.id],
    queryFn: () => api<Client[]>(`/clients?workspace_id=${ws.id}`),
  });
  const [editing, setEditing] = useState<Client | "new" | null>(null);
  const [sharing, setSharing] = useState<Client | null>(null);

  return (
    <section className="page wide">
      <div className="page-head">
        <h1>Clients</h1>
        <button onClick={() => setEditing("new")}>New client</button>
      </div>

      {clients.isPending && <p className="muted">Loading…</p>}
      {clients.data?.length === 0 && (
        <Empty>No clients yet. Add the first one to start tracking projects and rates.</Empty>
      )}
      {clients.data && clients.data.length > 0 && (
        <div className="card flush">
          <div className="table-wrap">
            <table className="clickable">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Contact</th>
                  <th className="num">Rate</th>
                  <th className="num">Terms</th>
                  <th className="num">Projects</th>
                  <th aria-label="Share" />
                </tr>
              </thead>
              <tbody>
                {clients.data.map((c) => (
                  <tr key={c.id} onClick={() => setEditing(c)} tabIndex={0}
                    onKeyDown={(e) => e.key === "Enter" && setEditing(c)}>
                    <td>
                      <strong>{c.name}</strong>
                      {c.code && <span className="tag">{c.code}</span>}
                    </td>
                    <td className="muted">{c.contact_email ?? "—"}</td>
                    <td className="num">
                      {c.rate ? money(c.rate, ws.currency, true) : (
                        <span className="muted" title="Uses the workspace default">default</span>
                      )}
                    </td>
                    <td className="num">
                      {c.payment_terms_days ?? ws.payment_terms_days} days
                    </td>
                    <td className="num">
                      <Link to={`/projects?client=${c.id}`} onClick={(e) => e.stopPropagation()}>
                        {c.project_count}
                      </Link>
                    </td>
                    <td className="right">
                      <button className="ghost small" onClick={(e) => { e.stopPropagation(); setSharing(c); }}>
                        Share report
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <Dialog open={sharing !== null} title={sharing ? `Report links · ${sharing.name}` : "Report links"}
        onClose={() => setSharing(null)}>
        {sharing && <ReportLinks client={sharing} />}
      </Dialog>

      <Dialog
        open={editing !== null}
        title={editing === "new" ? "New client" : "Edit client"}
        onClose={() => setEditing(null)}
      >
        {editing !== null && (
          <ClientForm client={editing === "new" ? null : editing} onDone={() => setEditing(null)} />
        )}
      </Dialog>
    </section>
  );
}

function ClientForm({ client, onDone }: { client: Client | null; onDone: () => void }) {
  const qc = useQueryClient();
  const { current: ws } = useWorkspace();
  const [f, setF] = useState({
    name: client?.name ?? "",
    code: str(client?.code),
    contact_email: str(client?.contact_email),
    billing_address: str(client?.billing_address),
    rate: str(client?.rate),
    payment_terms_days: str(client?.payment_terms_days),
    tax_rate: str(client?.tax_rate),
    notes: str(client?.notes),
  });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) =>
    setF((cur) => ({ ...cur, [k]: e.target.value }));

  const body = () => ({
    name: f.name,
    code: orNull(f.code),
    contact_email: orNull(f.contact_email),
    billing_address: orNull(f.billing_address),
    rate: orNull(f.rate),
    payment_terms_days: intOrNull(f.payment_terms_days),
    tax_rate: orNull(f.tax_rate),
    notes: orNull(f.notes),
  });

  const done = () => {
    qc.invalidateQueries({ queryKey: ["clients"] });
    qc.invalidateQueries({ queryKey: ["projects"] });
    onDone();
  };
  const save = useMutation({
    mutationFn: () =>
      client
        ? patch(`/clients/${client.id}`, body())
        : post("/clients", { ...body(), workspace_id: ws.id }),
    onSuccess: done,
  });
  const remove = useMutation({ mutationFn: () => del(`/clients/${client!.id}`), onSuccess: done });

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
          Code <span className="muted">(for invoice numbers)</span>
          <input value={f.code} onChange={set("code")} placeholder="ACME" maxLength={20} />
        </label>
        <label>
          Contact email
          <input type="email" value={f.contact_email} onChange={set("contact_email")} />
        </label>
        <label className="span-2">
          Billing address
          <textarea rows={3} value={f.billing_address} onChange={set("billing_address")} />
        </label>
        <label>
          Hourly rate ({ws.currency})
          <input inputMode="decimal" value={f.rate} onChange={set("rate")}
            placeholder={ws.default_rate ? `Default ${ws.default_rate}` : "Workspace default"} />
        </label>
        <label>
          Payment terms (days)
          <input inputMode="numeric" value={f.payment_terms_days}
            onChange={set("payment_terms_days")} placeholder={`Default ${ws.payment_terms_days}`} />
        </label>
        <label>
          Tax rate override (%)
          <input inputMode="decimal" value={f.tax_rate} onChange={set("tax_rate")}
            placeholder={ws.tax_rate ? `Default ${ws.tax_rate}` : "No tax"} />
        </label>
        <label className="span-2">
          Notes
          <textarea rows={3} value={f.notes} onChange={set("notes")} />
        </label>
      </div>
      {(save.isError || remove.isError) && (
        <p className="error">{errorMessage(save.error ?? remove.error)}</p>
      )}
      <div className="dialog-actions">
        {client && (
          <button type="button" className="danger" disabled={remove.isPending}
            onClick={() => confirm(`Delete ${client.name}?`) && remove.mutate()}>
            Delete
          </button>
        )}
        <span className="spacer" />
        <button type="button" className="ghost" onClick={onDone}>Cancel</button>
        <button type="submit" disabled={save.isPending}>{client ? "Save" : "Add client"}</button>
      </div>
    </form>
  );
}
