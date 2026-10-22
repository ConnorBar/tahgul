import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  INVOICE_GROUPINGS,
  api,
  del,
  errorMessage,
  patch,
  post,
  type Client,
  type Invoice,
  type InvoiceLine,
  type InvoiceStatus,
  type InvoiceSummary,
  type InvoiceTotals,
  type Unbilled,
} from "../api";
import { Dialog, Empty, addDays, dayLabel, hoursLabel, money, orNull, str, todayIn } from "../ui";
import { useWorkspace } from "../workspace";
import { Select } from "../Select";

const STATUS_LABEL: Record<InvoiceStatus, string> = {
  draft: "Draft",
  sent: "Sent",
  paid: "Paid",
  void: "Void",
};

const GROUP_LABEL: Record<(typeof INVOICE_GROUPINGS)[number], string> = {
  project: "By project",
  activity: "By activity",
  scope: "By scope",
  day: "By day",
  entry: "Each entry",
};

const date = (d: string) => dayLabel(d, { month: "short", day: "numeric", year: "numeric" });

function useInvoiceInvalidation() {
  const qc = useQueryClient();
  return () =>
    Promise.all(
      ["invoices", "invoice", "unbilled", "invoice-totals", "entries", "summary", "scopes"].map((k) =>
        qc.invalidateQueries({ queryKey: [k] }),
      ),
    );
}

function StatusBadge({ inv }: { inv: InvoiceSummary }) {
  const label = inv.overdue ? "Overdue" : STATUS_LABEL[inv.status];
  return <span className={`status-badge s-${inv.overdue ? "overdue" : inv.status}`}>{label}</span>;
}

// --- List ---------------------------------------------------------------------------------

export default function Invoices() {
  const { current: ws } = useWorkspace();
  const [status, setStatus] = useState<InvoiceStatus | "">("");
  const [creating, setCreating] = useState<string | "pick" | null>(null);
  const navigate = useNavigate();
  const invoices = useQuery({
    queryKey: ["invoices", ws.id, status],
    queryFn: () =>
      api<InvoiceSummary[]>(`/invoices?workspace_id=${ws.id}${status ? `&status=${status}` : ""}`),
  });
  const unbilled = useQuery({
    queryKey: ["unbilled", ws.id],
    queryFn: () => api<Unbilled[]>(`/invoices/unbilled?workspace_id=${ws.id}`),
  });
  const totals = useQuery({
    queryKey: ["invoice-totals", ws.id],
    queryFn: () => api<InvoiceTotals>(`/invoices/totals?workspace_id=${ws.id}`),
  });
  const year = todayIn(ws.timezone).slice(0, 4);

  return (
    <section className="page wide">
      <div className="page-head">
        <h1>Invoices</h1>
        <div className="row">
          <a className="button ghost" href={`/api/v1/exports/invoices.csv?workspace_id=${ws.id}&year=${year}`}>
            Export {year} (CSV)
          </a>
          <button onClick={() => setCreating("pick")}>New invoice</button>
        </div>
      </div>

      {totals.data && (
        <div className="stats">
          <div className="stat">
            <span className="muted small">Outstanding</span>
            <span className="stat-n">{money(totals.data.outstanding, ws.currency)}</span>
          </div>
          <div className="stat">
            <span className="muted small">Overdue</span>
            <span className={`stat-n${Number(totals.data.overdue) > 0 ? " danger-text" : ""}`}>
              {money(totals.data.overdue, ws.currency)}
            </span>
          </div>
          <div className="stat">
            <span className="muted small">Paid in {year}</span>
            <span className="stat-n">{money(totals.data.paid_this_year, ws.currency)}</span>
          </div>
        </div>
      )}

      {unbilled.data && unbilled.data.length > 0 && (
        <div className="group">
          <h2 className="group-title">Ready to bill</h2>
          <div className="card flush">
            <div className="table-wrap">
              <table className="ready">
                <tbody>
                  {unbilled.data.map((u) => (
                    <tr key={u.client_id}>
                      <td><strong>{u.client_name}</strong>
                        <div className="small muted">{date(u.first_date)} – {date(u.last_date)} · {u.entries} entr{u.entries === 1 ? "y" : "ies"}</div>
                      </td>
                      <td className="num">{hoursLabel(u.hours) || 0} h</td>
                      <td className="num strong">{money(u.amount, u.currency)}</td>
                      <td className="right">
                        <button className="ghost" onClick={() => setCreating(u.client_id)}>Draft invoice</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      <div className="group">
        <div className="row">
          <h2 className="group-title">All invoices</h2>
          <span className="spacer" />
          <Select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value as InvoiceStatus | "")}>
            <option value="">Any status</option>
            {(Object.keys(STATUS_LABEL) as InvoiceStatus[]).map((s) => (
              <option key={s} value={s}>{STATUS_LABEL[s]}</option>
            ))}
          </Select>
        </div>
        {invoices.data?.length === 0 && (
          <Empty>No invoices{status ? ` marked ${STATUS_LABEL[status].toLowerCase()}` : " yet"}.</Empty>
        )}
        {invoices.data && invoices.data.length > 0 && (
          <div className="card flush">
            <div className="table-wrap">
              <table className="clickable">
                <thead>
                  <tr><th>Number</th><th>Client</th><th className="hide-narrow">Issued</th><th>Due</th><th className="num">Total</th><th>Status</th></tr>
                </thead>
                <tbody>
                  {invoices.data.map((i) => (
                    <tr key={i.id} tabIndex={0} onClick={() => navigate(`/invoices/${i.id}`)}
                      onKeyDown={(e) => e.key === "Enter" && navigate(`/invoices/${i.id}`)}>
                      <td><Link to={`/invoices/${i.id}`}>{i.number ?? "Draft"}</Link></td>
                      <td>{i.client_name}</td>
                      <td className="hide-narrow">{date(i.issue_date)}</td>
                      <td>{i.status === "paid" && i.paid_on ? <span className="muted">paid {date(i.paid_on)}</span> : date(i.due_date)}</td>
                      <td className="num">{money(i.total, i.currency)}</td>
                      <td><StatusBadge inv={i} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      <Dialog open={creating !== null} title="New invoice" onClose={() => setCreating(null)}>
        {creating !== null && (
          <NewInvoice clientId={creating === "pick" ? "" : creating}
            unbilled={unbilled.data ?? []}
            onDone={(inv) => { setCreating(null); if (inv) navigate(`/invoices/${inv.id}`); }} />
        )}
      </Dialog>
    </section>
  );
}

function NewInvoice({ clientId, unbilled, onDone }: {
  clientId: string;
  unbilled: Unbilled[];
  onDone: (inv: Invoice | null) => void;
}) {
  const { current: ws } = useWorkspace();
  const invalidate = useInvoiceInvalidation();
  const clients = useQuery({
    queryKey: ["clients", ws.id],
    queryFn: () => api<Client[]>(`/clients?workspace_id=${ws.id}`),
  });
  const today = todayIn(ws.timezone);
  const lastMonthEnd = addDays(`${today.slice(0, 7)}-01`, -1);
  const pick = (id: string) => {
    const u = unbilled.find((x) => x.client_id === id);
    return { start: u?.first_date ?? `${lastMonthEnd.slice(0, 7)}-01`, end: u ? (u.last_date > today ? u.last_date : today) : lastMonthEnd };
  };
  const [f, setF] = useState({ client_id: clientId, group_by: "project", ...pick(clientId) });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) =>
    setF((c) => ({ ...c, [k]: e.target.value }));
  const create = useMutation({
    mutationFn: () =>
      post<Invoice>("/invoices", {
        client_id: f.client_id,
        period_start: f.start,
        period_end: f.end,
        group_by: f.group_by,
      }),
    onSuccess: async (inv) => {
      await invalidate();
      onDone(inv);
    },
  });
  const ranges: [string, string, string][] = [
    ["Last month", `${lastMonthEnd.slice(0, 7)}-01`, lastMonthEnd],
    ["This month", `${today.slice(0, 7)}-01`, today],
  ];
  return (
    <form onSubmit={(e: FormEvent) => { e.preventDefault(); create.mutate(); }}>
      <label>
        Client
        <Select value={f.client_id} required
          onChange={(e) => setF((c) => ({ ...c, client_id: e.target.value, ...pick(e.target.value) }))}>
          <option value="" disabled>Pick a client</option>
          {clients.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </Select>
      </label>
      <div className="grid-2">
        <label>
          Work from
          <input type="date" value={f.start} onChange={set("start")} required />
        </label>
        <label>
          Work to
          <input type="date" value={f.end} onChange={set("end")} required />
        </label>
      </div>
      <div className="row">
        {ranges.map(([label, s, e]) => (
          <button type="button" key={label} className="view-chip" onClick={() => setF((c) => ({ ...c, start: s, end: e }))}>
            {label}
          </button>
        ))}
      </div>
      <label>
        Lines
        <Select value={f.group_by} onChange={set("group_by")}>
          {INVOICE_GROUPINGS.map((g) => <option key={g} value={g}>{GROUP_LABEL[g]}</option>)}
        </Select>
      </label>
      <p className="muted small">
        Pulls in billable time not yet invoiced, finished fixed-fee scopes and retainer months.
        It stays a draft until you issue it.
      </p>
      {create.isError && <p className="error">{errorMessage(create.error)}</p>}
      <div className="dialog-actions">
        <span className="spacer" />
        <button type="button" className="ghost" onClick={() => onDone(null)}>Cancel</button>
        <button disabled={create.isPending || !f.client_id}>Create draft</button>
      </div>
    </form>
  );
}

// --- One invoice ------------------------------------------------------------------------

export function InvoicePage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const invalidate = useInvoiceInvalidation();
  const q = useQuery({ queryKey: ["invoice", id], queryFn: () => api<Invoice>(`/invoices/${id}`) });
  const [warnings, setWarnings] = useState<string[] | null>(null);
  const [preview, setPreview] = useState(0);
  const act = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) => post<Invoice>(`/invoices/${id}/${path}`, body ?? {}),
    onSuccess: async (inv) => {
      setWarnings(inv.warnings);
      await invalidate();
      setPreview((n) => n + 1);
    },
  });
  const remove = useMutation({
    mutationFn: () => del(`/invoices/${id}`),
    onSuccess: async () => {
      await invalidate();
      navigate("/invoices");
    },
  });
  const inv = q.data;
  if (q.isError) return <section className="page"><p>Invoice not found. <Link to="/invoices">All invoices</Link></p></section>;
  if (!inv) return <section className="page"><p className="muted">Loading…</p></section>;
  const draft = inv.status === "draft";
  const shown = warnings ?? inv.warnings;

  return (
    <section className="page full">
      <div className="page-head">
        <div>
          <p className="muted small crumbs"><Link to="/invoices">Invoices</Link> · {inv.client_name}</p>
          <h1>{inv.number ? `Invoice ${inv.number}` : "Draft invoice"} <StatusBadge inv={inv} /></h1>
        </div>
        <div className="row">
          <a className="button ghost" href={`/api/v1/invoices/${inv.id}/pdf${draft ? "" : "?download=true"}`}
            target={draft ? "_blank" : undefined} rel="noreferrer">
            {draft ? "Preview PDF" : "Download PDF"}
          </a>
          {draft && (
            <>
              <button className="ghost" disabled={act.isPending} onClick={() => act.mutate({ path: "rebuild" })}
                title="Pull in time logged since the draft was made">Refresh from time</button>
              <button disabled={act.isPending || !inv.lines.length}
                onClick={() => confirm(`Issue this invoice for ${money(inv.total, inv.currency)}? It gets the next number and can't be edited afterwards.`) && act.mutate({ path: "issue" })}>
                Issue invoice
              </button>
            </>
          )}
          {inv.status === "sent" && <MarkPaid inv={inv} onPaid={(paid_on) => act.mutate({ path: "paid", body: { paid_on } })} />}
          {inv.status === "paid" && (
            <button className="ghost" onClick={() => act.mutate({ path: "unpaid" })}>Mark unpaid</button>
          )}
        </div>
      </div>
      {act.isError && <p className="error">{errorMessage(act.error)}</p>}
      {draft && shown.length > 0 && (
        <div className="notice">
          {shown.map((w) => <div key={w}>{w}</div>)}
        </div>
      )}

      <div className="invoice-layout">
        <div>
          <InvoiceMeta inv={inv} onSaved={(i) => { setWarnings(i.warnings); setPreview((n) => n + 1); }} />
          <Lines inv={inv} onChanged={() => setPreview((n) => n + 1)} />
          <div className="danger-zone row">
            {draft && (
              <button className="danger" disabled={remove.isPending}
                onClick={() => confirm("Delete this draft? Its time goes back to unbilled.") && remove.mutate()}>
                Delete draft
              </button>
            )}
            {(inv.status === "sent" || inv.status === "paid") && (
              <button className="danger" disabled={act.isPending}
                onClick={() => confirm(`Void ${inv.number}? The number stays used, and its time goes back to unbilled.`) && act.mutate({ path: "void" })}>
                Void invoice
              </button>
            )}
          </div>
        </div>
        <div className="invoice-preview card flush">
          <iframe key={`${preview}-${inv.status}`} title="Invoice preview" sandbox=""
            src={`/api/v1/invoices/${inv.id}/html`} />
        </div>
      </div>
    </section>
  );
}

function MarkPaid({ inv, onPaid }: { inv: Invoice; onPaid: (d: string) => void }) {
  const { current: ws } = useWorkspace();
  const [open, setOpen] = useState(false);
  const [d, setD] = useState(todayIn(ws.timezone));
  if (!open) return <button onClick={() => setOpen(true)}>Mark paid</button>;
  return (
    <form className="row" onSubmit={(e) => { e.preventDefault(); onPaid(d); setOpen(false); }}>
      <input type="date" aria-label="Paid on" value={d} onChange={(e) => setD(e.target.value)} max={todayIn(ws.timezone)} />
      <button>Paid {inv.number}</button>
      <button type="button" className="ghost" onClick={() => setOpen(false)}>Cancel</button>
    </form>
  );
}

function InvoiceMeta({ inv, onSaved }: { inv: Invoice; onSaved: (i: Invoice) => void }) {
  const invalidate = useInvoiceInvalidation();
  const draft = inv.status === "draft";
  const init = () => ({
    period_start: inv.period_start,
    period_end: inv.period_end,
    group_by: inv.group_by as string,
    issue_date: inv.issue_date,
    due_date: inv.due_date,
    tax_rate: str(inv.tax_rate),
    notes: str(inv.notes),
  });
  const [f, setF] = useState(init);
  useEffect(() => setF(init()), [inv.id, inv.status]); // eslint-disable-line react-hooks/exhaustive-deps
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((c) => ({ ...c, [k]: e.target.value }));
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => patch<Invoice>(`/invoices/${inv.id}`, body),
    onSuccess: async (i) => {
      await invalidate();
      onSaved(i);
      setF((c) => ({ ...c, due_date: i.due_date }));
    },
  });
  const field = (k: keyof typeof f, value: unknown = f[k]) => () => {
    if (String(value ?? "") !== String((inv as unknown as Record<string, unknown>)[k] ?? "")) save.mutate({ [k]: value });
  };

  if (!draft)
    return (
      <div className="card invoice-meta">
        <div className="grid-2">
          <div><span className="label">Bill to</span><div className="pre">{inv.bill_to}</div></div>
          <div className="small">
            <div>Issued {date(inv.issue_date)}{inv.sent_at && " · sent"}</div>
            <div>Due {date(inv.due_date)}</div>
            <div>Work {date(inv.period_start)} – {date(inv.period_end)}</div>
            {inv.paid_on && <div className="ok">Paid {date(inv.paid_on)}</div>}
            {inv.voided_at && <div className="danger-text">Voided</div>}
          </div>
        </div>
        {inv.notes && <p className="pre small">{inv.notes}</p>}
      </div>
    );

  return (
    <div className="card invoice-meta">
      <div className="grid-3">
        <label>
          Work from
          <input type="date" value={f.period_start} onChange={set("period_start")} onBlur={field("period_start")} />
        </label>
        <label>
          Work to
          <input type="date" value={f.period_end} onChange={set("period_end")} onBlur={field("period_end")} />
        </label>
        <label>
          Lines
          <Select value={f.group_by} onChange={(e) => { setF((c) => ({ ...c, group_by: e.target.value })); save.mutate({ group_by: e.target.value }); }}>
            {INVOICE_GROUPINGS.map((g) => <option key={g} value={g}>{GROUP_LABEL[g]}</option>)}
          </Select>
        </label>
        <label>
          Issue date
          <input type="date" value={f.issue_date} onChange={set("issue_date")} onBlur={field("issue_date")} />
        </label>
        <label>
          Due date
          <input type="date" value={f.due_date} onChange={set("due_date")} onBlur={field("due_date")} />
        </label>
        <label>
          Tax (%)
          <input inputMode="decimal" value={f.tax_rate} onChange={set("tax_rate")}
            onBlur={field("tax_rate", orNull(f.tax_rate))} placeholder="none" />
        </label>
      </div>
      <label>
        Note on the invoice
        <textarea rows={2} value={f.notes} onChange={set("notes")} onBlur={field("notes", orNull(f.notes))}
          placeholder="PO number, thanks, anything the client should see" />
      </label>
      {save.isError && <p className="error small">{errorMessage(save.error)}</p>}
    </div>
  );
}

function Lines({ inv, onChanged }: { inv: Invoice; onChanged: () => void }) {
  const invalidate = useInvoiceInvalidation();
  const draft = inv.status === "draft";
  const [adding, setAdding] = useState(false);
  const run = useMutation({
    mutationFn: (fn: () => Promise<unknown>) => fn(),
    onSuccess: async () => {
      await invalidate();
      onChanged();
    },
  });
  const unit = (ln: InvoiceLine) => (ln.unit === "hours" ? "h" : ln.unit === "month" ? "mo" : "");
  return (
    <div className="card flush lines">
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Description</th><th className="num">Qty</th><th className="num">Rate</th><th className="num">Amount</th>{draft && <th />}</tr>
          </thead>
          <tbody>
            {inv.lines.map((ln) => (
              <tr key={ln.id}>
                <td>
                  {draft ? (
                    <LineText value={ln.description}
                      onSave={(description) => run.mutate(() => patch(`/invoices/${inv.id}/lines/${ln.id}`, { description }))} />
                  ) : ln.description}
                  <div className="small muted">
                    {ln.kind === "manual" ? "added by hand" : ln.kind === "time" ? `${ln.entry_count} time entr${ln.entry_count === 1 ? "y" : "ies"}` : `${ln.kind}${ln.entry_count ? ` · covers ${ln.entry_count} entr${ln.entry_count === 1 ? "y" : "ies"}` : ""}`}
                  </div>
                </td>
                <td className="num">{hoursLabel(ln.quantity) || "0"} {unit(ln)}</td>
                <td className="num">{money(ln.unit_price, inv.currency, ln.unit === "hours")}</td>
                <td className="num">{money(ln.amount, inv.currency)}</td>
                {draft && (
                  <td className="right">
                    <button className="icon" aria-label={`Remove ${ln.description}`} title="Remove line"
                      onClick={() => run.mutate(() => del(`/invoices/${inv.id}/lines/${ln.id}`))}>×</button>
                  </td>
                )}
              </tr>
            ))}
            {!inv.lines.length && (
              <tr><td colSpan={draft ? 5 : 4} className="muted">No lines. No billable work in this period yet.</td></tr>
            )}
          </tbody>
          <tfoot>
            {inv.tax_rate !== null && (
              <>
                <tr><td colSpan={3} className="right">Subtotal</td><td className="num">{money(inv.subtotal, inv.currency)}</td>{draft && <td />}</tr>
                <tr><td colSpan={3} className="right">Tax ({Number(inv.tax_rate)}%)</td><td className="num">{money(inv.tax_amount, inv.currency)}</td>{draft && <td />}</tr>
              </>
            )}
            <tr className="total-row">
              <td colSpan={3} className="right">Total{Number(inv.hours) > 0 && <span className="muted small"> · {hoursLabel(inv.hours)} h</span>}</td>
              <td className="num">{money(inv.total, inv.currency)}</td>
              {draft && <td />}
            </tr>
          </tfoot>
        </table>
      </div>
      {run.isError && <p className="error small">{errorMessage(run.error)}</p>}
      {draft && (adding ? (
        <ManualLine invoiceId={inv.id} currency={inv.currency}
          onDone={() => { setAdding(false); onChanged(); }} />
      ) : (
        <button className="ghost add-line" onClick={() => setAdding(true)}>+ Add a line (expense, discount…)</button>
      ))}
    </div>
  );
}

function LineText({ value, onSave }: { value: string; onSave: (v: string) => void }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <input className="line-text" aria-label="Line description" value={v} onChange={(e) => setV(e.target.value)}
      onBlur={() => v.trim() && v.trim() !== value && onSave(v.trim())}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
  );
}

function ManualLine({ invoiceId, currency, onDone }: { invoiceId: string; currency: string; onDone: () => void }) {
  const invalidate = useInvoiceInvalidation();
  const [f, setF] = useState({ description: "", quantity: "1", unit: "item", unit_price: "" });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((c) => ({ ...c, [k]: e.target.value }));
  const add = useMutation({
    mutationFn: () => post(`/invoices/${invoiceId}/lines`, f),
    onSuccess: async () => {
      await invalidate();
      onDone();
    },
  });
  return (
    <form className="manual-line" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
      <input aria-label="Description" placeholder="Hardware: 2× Raspberry Pi kits" value={f.description}
        onChange={set("description")} required autoFocus />
      <input aria-label="Quantity" inputMode="decimal" value={f.quantity} onChange={set("quantity")} required />
      <Select aria-label="Unit" value={f.unit} onChange={set("unit")}>
        <option value="item">item</option>
        <option value="hours">hours</option>
        <option value="month">month</option>
      </Select>
      <input aria-label={`Price (${currency})`} inputMode="decimal" placeholder={`Price (${currency}), − for a discount`}
        value={f.unit_price} onChange={set("unit_price")} required />
      <button disabled={add.isPending}>Add</button>
      <button type="button" className="ghost" onClick={onDone}>Cancel</button>
      {add.isError && <p className="error small">{errorMessage(add.error)}</p>}
    </form>
  );
}
