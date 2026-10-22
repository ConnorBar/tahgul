import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, del, errorMessage, patch, post, type CalendarAccount, type Project } from "./api";
import { useWorkspace } from "./workspace";
import { Select } from "./Select";

type ReminderList = {
  id: string;
  account_id: string;
  account_name: string;
  url: string;
  name: string;
  enabled: boolean;
  include: "due" | "open";
  inbox_project_id: string | null;
  inbox_project_name: string | null;
  task_count: number;
  last_synced_at: string | null;
  last_error: string | null;
};
type RemoteList = { url: string; name: string; in_use: boolean };

const ago = (iso: string | null) => {
  if (!iso) return "never";
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`;
};

/** Settings → Calendars → Reminders: tasks as to-dos in Apple Reminders. */
export function RemindersSettings({ accounts }: { accounts: CalendarAccount[] }) {
  const { current: ws } = useWorkspace();
  const qc = useQueryClient();
  const lists = useQuery({ queryKey: ["reminder-lists"], queryFn: () => api<ReminderList[]>("/reminder-lists") });
  const projects = useQuery({
    queryKey: ["projects", ws.id, ""],
    queryFn: () => api<Project[]>(`/projects?workspace_id=${ws.id}`),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["reminder-lists"] });
  const [accountId, setAccountId] = useState("");
  const [existing, setExisting] = useState("");
  const account = accountId || accounts[0]?.id || "";
  const remote = useQuery({
    queryKey: ["todo-lists", account],
    queryFn: () => api<RemoteList[]>(`/calendar-accounts/${account}/todo-lists`),
    enabled: !!account,
  });
  const create = useMutation({
    mutationFn: () =>
      post("/reminder-lists", {
        account_id: account,
        workspace_id: ws.id,
        url: existing || null,
        name: existing ? remote.data?.find((r) => r.url === existing)?.name ?? "Tahgul" : "Tahgul",
      }),
    onSuccess: refresh,
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<ReminderList> }) => patch(`/reminder-lists/${id}`, body),
    onSuccess: refresh,
  });
  const remove = useMutation({ mutationFn: (id: string) => del(`/reminder-lists/${id}`), onSuccess: refresh });
  const sync = useMutation({ mutationFn: () => post("/reminder-lists/sync"), onSuccess: refresh });
  const err = create.error ?? update.error ?? remove.error ?? sync.error;
  const usable = (projects.data ?? []).filter((p) => p.status !== "done");

  return (
    <div className="card">
      <div className="page-head">
        <h2>Reminders</h2>
        {!!lists.data?.length && (
          <button className="ghost" onClick={() => sync.mutate()} disabled={sync.isPending}>
            {sync.isPending ? "Syncing…" : "Sync now"}
          </button>
        )}
      </div>
      <p className="muted">
        Put tasks in the iPhone's Reminders app. Tahgul keeps a to-do list on your calendar
        server: tasks show up there, ticking one off finishes the task, and edits go both ways.
        On the phone the list appears under the same CalDAV account as your calendars.
      </p>
      {lists.data?.map((l) => (
        <div key={l.id} className="reminder-list">
          <div className="row">
            <div>
              <strong>{l.name}</strong> <span className="muted small">on {l.account_name} · {l.task_count} task{l.task_count === 1 ? "" : "s"} · synced {ago(l.last_synced_at)}</span>
              {l.last_error && <div className="error small">{l.last_error}</div>}
            </div>
            <span className="spacer" />
            <button className="ghost danger-text"
              onClick={() => confirm(`Stop syncing "${l.name}"? Tahgul's to-dos are removed from the list; your tasks stay.`) && remove.mutate(l.id)}>
              Stop
            </button>
          </div>
          <div className="grid-2">
            <label>
              Which tasks
              <Select value={l.include} onChange={(e) => update.mutate({ id: l.id, body: { include: e.target.value as "due" | "open" } })}>
                <option value="due">Open tasks with a due date</option>
                <option value="open">Every open task</option>
              </Select>
            </label>
            <label>
              New reminders from the phone go to
              <Select value={l.inbox_project_id ?? ""}
                onChange={(e) => update.mutate({ id: l.id, body: { inbox_project_id: e.target.value || null } })}>
                <option value="">Nowhere (leave them on the phone)</option>
                {usable.map((p) => <option key={p.id} value={p.id}>{p.name}{p.client_name ? ` · ${p.client_name}` : ""}</option>)}
              </Select>
            </label>
          </div>
        </div>
      ))}
      {accounts.length === 0 ? (
        <p className="muted small">Connect a calendar account above first.</p>
      ) : (
        <div className="row reminder-add">
          {accounts.length > 1 && (
            <Select aria-label="Account" value={account} onChange={(e) => { setAccountId(e.target.value); setExisting(""); }}>
              {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </Select>
          )}
          <Select aria-label="List" value={existing} onChange={(e) => setExisting(e.target.value)}>
            <option value="">Make a new “Tahgul” list</option>
            {remote.data?.filter((r) => !r.in_use).map((r) => <option key={r.url} value={r.url}>Use “{r.name}”</option>)}
          </Select>
          <button onClick={() => create.mutate()} disabled={create.isPending || !account}>
            {create.isPending ? "Setting up…" : "Add to Reminders"}
          </button>
        </div>
      )}
      {err && <p className="error">{errorMessage(err)}</p>}
    </div>
  );
}
