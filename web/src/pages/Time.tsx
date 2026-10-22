import { useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import {
  api,
  del,
  errorMessage,
  patch,
  post,
  put,
  type Activity,
  type TimeEntry,
  type Week,
  type WeekRow,
} from "../api";
import { ProjectSelect, useProjects, useTimeInvalidation } from "../timer";
import {
  Dialog,
  Empty,
  addDays,
  dayLabel,
  hm,
  hoursLabel,
  money,
  parseHours,
  todayIn,
  zoned,
} from "../ui";
import { useWorkspace } from "../workspace";
import { ScopeSelect } from "../scopes";
import { DateTimeRange, minutesBetween, type DTRange } from "../calendar/DateTimeRange";
import { Select } from "../Select";

type RowKey = { project_id: string | null; activity_id: string | null };
const keyOf = (r: RowKey) => `${r.project_id ?? ""}|${r.activity_id ?? ""}`;

function useActivities() {
  const { current: ws } = useWorkspace();
  return useQuery({
    queryKey: ["activities", ws.id],
    queryFn: () => api<Activity[]>(`/activities?workspace_id=${ws.id}`),
  });
}

export default function Time() {
  const { current: ws } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const weekParam = params.get("week") ?? "";
  const week = useQuery({
    queryKey: ["timesheet", ws.id, weekParam],
    queryFn: () =>
      api<Week>(`/timesheet?workspace_id=${ws.id}${weekParam ? `&week=${weekParam}` : ""}`),
  });
  const w = week.data;
  const go = (n: number) => w && setParams({ week: addDays(w.week_start, n) });
  const thisWeek = w && todayIn(ws.timezone) >= w.days[0] && todayIn(ws.timezone) <= w.days[6];

  return (
    <section className="page wide">
      <div className="page-head">
        <h1>Time</h1>
        <div className="row week-nav">
          <button className="ghost" onClick={() => go(-7)} aria-label="Previous week">‹</button>
          <span className="week-label">
            {w ? `${dayLabel(w.days[0], { month: "short", day: "numeric" })} – ${dayLabel(w.days[6], { month: "short", day: "numeric", year: "numeric" })}` : "…"}
          </span>
          <button className="ghost" onClick={() => go(7)} aria-label="Next week">›</button>
          {!thisWeek && w && (
            <button className="ghost" onClick={() => setParams({})}>This week</button>
          )}
        </div>
      </div>
      {w && <Timesheet week={w} />}
      {w && <Entries week={w} />}
    </section>
  );
}

function Timesheet({ week }: { week: Week }) {
  const { current: ws } = useWorkspace();
  const activities = useActivities();
  const projects = useProjects();
  const invalidate = useTimeInvalidation();
  const [extra, setExtra] = useState<WeekRow[]>([]);
  const [adding, setAdding] = useState({ project: "", activity: "" });
  const [error, setError] = useState<string | null>(null);
  const today = todayIn(ws.timezone);

  const rows = [
    ...week.rows,
    ...extra.filter((x) => !week.rows.some((r) => keyOf(r) === keyOf(x))),
  ];

  const setCell = useMutation({
    mutationFn: (v: { row: RowKey; date: string; hours: number }) =>
      put<Week>("/timesheet/cell", {
        workspace_id: ws.id,
        project_id: v.row.project_id,
        activity_id: v.row.activity_id,
        date: v.date,
        hours: v.hours.toFixed(2),
      }),
    onSuccess: () => {
      setError(null);
      invalidate();
    },
    onError: (e) => setError(errorMessage(e)),
  });

  const addRow = () => {
    if (!adding.project && !adding.activity) return;
    const proj = projects.data?.find((p) => p.id === adding.project);
    const act = activities.data?.find((a) => a.id === adding.activity);
    setExtra((cur) => [
      ...cur,
      {
        project_id: adding.project || null,
        activity_id: adding.activity || null,
        project_name: proj?.name ?? null,
        client_name: proj?.client_name ?? null,
        colour: proj?.colour ?? null,
        activity_name: act?.name ?? null,
        hours: Array(7).fill("0.00"),
        total: "0.00",
      },
    ]);
    setAdding({ project: "", activity: "" });
  };

  return (
    <div className="card flush timesheet">
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Project</th>
              {week.days.map((d) => (
                <th key={d} className={`num day${d === today ? " today" : ""}`}>{dayLabel(d)}</th>
              ))}
              <th className="num">Total</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={keyOf(r)}>
                <td>
                  <div className="ts-name">
                    {r.colour && <span className="dot" style={{ background: r.colour }} />}
                    <span>
                      <RowName row={r} />
                    </span>
                  </div>
                </td>
                {week.days.map((d, i) => (
                  <td key={d} className={`num${d === today ? " today" : ""}`}>
                    <HoursCell
                      value={r.hours[i]}
                      label={`${r.project_name ?? "No project"}${r.activity_name ? ` (${r.activity_name})` : ""} ${dayLabel(d)}`}
                      onCommit={(h) => setCell.mutate({ row: r, date: d, hours: h })}
                      onInvalid={() => setError("Use hours like 1.5, 1:30 or 90m.")}
                    />
                  </td>
                ))}
                <td className="num strong">{hoursLabel(r.total) || "—"}</td>
              </tr>
            ))}
            <tr className="add-row">
              <td colSpan={9}>
                <div className="row">
                  <ProjectSelect value={adding.project}
                    onChange={(id) => setAdding((a) => ({ ...a, project: id }))}
                    label="Add a row for project" />
                  {activities.data && activities.data.length > 0 && (
                    <Select aria-label="Activity for new row" value={adding.activity}
                      onChange={(e) => setAdding((a) => ({ ...a, activity: e.target.value }))}>
                      <option value="">No activity</option>
                      {activities.data.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                    </Select>
                  )}
                  <button className="ghost" onClick={addRow}
                    disabled={!adding.project && !adding.activity}>
                    Add row
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
          <tfoot>
            <tr>
              <td>Total</td>
              {week.day_totals.map((t, i) => (
                <td key={i} className={`num${week.days[i] === today ? " today" : ""}`}>
                  {hoursLabel(t) || "—"}
                </td>
              ))}
              <td className="num strong">{hoursLabel(week.total) || "0"}</td>
            </tr>
          </tfoot>
        </table>
      </div>
      {error && <p className="error ts-error">{error}</p>}
      {week.running_entry_id && (
        <p className="muted small ts-note">The running timer is added when it stops.</p>
      )}
    </div>
  );
}

function RowName({ row }: { row: WeekRow }) {
  return (
    <>
      <strong>{row.project_name ?? "No project"}</strong>
      {row.client_name && <span className="muted"> · {row.client_name}</span>}
      {row.activity_name && <span className="tag">{row.activity_name}</span>}
    </>
  );
}

function HoursCell({
  value,
  label,
  onCommit,
  onInvalid,
}: {
  value: string;
  label: string;
  onCommit: (hours: number) => void;
  onInvalid: () => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? hoursLabel(value);
  const commit = () => {
    if (draft === null) return;
    const h = parseHours(draft);
    setDraft(null);
    if (h === null || h > 24) return onInvalid();
    if (Math.abs(h - Number(value)) >= 0.005) onCommit(h);
  };
  return (
    <input className="cell" inputMode="decimal" aria-label={label} value={shown}
      onChange={(e) => setDraft(e.target.value)} onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") setDraft(null);
      }} />
  );
}

// --- Entries ----------------------------------------------------------------------------

function Entries({ week }: { week: Week }) {
  const { current: ws } = useWorkspace();
  const entries = useQuery({
    queryKey: ["entries", ws.id, week.week_start],
    queryFn: () =>
      api<TimeEntry[]>(
        `/time-entries?workspace_id=${ws.id}&from=${week.days[0]}&to=${week.days[6]}`,
      ),
  });
  const [editing, setEditing] = useState<TimeEntry | "new" | null>(null);

  const byDay = new Map<string, TimeEntry[]>();
  for (const e of entries.data ?? []) {
    const d = zoned(e.start_at, ws.timezone).date;
    byDay.set(d, [...(byDay.get(d) ?? []), e]);
  }
  const days = [...byDay.keys()].sort().reverse();

  return (
    <div className="entries">
      <div className="page-head">
        <h2>Entries</h2>
        <button className="ghost" onClick={() => setEditing("new")}>Add entry</button>
      </div>
      {entries.data?.length === 0 && <Empty>Nothing logged this week.</Empty>}
      {days.map((d) => {
        const list = byDay.get(d)!.sort((a, b) => a.start_at.localeCompare(b.start_at));
        const total = list.reduce((s, e) => s + e.duration_seconds, 0);
        return (
          <div key={d} className="day-group">
            <h3 className="group-title">
              {dayLabel(d, { weekday: "long", month: "short", day: "numeric" })}
              <span className="muted"> {hm(total)}</span>
            </h3>
            <div className="card flush">
              {list.map((e) => (
                <button key={e.id} className="entry" onClick={() => setEditing(e)}
                  disabled={e.running}>
                  <span className="entry-time muted">
                    {zoned(e.start_at, ws.timezone).time}–
                    {e.end_at ? zoned(e.end_at, ws.timezone).time : "now"}
                  </span>
                  <span className="entry-main">
                    <strong>{e.project_name ?? "No project"}</strong>
                    {e.client_name && <span className="muted"> · {e.client_name}</span>}
                    {e.activity_name && <span className="tag">{e.activity_name}</span>}
                    {e.scope_title && <span className="tag">{e.scope_title}</span>}
                    {e.invoice_id && <span className="tag" title="On an invoice">INVOICED</span>}
                    {e.notes && <span className="entry-notes muted">{e.notes}</span>}
                  </span>
                  <span className="entry-amount">
                    <span>{e.running ? "running" : hm(e.duration_seconds)}</span>
                    <span className="muted small">
                      {e.billable ? money(e.amount, ws.currency) : "not billable"}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          </div>
        );
      })}
      <Dialog open={editing !== null} title={editing === "new" ? "Add entry" : "Edit entry"}
        onClose={() => setEditing(null)}>
        {editing !== null && (
          <EntryForm entry={editing === "new" ? null : editing}
            defaultDate={todayIn(ws.timezone) >= week.days[0] && todayIn(ws.timezone) <= week.days[6]
              ? todayIn(ws.timezone) : week.days[0]}
            onDone={() => setEditing(null)} />
        )}
      </Dialog>
    </div>
  );
}

export function EntryForm({
  entry,
  defaultDate,
  onDone,
}: {
  entry: TimeEntry | null;
  defaultDate: string;
  onDone: () => void;
}) {
  const { current: ws } = useWorkspace();
  const activities = useActivities();
  const invalidate = useTimeInvalidation();
  const start = entry ? zoned(entry.start_at, ws.timezone) : { date: defaultDate, time: "09:00" };
  const end = entry?.end_at ? zoned(entry.end_at, ws.timezone) : { date: defaultDate, time: "10:00" };
  const [times, setTimes] = useState<DTRange>({
    start: `${start.date}T${start.time}`,
    end: `${end.date}T${end.time}`,
  });
  const [f, setF] = useState({
    project_id: entry?.project_id ?? "",
    activity_id: entry?.activity_id ?? "",
    scope_id: entry?.scope_id ?? "",
    notes: entry?.notes ?? "",
    billable: entry?.billable ?? true,
  });
  const [warning, setWarning] = useState<string | null>(null);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) =>
    setF((cur) => ({ ...cur, [k]: e.target.value }));

  // Naive local times; the API reads them in the workspace time zone.
  const body = () => ({
    start_at: `${times.start}:00`,
    end_at: `${times.end}:00`,
    project_id: f.project_id || null,
    activity_id: f.activity_id || null,
    scope_id: f.scope_id || null,
    notes: f.notes.trim() || null,
    billable: f.billable,
  });
  const save = useMutation({
    mutationFn: () =>
      entry
        ? patch<TimeEntry>(`/time-entries/${entry.id}`, body())
        : post<TimeEntry>("/time-entries", { ...body(), workspace_id: ws.id }),
    onSuccess: async (saved) => {
      await invalidate();
      if (saved.overlaps?.length) {
        setWarning(`Saved, but it overlaps ${saved.overlaps.length} other entr${saved.overlaps.length === 1 ? "y" : "ies"}.`);
      } else onDone();
    },
  });
  const remove = useMutation({
    mutationFn: () => del(`/time-entries/${entry!.id}`),
    onSuccess: async () => {
      await invalidate();
      onDone();
    },
  });

  if (warning)
    return (
      <div>
        <p className="notice">{warning}</p>
        <div className="dialog-actions">
          <span className="spacer" />
          <button onClick={onDone}>OK</button>
        </div>
      </div>
    );

  return (
    <form onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
      {entry?.invoice_id && <p className="notice">This entry is on an invoice and can't change.</p>}
      <DateTimeRange value={times} onChange={setTimes} idPrefix="entry" />
      <div className="grid-2">
        <label>
          Project
          <ProjectSelect value={f.project_id}
            onChange={(id) => setF((c) => ({ ...c, project_id: id, scope_id: "" }))} includeDone />
        </label>
        <label>
          Activity
          <Select aria-label="Activity" value={f.activity_id} onChange={(e) => {
            const a = activities.data?.find((x) => x.id === e.target.value);
            setF((c) => ({ ...c, activity_id: e.target.value, billable: a ? a.billable : c.billable }));
          }}>
            <option value="">None</option>
            {activities.data?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </Select>
        </label>
      </div>
      <ScopeSelect projectId={f.project_id} value={f.scope_id}
        onChange={(id) => setF((c) => ({ ...c, scope_id: id }))} />
      <label>
        Notes
        <textarea rows={2} value={f.notes} onChange={set("notes")} />
      </label>
      <label className="check">
        <input type="checkbox" checked={f.billable}
          onChange={(e) => setF((c) => ({ ...c, billable: e.target.checked }))} />
        <span>Billable</span>
      </label>
      {entry && entry.rate && (
        <p className="muted small">
          Priced at {money(entry.rate, ws.currency, true)} ({entry.rate_source} rate) when saved.
          Changing the project or activity re-prices it.
        </p>
      )}
      {(save.isError || remove.isError) && (
        <p className="error">{errorMessage(save.error ?? remove.error)}</p>
      )}
      <div className="dialog-actions">
        {entry && (
          <button type="button" className="danger" disabled={remove.isPending || !!entry.invoice_id}
            onClick={() => confirm("Delete this entry?") && remove.mutate()}>
            Delete
          </button>
        )}
        <span className="spacer" />
        <button type="button" className="ghost" onClick={onDone}>Cancel</button>
        <button type="submit" disabled={save.isPending || !!entry?.invoice_id || minutesBetween(times.start, times.end) <= 0}>
          {entry ? "Save" : "Add entry"}
        </button>
      </div>
    </form>
  );
}
