import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  del,
  errorMessage,
  patch,
  post,
  type CalendarAccount,
  type CalendarInfo,
} from "../api";
import { RemindersSettings } from "../reminders";
import { Dialog, Empty } from "../ui";

export default function CalendarSettings() {
  const qc = useQueryClient();
  const accounts = useQuery({ queryKey: ["calendar-accounts"], queryFn: () => api<CalendarAccount[]>("/calendar-accounts") });
  const [adding, setAdding] = useState(false);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["calendar-accounts"] });
    qc.invalidateQueries({ queryKey: ["calendars"] });
    qc.invalidateQueries({ queryKey: ["events"] });
  };
  const sync = useMutation({ mutationFn: () => post("/calendars/sync"), onSuccess: refresh });

  return (
    <>
      <div className="card">
        <div className="page-head">
          <h2>Calendars</h2>
          <div className="row">
            {!!accounts.data?.length && (
              <button className="ghost" onClick={() => sync.mutate()} disabled={sync.isPending}>
                {sync.isPending ? "Syncing…" : "Sync now"}
              </button>
            )}
            <button className="ghost" onClick={() => setAdding(true)}>Connect a calendar</button>
          </div>
        </div>
        <p className="muted">
          Tahgul syncs both ways with CalDAV, which the iPhone's Calendar app speaks. It pulls
          changes every few minutes and pushes yours right away. Tahgul only writes to calendars
          you mark <strong>Tahgul can edit</strong>; the rest are shown, never changed.
        </p>
        {accounts.data?.length === 0 && (
          <Empty>
            No calendar accounts yet. If you run the Docker stack with Radicale set up in
            <code> deploy/.env</code>, it connects itself on first start.
          </Empty>
        )}
      </div>

      {accounts.data?.map((a) => <Account key={a.id} account={a} onChange={refresh} />)}

      {accounts.data && <RemindersSettings accounts={accounts.data} />}

      <Dialog open={adding} title="Connect a calendar" onClose={() => setAdding(false)}>
        {adding && <AddAccount onDone={() => { setAdding(false); refresh(); }} />}
      </Dialog>
    </>
  );
}

function Account({ account, onChange }: { account: CalendarAccount; onChange: () => void }) {
  const remove = useMutation({ mutationFn: () => del(`/calendar-accounts/${account.id}`), onSuccess: onChange });
  const billable = useMutation({
    mutationFn: () => post(`/calendar-accounts/${account.id}/new-calendar?name=Billable&mirror_time_entries=true`),
    onSuccess: onChange,
  });
  const rediscover = useMutation({ mutationFn: () => post(`/calendar-accounts/${account.id}/discover`), onSuccess: onChange });
  const hasMirror = account.calendars.some((c) => c.mirror_time_entries);
  const err = remove.error ?? billable.error ?? rediscover.error;
  return (
    <div className="card">
      <div className="page-head">
        <div>
          <h2>{account.name}</h2>
          <p className="muted small">
            {account.kind === "icloud" ? "iCloud" : account.url} · {account.username}
            {account.last_synced_at && ` · synced ${new Date(account.last_synced_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`}
          </p>
        </div>
        <div className="row">
          <button className="ghost" onClick={() => rediscover.mutate()} disabled={rediscover.isPending}>Find new calendars</button>
          <button className="danger" onClick={() => confirm(`Disconnect ${account.name}? Nothing is deleted on the server.`) && remove.mutate()}>
            Disconnect
          </button>
        </div>
      </div>
      {account.last_error && <p className="error small">Last sync failed: {account.last_error}</p>}
      {err && <p className="error small">{errorMessage(err)}</p>}
      <div className="table-wrap">
        <table className="cal-table">
          <thead>
            <tr>
              <th>Calendar</th>
              <th>Show</th>
              <th>Tahgul can edit</th>
              <th>Default for new events</th>
              <th>Mirror logged time</th>
            </tr>
          </thead>
          <tbody>
            {account.calendars.map((c) => <CalendarRow key={c.id} cal={c} onChange={onChange} />)}
          </tbody>
        </table>
      </div>
      {!hasMirror && (
        <div className="reprice">
          <p className="muted small">
            A <strong>Billable</strong> calendar shows every time entry on your phone, so you can
            see what was billed at a glance.
          </p>
          <button className="ghost" onClick={() => billable.mutate()} disabled={billable.isPending}>
            Create a Billable calendar
          </button>
        </div>
      )}
    </div>
  );
}

function CalendarRow({ cal, onChange }: { cal: CalendarInfo; onChange: () => void }) {
  const save = useMutation({
    mutationFn: (body: Partial<CalendarInfo>) => patch(`/calendars/${cal.id}`, body),
    onSuccess: onChange,
  });
  const check = (field: keyof CalendarInfo, label: string, disabled = false) => (
    <input type="checkbox" checked={Boolean(cal[field])} disabled={disabled || save.isPending}
      aria-label={`${label}: ${cal.name}`} onChange={(e) => save.mutate({ [field]: e.target.checked })} />
  );
  return (
    <tr>
      <td>
        <span className="dot inline-dot" style={{ background: cal.colour ?? "#6b6f76" }} />
        {cal.name}
        {!cal.server_writable && <span className="tag">read-only</span>}
        {save.isError && <div className="error small">{errorMessage(save.error)}</div>}
      </td>
      <td>{check("enabled", "Show")}</td>
      <td>{check("writable", "Tahgul can edit", !cal.server_writable)}</td>
      <td>
        <input type="radio" name="default-calendar" checked={cal.is_default}
          disabled={!cal.server_writable || save.isPending} aria-label={`Default: ${cal.name}`}
          onChange={() => save.mutate({ is_default: true })} />
      </td>
      <td>{check("mirror_time_entries", "Mirror logged time", !cal.writable)}</td>
    </tr>
  );
}

function AddAccount({ onDone }: { onDone: () => void }) {
  const [f, setF] = useState({ name: "", kind: "radicale", url: "", username: "", password: "" });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((c) => ({ ...c, [k]: e.target.value }));
  const add = useMutation({
    mutationFn: () => post("/calendar-accounts", {
      ...f, name: f.name || (f.kind === "icloud" ? "iCloud" : "Calendar"), url: f.kind === "icloud" ? null : f.url,
    }),
    onSuccess: onDone,
  });
  return (
    <form onSubmit={(e: FormEvent) => { e.preventDefault(); add.mutate(); }}>
      <label>
        Server
        <select aria-label="Server" value={f.kind} onChange={set("kind")}>
          <option value="radicale">Radicale (self-hosted)</option>
          <option value="icloud">iCloud</option>
          <option value="caldav">Other CalDAV server</option>
        </select>
      </label>
      {f.kind !== "icloud" && (
        <label>
          Server URL
          <input type="url" value={f.url} onChange={set("url")} required
            placeholder={f.kind === "radicale" ? "http://radicale:5232/" : "https://"} />
        </label>
      )}
      <label>
        {f.kind === "icloud" ? "Apple ID email" : "Username"}
        <input value={f.username} onChange={set("username")} required autoComplete="off" />
      </label>
      <label>
        {f.kind === "icloud" ? "App-specific password" : "Password"}
        <input type="password" value={f.password} onChange={set("password")} required autoComplete="new-password" />
      </label>
      {f.kind === "icloud" && (
        <p className="muted small">
          Make an app-specific password at account.apple.com → Sign-In and Security. Your normal
          Apple ID password won't work. With iCloud, events live on Apple's servers.
        </p>
      )}
      <label>
        Name in Tahgul
        <input value={f.name} onChange={set("name")} placeholder={f.kind === "icloud" ? "iCloud" : "Homelab"} />
      </label>
      <p className="muted small">The password is stored encrypted. Calendars start read-only; pick which Tahgul may edit next.</p>
      {add.isError && <p className="error">{errorMessage(add.error)}</p>}
      <div className="dialog-actions">
        <span className="spacer" />
        <button type="button" className="ghost" onClick={onDone}>Cancel</button>
        <button type="submit" disabled={add.isPending}>{add.isPending ? "Connecting…" : "Connect"}</button>
      </div>
    </form>
  );
}
