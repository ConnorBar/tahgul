import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  errorMessage,
  patch,
  post,
  type NumberPreset,
  type PatternCheck,
  type Workspace,
} from "../api";
import { Dialog, orNull, str } from "../ui";
import { useWorkspace } from "../workspace";

const TIMEZONES = [
  "America/Chicago",
  "America/New_York",
  "America/Denver",
  "America/Los_Angeles",
  "America/Phoenix",
  "Europe/London",
  "UTC",
];
const CUSTOM = "__custom__";

export default function WorkspaceSettings() {
  const { current, setCurrent } = useWorkspace();
  const [creating, setCreating] = useState(false);
  return (
    <>
      {/* Remount the form when switching workspaces so fields reset. */}
      <WorkspaceForm key={current.id} ws={current} />
      <div className="card">
        <h2>Other workspaces</h2>
        <p className="muted">
          Keep separate spaces for, say, consulting and personal projects. Switch between them in
          the top bar.
        </p>
        <button className="ghost" onClick={() => setCreating(true)}>New workspace</button>
      </div>
      <Dialog open={creating} title="New workspace" onClose={() => setCreating(false)}>
        {creating && (
          <NewWorkspace
            onDone={(w) => {
              setCreating(false);
              if (w) setCurrent(w.id);
            }}
          />
        )}
      </Dialog>
    </>
  );
}

function WorkspaceForm({ ws }: { ws: Workspace }) {
  const qc = useQueryClient();
  const presets = useQuery({
    queryKey: ["number-presets"],
    queryFn: () => api<NumberPreset[]>("/invoice-numbers/presets"),
  });
  const isPreset = (p: string) => presets.data?.some((x) => x.pattern === p) ?? true;

  const [f, setF] = useState({
    name: ws.name,
    currency: ws.currency,
    default_rate: str(ws.default_rate),
    payment_terms_days: str(ws.payment_terms_days),
    timezone: ws.timezone,
    tax_on: ws.tax_rate !== null,
    tax_rate: str(ws.tax_rate),
    pattern: ws.invoice_number_pattern,
    custom: "",
  });
  const [choice, setChoice] = useState<string>(ws.invoice_number_pattern);
  useEffect(() => {
    // Once presets load, a pattern that isn't one of them is a custom one.
    if (presets.data && !isPreset(ws.invoice_number_pattern)) {
      setChoice(CUSTOM);
      setF((cur) => ({ ...cur, custom: ws.invoice_number_pattern }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presets.data]);

  const set = (k: keyof typeof f) => (e: { target: { value: string } }) =>
    setF((cur) => ({ ...cur, [k]: e.target.value }));

  const pattern = choice === CUSTOM ? f.custom : choice;
  const check = usePatternCheck(choice === CUSTOM ? f.custom : "");

  const save = useMutation({
    mutationFn: () =>
      patch<Workspace>(`/workspaces/${ws.id}`, {
        name: f.name,
        currency: f.currency,
        default_rate: orNull(f.default_rate),
        payment_terms_days: Number(f.payment_terms_days || 0),
        timezone: f.timezone,
        tax_rate: f.tax_on ? orNull(f.tax_rate) : null,
        invoice_number_pattern: pattern,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };

  return (
    <form className="card" onSubmit={submit}>
      <h2>Workspace</h2>
      <div className="grid-2">
        <label className="span-2">
          Name
          <input value={f.name} onChange={set("name")} required maxLength={200} />
        </label>
        <label>
          Default hourly rate
          <input inputMode="decimal" value={f.default_rate} onChange={set("default_rate")}
            placeholder="e.g. 125" />
        </label>
        <label>
          Currency
          <input value={f.currency} onChange={set("currency")} maxLength={3} required
            className="upper" />
        </label>
        <label>
          Payment terms (days)
          <input inputMode="numeric" value={f.payment_terms_days}
            onChange={set("payment_terms_days")} required />
        </label>
        <label>
          Time zone
          <input list="tz-list" value={f.timezone} onChange={set("timezone")} required />
          <datalist id="tz-list">
            {TIMEZONES.map((t) => <option key={t} value={t} />)}
          </datalist>
        </label>
      </div>

      <fieldset>
        <legend>Tax</legend>
        <label className="check">
          <input type="checkbox" checked={f.tax_on}
            onChange={(e) => setF((cur) => ({ ...cur, tax_on: e.target.checked }))} />
          <span>Add a tax line to invoices</span>
        </label>
        {f.tax_on ? (
          <label className="inline">
            Rate (%)
            <input inputMode="decimal" value={f.tax_rate} onChange={set("tax_rate")} required
              placeholder="e.g. 6.25" />
          </label>
        ) : (
          <p className="muted small">Off. You can still set a tax rate on an individual client.</p>
        )}
      </fieldset>

      <fieldset>
        <legend>Invoice numbers</legend>
        <div className="choices">
          {presets.data?.map((p) => (
            <label key={p.pattern} className={`choice${choice === p.pattern ? " on" : ""}`}>
              <input type="radio" name="pattern" value={p.pattern} checked={choice === p.pattern}
                onChange={() => setChoice(p.pattern)} />
              <span>
                <span>
                  <strong>{p.examples[0]}</strong>, {p.examples[1]}…
                </span>
                <span className="muted small">
                  {p.label} · resets {p.resets} · <code>{p.pattern}</code>
                </span>
              </span>
            </label>
          ))}
          <label className={`choice${choice === CUSTOM ? " on" : ""}`}>
            <input type="radio" name="pattern" checked={choice === CUSTOM}
              onChange={() => setChoice(CUSTOM)} />
            <span>
              <strong>Custom</strong>
              <span className="muted small">
                Tokens: {"{Y}"} {"{y}"} {"{M}"} {"{CLIENT}"} {"{seq}"} {"{seq:3}"}
              </span>
            </span>
          </label>
        </div>
        {choice === CUSTOM && (
          <div className="custom-pattern">
            <input value={f.custom} onChange={set("custom")} placeholder="e.g. TG-{Y}-{seq:3}"
              aria-label="Custom invoice number pattern" required />
            {check.data &&
              (check.data.valid ? (
                <p className="ok small">Next numbers: {check.data.examples.join(", ")}</p>
              ) : (
                <p className="error small">{check.data.errors.join(" ")}</p>
              ))}
          </div>
        )}
      </fieldset>

      {save.isError && <p className="error">{errorMessage(save.error)}</p>}
      {save.isSuccess && !save.isPending && <p className="ok">Saved.</p>}
      <button type="submit" disabled={save.isPending}>Save workspace</button>
    </form>
  );
}

function usePatternCheck(pattern: string) {
  const [debounced, setDebounced] = useState(pattern);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(pattern), 300);
    return () => clearTimeout(t);
  }, [pattern]);
  return useQuery({
    queryKey: ["pattern-check", debounced],
    queryFn: () =>
      api<PatternCheck>(`/invoice-numbers/preview?pattern=${encodeURIComponent(debounced)}`),
    enabled: debounced.trim() !== "",
  });
}

function NewWorkspace({ onDone }: { onDone: (w?: Workspace) => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => post<Workspace>("/workspaces", { name }),
    onSuccess: async (w) => {
      await qc.invalidateQueries({ queryKey: ["workspaces"] });
      onDone(w);
    },
  });
  return (
    <form onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
      <label>
        Name
        <input value={name} onChange={(e) => setName(e.target.value)} required autoFocus
          placeholder="e.g. Personal" />
      </label>
      <p className="muted small">Starts in USD, no tax, Central time. Change it after.</p>
      {create.isError && <p className="error">{errorMessage(create.error)}</p>}
      <div className="dialog-actions">
        <span className="spacer" />
        <button type="button" className="ghost" onClick={() => onDone()}>Cancel</button>
        <button type="submit" disabled={create.isPending}>Create</button>
      </div>
    </form>
  );
}
