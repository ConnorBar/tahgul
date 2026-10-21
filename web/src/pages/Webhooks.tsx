import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, del, errorMessage, patch, post } from "../api";
import { Dialog, Empty } from "../ui";

type Hook = {
  id: string;
  name: string;
  url: string;
  events: string[];
  active: boolean;
  last_delivery_at: string | null;
  last_status: string | null;
  created_at: string;
};
type Delivery = {
  id: string;
  event: string;
  status: "pending" | "sent" | "failed";
  attempts: number;
  response_code: number | null;
  error: string | null;
  created_at: string;
  next_attempt_at: string;
};
type EventInfo = { name: string; description: string };

const when = (iso: string) =>
  new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(iso));

/** Settings → Webhooks: tell the agent (or anything) when things happen. */
export default function Webhooks() {
  const qc = useQueryClient();
  const hooks = useQuery({ queryKey: ["webhooks"], queryFn: () => api<Hook[]>("/webhooks") });
  const [editing, setEditing] = useState<Hook | "new" | null>(null);
  const [secret, setSecret] = useState<{ name: string; secret: string } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const invalidate = () => qc.invalidateQueries({ queryKey: ["webhooks"] });
  const test = useMutation({
    mutationFn: (id: string) => post<Delivery>(`/webhooks/${id}/test`),
    onSuccess: () => { invalidate(); qc.invalidateQueries({ queryKey: ["deliveries"] }); },
  });
  const rotate = useMutation({
    mutationFn: (h: Hook) => post<Hook & { secret: string }>(`/webhooks/${h.id}/rotate-secret`),
    onSuccess: (h) => setSecret({ name: h.name, secret: h.secret }),
  });

  return (
    <>
      <p className="muted">
        Tahgul POSTs JSON to these URLs when things happen, so a local agent or script can react
        (say, "you stopped the timer after 3 hours: anything to note?"). Each request is signed
        with the webhook's secret in <code>X-Tahgul-Signature</code>; details in the repo's
        docs/webhooks.md.
      </p>
      {secret && (
        <div className="card reveal">
          <h2>Signing secret for “{secret.name}”</h2>
          <p className="muted">Shown once. Your receiver uses it to check each request came from Tahgul.</p>
          <code className="secret">{secret.secret}</code>
          <div className="row">
            <button onClick={() => navigator.clipboard?.writeText(secret.secret)}>Copy</button>
            <button className="ghost" onClick={() => setSecret(null)}>Done</button>
          </div>
        </div>
      )}
      <div className="row">
        <span className="spacer" />
        <button onClick={() => setEditing("new")}>New webhook</button>
      </div>
      {hooks.data?.length === 0 && <Empty>No webhooks yet.</Empty>}
      {hooks.data?.map((h) => (
        <div key={h.id} className="card webhook">
          <div className="row">
            <div className="webhook-main">
              <strong>{h.name}</strong>{!h.active && <span className="tag">OFF</span>}
              <div className="small muted mono">{h.url}</div>
              <div className="small">{h.events.includes("*") ? "All events" : h.events.join(", ")}</div>
              {h.last_delivery_at && (
                <div className={`small ${h.last_status === "ok" ? "ok" : h.last_status === "failed" ? "error" : "muted"}`}>
                  Last delivery {when(h.last_delivery_at)}: {h.last_status}
                </div>
              )}
            </div>
            <div className="row webhook-actions">
              <button className="ghost" disabled={test.isPending} onClick={() => test.mutate(h.id)}>Send test</button>
              <button className="ghost" onClick={() => setOpen(open === h.id ? null : h.id)}>
                {open === h.id ? "Hide log" : "Log"}
              </button>
              <button className="ghost" onClick={() => setEditing(h)}>Edit</button>
            </div>
          </div>
          {test.data && test.variables === h.id && (
            <p className={`small ${test.data.status === "sent" ? "ok" : "error"}`}>
              Test {test.data.status === "sent" ? `delivered (HTTP ${test.data.response_code})` : `failed: ${test.data.error}`}
            </p>
          )}
          {open === h.id && <Deliveries hookId={h.id} />}
          <button className="linkish small muted" onClick={() => confirm("Make a new signing secret? The old one stops working.") && rotate.mutate(h)}>
            New signing secret
          </button>
        </div>
      ))}
      <Dialog open={editing !== null} title={editing === "new" ? "New webhook" : "Edit webhook"} onClose={() => setEditing(null)}>
        {editing && (
          <HookForm hook={editing === "new" ? null : editing}
            onDone={(created) => { setEditing(null); invalidate(); if (created) setSecret(created); }} />
        )}
      </Dialog>
    </>
  );
}

function Deliveries({ hookId }: { hookId: string }) {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["deliveries", hookId],
    queryFn: () => api<Delivery[]>(`/webhooks/${hookId}/deliveries?limit=25`),
    refetchInterval: 10_000,
  });
  const retry = useMutation({
    mutationFn: (id: string) => post(`/webhook-deliveries/${id}/retry`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["deliveries", hookId] }),
  });
  if (!list.data?.length) return <p className="small muted">Nothing sent yet.</p>;
  return (
    <div className="table-wrap">
      <table className="small">
        <thead><tr><th>When</th><th>Event</th><th>Result</th><th /></tr></thead>
        <tbody>
          {list.data.map((d) => (
            <tr key={d.id}>
              <td>{when(d.created_at)}</td>
              <td className="mono">{d.event}</td>
              <td className={d.status === "sent" ? "ok" : d.status === "failed" ? "error" : "muted"}>
                {d.status === "sent" ? `sent (${d.response_code})` : d.status === "pending" && d.attempts
                  ? `retrying (${d.attempts} tries) · ${d.error ?? ""}` : d.status === "pending" ? "queued" : `failed · ${d.error ?? ""}`}
              </td>
              <td className="right">
                {d.status !== "pending" && <button className="linkish" onClick={() => retry.mutate(d.id)}>Resend</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HookForm({ hook, onDone }: { hook: Hook | null; onDone: (created?: { name: string; secret: string }) => void }) {
  const events = useQuery({ queryKey: ["webhook-events"], queryFn: () => api<EventInfo[]>("/webhooks/events") });
  const [f, setF] = useState({ name: hook?.name ?? "", url: hook?.url ?? "", active: hook?.active ?? true });
  const [picked, setPicked] = useState<string[]>(hook?.events ?? ["timer.stopped", "task.due_soon", "calendar.event_ended"]);
  const all = picked.includes("*");
  const save = useMutation({
    mutationFn: () => {
      const body = { ...f, events: picked };
      return hook ? patch<Hook>(`/webhooks/${hook.id}`, body) : post<Hook & { secret: string }>("/webhooks", body);
    },
    onSuccess: (h) => onDone("secret" in h ? { name: h.name, secret: (h as Hook & { secret: string }).secret } : undefined),
  });
  const remove = useMutation({ mutationFn: () => del(`/webhooks/${hook!.id}`), onSuccess: () => onDone() });
  const toggle = (e: string) => setPicked((p) => (p.includes(e) ? p.filter((x) => x !== e) : [...p.filter((x) => x !== "*"), e]));
  return (
    <form onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
      <label>Name<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required maxLength={100} placeholder="Local agent" /></label>
      <label>URL<input value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} required placeholder="http://agent.lan:8787/tahgul" /></label>
      <fieldset>
        <legend>Events</legend>
        <label className="check"><input type="checkbox" checked={all} onChange={() => setPicked(all ? [] : ["*"])} /><span>Everything</span></label>
        {!all && events.data?.map((e) => (
          <label key={e.name} className="check">
            <input type="checkbox" checked={picked.includes(e.name)} onChange={() => toggle(e.name)} />
            <span><code>{e.name}</code> <span className="muted small">{e.description}</span></span>
          </label>
        ))}
      </fieldset>
      {hook && (
        <label className="check"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /><span>On</span></label>
      )}
      {(save.error || remove.error) && <p className="error">{errorMessage(save.error ?? remove.error)}</p>}
      <div className="dialog-actions">
        {hook && <button type="button" className="danger" onClick={() => confirm(`Delete "${hook.name}"?`) && remove.mutate()}>Delete</button>}
        <span className="spacer" />
        <button type="button" className="ghost" onClick={() => onDone()}>Cancel</button>
        <button disabled={save.isPending || !picked.length}>{hook ? "Save" : "Add webhook"}</button>
      </div>
    </form>
  );
}
