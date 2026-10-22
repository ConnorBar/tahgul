import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import {
  api,
  errorMessage,
  post,
  type Client,
  type FeeBilling,
  type FeeInvoice,
  type FixedFee,
  type Invoice,
  type Scope,
} from "./api";
import { useScopes } from "./scopes";
import { Dialog, dayLabel, money, todayIn } from "./ui";
import { useWorkspace } from "./workspace";
import { Select } from "./Select";

/**
 * Fixed-price work: what's been invoiced and paid against a fixed fee, billing part or
 * all of it, and recording invoices that were sent from somewhere else.
 */

const date = (d: string) => dayLabel(d, { month: "short", day: "numeric", year: "numeric" });
const n = (s: string | null | undefined) => Number(s ?? 0);

function useInvalidate() {
  const qc = useQueryClient();
  return () =>
    Promise.all(
      ["invoices", "invoice", "unbilled", "invoice-totals", "scopes", "fixed-fees"].map((k) =>
        qc.invalidateQueries({ queryKey: [k] }),
      ),
    );
}

export function useFixedFees(open = true) {
  const { current: ws } = useWorkspace();
  return useQuery({
    queryKey: ["fixed-fees", ws.id, open],
    queryFn: () => api<FixedFee[]>(`/fixed-fees?workspace_id=${ws.id}${open ? "&open=true" : ""}`),
  });
}

/** One bar: paid, waiting for payment, on a draft, not invoiced yet. */
export function FeeBar({ b, currency }: { b: FeeBilling; currency: string }) {
  const fee = n(b.fee) || 1;
  const waiting = n(b.outstanding) - n(b.overdue);
  const parts = [
    { k: "paid", v: n(b.paid), label: "Paid" },
    { k: "overdue", v: n(b.overdue), label: "Overdue" },
    { k: "waiting", v: waiting, label: "Awaiting payment" },
    { k: "draft", v: n(b.in_draft), label: "On a draft" },
    { k: "left", v: n(b.to_invoice), label: "Not invoiced" },
  ].filter((p) => p.v > 0);
  return (
    <div className="fee">
      <div className="fee-bar" role="img"
        aria-label={parts.map((p) => `${p.label} ${money(String(p.v), currency)}`).join(", ")}>
        {parts.map((p) => <span key={p.k} className={`fee-${p.k}`} style={{ width: `${(p.v / fee) * 100}%` }} />)}
      </div>
      <div className="fee-legend small">
        {parts.map((p) => (
          <span key={p.k}><i className={`fee-${p.k}`} />{p.label} <strong>{money(String(p.v), currency)}</strong></span>
        ))}
      </div>
    </div>
  );
}

function FeeInvoiceRow({ i, currency }: { i: FeeInvoice; currency: string }) {
  const state = i.overdue ? "overdue" : i.status;
  const label = { draft: "Draft", sent: "Awaiting payment", paid: "Paid", void: "Void", overdue: "Overdue" }[state];
  return (
    <li className="fee-invoice">
      <span className="fi-main">
        <Link to={`/invoices/${i.id}`}>{i.number ? `#${i.number}` : "Draft"}</Link>
        {i.external && <span className="tag" title="Sent from elsewhere, tracked here">RECORDED</span>}
        <span className="muted small fi-when">
          {i.status === "paid" && i.paid_on ? `Paid ${date(i.paid_on)}` : i.status === "draft" ? "Not sent yet" : `Sent ${date(i.issue_date)} · due ${date(i.due_date)}`}
        </span>
      </span>
      <span className={`status-badge s-${state}`}>{label}</span>
      <span className="num">{money(i.amount, currency)}</span>
    </li>
  );
}

/** A fixed-fee scope's money: the bar, its invoices, and what you can do next. */
export function ContractCard({ scope, currency, hasClient, title }: {
  scope: Scope;
  currency: string;
  hasClient: boolean;
  title?: React.ReactNode;
}) {
  const b = scope.billing!;
  const [doing, setDoing] = useState<"bill" | "record" | null>(null);
  const left = n(b.to_invoice) > 0;
  return (
    <div className="card contract">
      <div className="contract-head">
        <div>
          <span className="label">Fixed price{scope.status === "done" ? " · done" : ""}</span>
          <h3>{title ?? scope.title}</h3>
        </div>
        <span className="contract-fee">{money(b.fee, currency)}</span>
      </div>
      <FeeBar b={b} currency={currency} />
      {b.invoices.length > 0 && (
        <ul className="fee-invoices">{b.invoices.map((i) => <FeeInvoiceRow key={i.id} i={i} currency={currency} />)}</ul>
      )}
      {left && (
        hasClient ? (
          <div className="row contract-actions">
            <button className="ghost small" onClick={() => setDoing("record")}>Record a sent invoice</button>
            <button className="ghost small" onClick={() => setDoing("bill")}>New invoice…</button>
          </div>
        ) : (
          <p className="muted small">Give the project a client to invoice it.</p>
        )
      )}
      {!left && n(b.outstanding) === 0 && n(b.in_draft) === 0 && <p className="ok small">Paid in full.</p>}
      <Dialog open={doing === "bill"} title={`Invoice ${scope.title}`} onClose={() => setDoing(null)}>
        {doing === "bill" && <BillFeeForm scope={scope} currency={currency} onDone={() => setDoing(null)} />}
      </Dialog>
      <Dialog open={doing === "record"} title="Record an invoice you sent" onClose={() => setDoing(null)}>
        {doing === "record" && <RecordInvoiceForm scope={scope} onDone={() => setDoing(null)} />}
      </Dialog>
    </div>
  );
}

/** Contract cards for every fixed-fee scope on a project. */
export function ContractPanel({ projectId, hasClient }: { projectId: string; hasClient: boolean }) {
  const { current: ws } = useWorkspace();
  const scopes = useScopes(projectId);
  const fixed = (scopes.data?.scopes ?? []).filter((s) => s.type === "fixed" && s.billing);
  if (!fixed.length) return null;
  return (
    <div className="contracts">
      {fixed.map((s) => <ContractCard key={s.id} scope={s} currency={ws.currency} hasClient={hasClient} />)}
    </div>
  );
}

/** Draft a Tahgul invoice for part or all of what's left of a fixed fee. */
export function BillFeeForm({ scope, currency, onDone }: { scope: Scope; currency: string; onDone: () => void }) {
  const { current: ws } = useWorkspace();
  const navigate = useNavigate();
  const invalidate = useInvalidate();
  const b = scope.billing!;
  const [amount, setAmount] = useState(b.to_invoice);
  const [issue, setIssue] = useState(todayIn(ws.timezone));
  const [description, setDescription] = useState("");
  const create = useMutation({
    mutationFn: () =>
      post<Invoice>(`/scopes/${scope.id}/invoice`, {
        amount,
        issue_date: issue,
        description: description.trim() || null,
      }),
    onSuccess: async (inv) => {
      await invalidate();
      onDone();
      navigate(`/invoices/${inv.id}`);
    },
  });
  const half = (n(b.fee) / 2).toFixed(2);
  return (
    <form onSubmit={(e: FormEvent) => { e.preventDefault(); create.mutate(); }}>
      <p className="muted small">
        {money(b.to_invoice, currency)} of the {money(b.fee, currency)} fee isn't on an invoice yet.
        Bill a deposit or a milestone now and the rest later; once the scope is marked done,
        whatever is left goes on the client's next invoice by itself.
      </p>
      <div className="grid-2">
        <label>
          Amount ({currency})
          <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required autoFocus />
        </label>
        <label>
          Issue date
          <input type="date" value={issue} onChange={(e) => setIssue(e.target.value)} required />
        </label>
      </div>
      <div className="row chips-row">
        {n(half) <= n(b.to_invoice) && n(half) < n(b.to_invoice) && (
          <button type="button" className="view-chip" onClick={() => setAmount(half)}>Half ({money(half, currency)})</button>
        )}
        <button type="button" className="view-chip" onClick={() => setAmount(b.to_invoice)}>All that's left</button>
      </div>
      <label>
        Line on the invoice <span className="muted small">(optional)</span>
        <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500}
          placeholder={n(amount) === n(b.fee) ? `${scope.title} (fixed fee)` : `${scope.title}: deposit`} />
      </label>
      {create.isError && <p className="error">{errorMessage(create.error)}</p>}
      <div className="dialog-actions">
        <span className="spacer" />
        <button type="button" className="ghost" onClick={onDone}>Cancel</button>
        <button disabled={create.isPending || !(n(amount) > 0)}>Create draft</button>
      </div>
    </form>
  );
}

/**
 * Record an invoice sent from somewhere else (Word, another app, an email) so its payment
 * can be tracked here. Against a fixed fee when given one; otherwise pick the client.
 */
export function RecordInvoiceForm({ scope, onDone }: { scope?: Scope | null; onDone: (inv?: Invoice) => void }) {
  const { current: ws } = useWorkspace();
  const invalidate = useInvalidate();
  const today = todayIn(ws.timezone);
  const clients = useQuery({
    queryKey: ["clients", ws.id],
    queryFn: () => api<Client[]>(`/clients?workspace_id=${ws.id}`),
    enabled: !scope,
  });
  const fees = useFixedFees(true);
  const [f, setF] = useState({
    client_id: "",
    scope_id: scope?.id ?? "",
    number: "",
    issue_date: today,
    due_date: "",
    amount: scope?.billing?.to_invoice ?? "",
    status: "sent" as "sent" | "paid",
    paid_on: today,
    description: "",
  });
  const [file, setFile] = useState<File | null>(null);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((c) => ({ ...c, [k]: e.target.value }));
  const clientFees = (fees.data ?? []).filter((x) => x.client_id === f.client_id && n(x.billing.to_invoice) > 0);
  const pickFee = (id: string) => {
    const fee = clientFees.find((x) => x.scope_id === id);
    setF((c) => ({ ...c, scope_id: id, amount: fee ? fee.billing.to_invoice : c.amount }));
  };
  const save = useMutation({
    mutationFn: async () => {
      const inv = await post<Invoice>("/invoices/external", {
        scope_id: f.scope_id || null,
        client_id: f.scope_id ? null : f.client_id,
        number: f.number.trim(),
        issue_date: f.issue_date,
        due_date: f.due_date || null,
        amount: f.amount,
        status: f.status,
        paid_on: f.status === "paid" ? f.paid_on : null,
        description: f.description.trim() || null,
      });
      if (file) {
        const fd = new FormData();
        fd.append("file", file, file.name);
        return api<Invoice>(`/invoices/${inv.id}/file`, { method: "POST", body: fd });
      }
      return inv;
    },
    onSuccess: async (inv) => {
      await invalidate();
      onDone(inv);
    },
  });
  return (
    <form onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
      <p className="muted small">
        For an invoice you already sent from somewhere else. It keeps its own number (your
        Tahgul numbering isn't touched) and counts towards outstanding and paid.
      </p>
      {!scope && (
        <div className="grid-2">
          <label>
            Client
            <Select value={f.client_id} required onChange={(e) => setF((c) => ({ ...c, client_id: e.target.value, scope_id: "" }))}>
              <option value="" disabled>Pick a client</option>
              {clients.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </Select>
          </label>
          <label>
            For
            <Select value={f.scope_id} onChange={(e) => pickFee(e.target.value)} disabled={!clientFees.length}>
              <option value="">{clientFees.length ? "Other work" : "No open fixed fees"}</option>
              {clientFees.map((x) => (
                <option key={x.scope_id} value={x.scope_id}>{x.title} ({money(x.billing.to_invoice, x.currency)} left)</option>
              ))}
            </Select>
          </label>
        </div>
      )}
      <div className="grid-2">
        <label>
          Invoice number
          <input value={f.number} onChange={set("number")} required maxLength={60} placeholder="As it appears on the invoice" autoFocus />
        </label>
        <label>
          Amount ({ws.currency})
          <input inputMode="decimal" value={f.amount} onChange={set("amount")} required />
        </label>
        <label>
          Sent on
          <input type="date" value={f.issue_date} onChange={set("issue_date")} required max={today} />
        </label>
        <label>
          Due <span className="muted small">(blank = usual terms)</span>
          <input type="date" value={f.due_date} onChange={set("due_date")} min={f.issue_date} />
        </label>
      </div>
      <div className="seg-control wide" role="radiogroup" aria-label="Payment">
        {([["sent", "Waiting for payment"], ["paid", "Already paid"]] as const).map(([v, label]) => (
          <button key={v} type="button" role="radio" aria-checked={f.status === v} className={f.status === v ? "on" : undefined}
            onClick={() => setF((c) => ({ ...c, status: v }))}>{label}</button>
        ))}
      </div>
      {f.status === "paid" && (
        <label>
          Paid on
          <input type="date" value={f.paid_on} onChange={set("paid_on")} required max={today} />
        </label>
      )}
      {!f.scope_id && (
        <label>
          What it was for <span className="muted small">(optional)</span>
          <input value={f.description} onChange={set("description")} maxLength={500} placeholder={`Invoice ${f.number || "…"}`} />
        </label>
      )}
      <div className="file-field">
        <span className="field-label">The PDF you sent <span className="muted small">(optional)</span></span>
        <div className="row">
          <label className="button ghost file-btn">
            {file ? "Choose another" : "Attach PDF"}
            <input type="file" accept="application/pdf,.pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
          {file && (
            <>
              <span className="small">{file.name}</span>
              <button type="button" className="icon" aria-label="Remove file" onClick={() => setFile(null)}>×</button>
            </>
          )}
        </div>
      </div>
      {save.isError && <p className="error">{errorMessage(save.error)}</p>}
      <div className="dialog-actions">
        <span className="spacer" />
        <button type="button" className="ghost" onClick={() => onDone()}>Cancel</button>
        <button disabled={save.isPending || !f.number.trim() || !(n(f.amount) > 0) || (!scope && !f.client_id)}>Record invoice</button>
      </div>
    </form>
  );
}

/** The Invoices page section: fixed-price work that isn't fully paid yet. */
export function OpenFixedFees() {
  const fees = useFixedFees(true);
  if (!fees.data?.length) return null;
  return (
    <div className="group">
      <h2 className="group-title">Fixed-price work</h2>
      <div className="contracts">
        {fees.data.map((x) => (
          <div key={x.scope_id} className="card contract">
            <div className="contract-head">
              <div>
                <span className="label">{x.client_name ?? "No client"}</span>
                <h3><Link to={`/projects/${x.project_id}?view=scopes`}>{x.title}</Link>
                  {x.title !== x.project_name && <span className="muted small"> · {x.project_name}</span>}</h3>
              </div>
              <span className="contract-fee">{money(x.billing.fee, x.currency)}</span>
            </div>
            <FeeBar b={x.billing} currency={x.currency} />
            {x.billing.invoices.length > 0 && (
              <ul className="fee-invoices">
                {x.billing.invoices.map((i) => <FeeInvoiceRow key={i.id} i={i} currency={x.currency} />)}
              </ul>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
