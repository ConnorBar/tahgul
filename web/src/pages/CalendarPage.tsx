import { useCallback, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import FullCalendar from "@fullcalendar/react";
import type { EventClickArg, EventInput } from "@fullcalendar/core";
import dayGridPlugin from "@fullcalendar/daygrid";
import interactionPlugin, { type DateClickArg } from "@fullcalendar/interaction";
import luxonPlugin from "@fullcalendar/luxon3";
import {
  api,
  del,
  errorMessage,
  patch,
  post,
  type CalEvent,
  type CalendarInfo,
  type TimeEntry,
} from "../api";
import { Strip, type LocalRange, type StripHandle, type StripItem } from "../calendar/Strip";
import { ProjectSelect, useTimeInvalidation } from "../timer";
import { Dialog, hm, money, zoned } from "../ui";
import { useWorkspace } from "../workspace";
import { DateTimeRange, minutesBetween, type DTRange } from "../calendar/DateTimeRange";
import { EntryForm } from "./Time";
import { Select } from "../Select";

type Range = { from: string; to: string };
type Mode = "1" | "3" | "7" | "month";
const MODES: [Mode, string][] = [["1", "Day"], ["3", "3 days"], ["7", "Week"], ["month", "Month"]];

function savedMode(): Mode {
  try {
    const m = localStorage.getItem("tahgul.calendar.mode") as Mode | null;
    if (m && MODES.some(([k]) => k === m)) return m;
  } catch {
    /* private mode */
  }
  return window.innerWidth < 700 ? "1" : "7";
}

type Item = { kind: "event"; ev: CalEvent } | { kind: "entry"; t: TimeEntry };

/** Day/week calendar: events from your calendars next to logged time (SPEC: calendar). */
export default function CalendarPage() {
  const { current: ws } = useWorkspace();
  const qc = useQueryClient();
  const invalidateTime = useTimeInvalidation();
  const strip = useRef<StripHandle>(null);
  const month = useRef<FullCalendar>(null);
  const [mode, setModeState] = useState<Mode>(savedMode);
  const [range, setRange] = useState<Range | null>(null);
  const [visibleDay, setVisibleDay] = useState<string | null>(null);
  const [openEvent, setOpenEvent] = useState<CalEvent | null>(null);
  const [openEntry, setOpenEntry] = useState<TimeEntry | null>(null);
  const [selection, setSelection] = useState<LocalRange | null>(null);
  const [error, setError] = useState<string | null>(null);
  const setMode = (m: Mode) => {
    setModeState(m);
    try { localStorage.setItem("tahgul.calendar.mode", m); } catch { /* ignore */ }
  };

  const calendars = useQuery({ queryKey: ["calendars"], queryFn: () => api<CalendarInfo[]>("/calendars") });
  const events = useQuery({
    queryKey: ["events", ws.id, range],
    queryFn: () => api<CalEvent[]>(`/events?workspace_id=${ws.id}&from=${range!.from}&to=${range!.to}`),
    enabled: !!range,
    placeholderData: (prev) => prev,
  });
  const entries = useQuery({
    queryKey: ["entries", ws.id, "cal", range],
    queryFn: () => api<TimeEntry[]>(`/time-entries?workspace_id=${ws.id}&from=${range!.from}&to=${range!.to}&limit=2000`),
    enabled: !!range,
    placeholderData: (prev) => prev,
  });
  const sync = useMutation({
    mutationFn: () => post("/calendars/sync"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["events"] }),
    onError: (e) => setError(errorMessage(e)),
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["events"] });
    invalidateTime();
  };
  const moveEvent = useMutation({
    mutationFn: (v: { id: string; start: string; end: string }) =>
      patch(`/events/${v.id}`, { start_at: v.start, end_at: v.end }),
    onError: (e) => setError(errorMessage(e)),
    onSettled: refresh,
  });
  const moveEntry = useMutation({
    mutationFn: (v: { id: string; start: string; end: string }) =>
      patch(`/time-entries/${v.id}`, { start_at: v.start, end_at: v.end }),
    onError: (e) => setError(errorMessage(e)),
    onSettled: refresh,
  });

  const items: StripItem[] = useMemo(() => {
    const out: StripItem[] = [];
    for (const e of events.data ?? []) {
      // Mirrors of time entries are already shown as time entries.
      if (e.origin === "time_mirror") continue;
      out.push({
        key: `ev:${e.id}:${e.start_at}`,
        start: e.start_at,
        end: e.end_at,
        allDay: e.all_day,
        title: e.title || "(no title)",
        sub: [e.project_name, e.calendar_name].filter(Boolean).join(" · "),
        colour: e.colour ?? "#6b6f76",
        className: ["event", e.time_entry_id ? "logged" : "", e.sync_pending ? "pending" : ""].join(" "),
        editable: e.writable && !e.all_day && !e.recurring,
        data: { kind: "event", ev: e } satisfies Item,
      });
    }
    const nowIso = new Date().toISOString();
    for (const t of entries.data ?? []) {
      const end = t.end_at ?? nowIso;
      out.push({
        key: `te:${t.id}`,
        start: t.start_at,
        end,
        allDay: false,
        title: t.project_name ?? "No project",
        sub: [t.running ? "running" : hm(t.duration_seconds), t.notes].filter(Boolean).join(" · "),
        className: ["entry", t.billable ? "" : "nonbillable", t.running ? "running" : ""].join(" "),
        editable: !t.invoice_id && !t.running,
        data: { kind: "entry", t } satisfies Item,
      });
    }
    return out;
  }, [events.data, entries.data]);

  const monthInput: EventInput[] = useMemo(
    () => items.map((i) => ({
      id: i.key, title: i.title, start: i.start, end: i.end, allDay: i.allDay,
      backgroundColor: i.colour ?? undefined, borderColor: i.colour ?? undefined,
      classNames: [(i.data as Item).kind === "entry" ? "fc-entry" : ""], extendedProps: { item: i },
    })),
    [items],
  );

  const writable = calendars.data?.filter((c) => c.writable && c.enabled) ?? [];
  const noCalendars = calendars.data && calendars.data.length === 0;

  const open = (i: StripItem) => {
    const d = i.data as Item;
    if (d.kind === "event") setOpenEvent(d.ev);
    else if (!d.t.running) setOpenEntry(d.t);
  };
  const onMove = (i: StripItem, start: string, end: string) => {
    const d = i.data as Item;
    if (d.kind === "event") moveEvent.mutate({ id: d.ev.id, start, end });
    else moveEntry.mutate({ id: d.t.id, start, end });
  };
  const onRange = useCallback((from: string, to: string) => {
    setRange((r) => (r && r.from === from && r.to === to ? r : { from, to }));
  }, []);

  const title = visibleDay
    ? new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(`${visibleDay}T12:00:00Z`))
    : "";

  return (
    <section className="page full calendar-page">
      <div className="page-head">
        <h1>Calendar</h1>
        <div className="row">
          <span className="legend"><span className="sw entry" /> Logged time</span>
          <span className="legend"><span className="sw event" /> Calendar events</span>
          <button className="ghost" onClick={() => sync.mutate()} disabled={sync.isPending}>
            {sync.isPending ? "Syncing…" : "Sync now"}
          </button>
        </div>
      </div>
      {noCalendars && (
        <div className="notice">
          No calendars connected yet. <Link to="/settings/calendars">Connect one</Link> to see
          your iPhone's events here and add events from Tahgul.
        </div>
      )}
      {error && <p className="error">{error}</p>}
      <PendingLog />

      <div className="card flush cal-card">
        <div className="cal-toolbar">
          <button className="ghost" onClick={() => (mode === "month" ? month.current?.getApi().today() : strip.current?.today())}>Today</button>
          <button className="ghost icon-btn" aria-label="Earlier"
            onClick={() => (mode === "month" ? month.current?.getApi().prev() : strip.current?.page(-1))}>‹</button>
          <button className="ghost icon-btn" aria-label="Later"
            onClick={() => (mode === "month" ? month.current?.getApi().next() : strip.current?.page(1))}>›</button>
          {mode !== "month" && <h2>{title}</h2>}
          <span className="spacer" />
          <div className="seg-control" role="radiogroup" aria-label="Show">
            {MODES.map(([m, label]) => (
              <button key={m} type="button" role="radio" aria-checked={mode === m} className={mode === m ? "on" : undefined}
                onClick={() => setMode(m)}>{label}</button>
            ))}
          </div>
        </div>

        {mode === "month" ? (
          <div className="fc-wrap">
            <FullCalendar
              ref={month}
              plugins={[dayGridPlugin, interactionPlugin, luxonPlugin]}
              timeZone={ws.timezone}
              initialView="dayGridMonth"
              headerToolbar={{ left: "", center: "title", right: "" }}
              firstDay={1}
              height="auto"
              events={monthInput}
              dayMaxEvents={4}
              datesSet={(arg) => {
                const from = arg.startStr.slice(0, 10);
                const end = new Date(arg.end.getTime() - 1);
                const to = new Intl.DateTimeFormat("en-CA", { timeZone: ws.timezone }).format(end);
                onRange(from, to);
              }}
              dateClick={(arg: DateClickArg) => {
                setMode("1");
                setTimeout(() => strip.current?.goTo(arg.dateStr.slice(0, 10)), 50);
              }}
              eventClick={(arg: EventClickArg) => open(arg.event.extendedProps.item as StripItem)}
            />
          </div>
        ) : (
          <Strip ref={strip} items={items} timeZone={ws.timezone} visibleDays={Number(mode)}
            onRange={onRange} onVisible={setVisibleDay} onSelect={setSelection} onOpen={open} onMove={onMove} />
        )}
      </div>

      <Dialog open={openEvent !== null} title="Event" onClose={() => setOpenEvent(null)}>
        {openEvent && <EventDetail ev={openEvent} onDone={() => { setOpenEvent(null); refresh(); }} />}
      </Dialog>
      <Dialog open={openEntry !== null} title="Edit entry" onClose={() => setOpenEntry(null)}>
        {openEntry && <EntryForm entry={openEntry} defaultDate={zoned(openEntry.start_at, ws.timezone).date} onDone={() => { setOpenEntry(null); refresh(); }} />}
      </Dialog>
      <Dialog open={selection !== null} title="New" onClose={() => setSelection(null)}>
        {selection && (
          <NewFromSelection sel={selection} calendars={writable} onDone={() => { setSelection(null); refresh(); }} />
        )}
      </Dialog>
    </section>
  );
}

function fmtRange(ev: { start_at: string; end_at: string; all_day: boolean }, tz: string) {
  const s = new Date(ev.start_at);
  const e = new Date(ev.end_at);
  if (ev.all_day) return s.toLocaleDateString([], { timeZone: tz, weekday: "short", month: "short", day: "numeric" });
  const day = s.toLocaleDateString([], { timeZone: tz, weekday: "short", month: "short", day: "numeric" });
  const t = (d: Date) => d.toLocaleTimeString([], { timeZone: tz, hour: "numeric", minute: "2-digit" });
  return `${day}, ${t(s)}–${t(e)}`;
}

function EventDetail({ ev, onDone }: { ev: CalEvent; onDone: () => void }) {
  const { current: ws } = useWorkspace();
  const [project, setProject] = useState(ev.project_id ?? "");
  const editable = ev.writable && !ev.recurring;
  const local = (iso: string) => {
    const z = zoned(iso, ws.timezone);
    return `${z.date}T${z.time}`;
  };
  const [title, setTitle] = useState(ev.title);
  const [times, setTimes] = useState<DTRange>(() =>
    ev.all_day
      ? { start: `${ev.start_at.slice(0, 10)}T00:00`, end: `${ev.end_at.slice(0, 10)}T00:00` }
      : { start: local(ev.start_at), end: local(ev.end_at) });
  const changed = title !== ev.title || (!ev.all_day && (times.start !== local(ev.start_at) || times.end !== local(ev.end_at)));
  const save = useMutation({
    mutationFn: () => patch(`/events/${ev.id}`, ev.all_day
      ? { title }
      : { title, start_at: `${times.start}:00`, end_at: `${times.end}:00` }),
    onSuccess: onDone,
  });
  const link = useMutation({
    mutationFn: (pid: string) => patch(`/events/${ev.id}`, { project_id: pid || null }),
  });
  const log = useMutation({ mutationFn: () => post<{ entry: TimeEntry }>(`/events/${ev.id}/log-time`) });
  const remove = useMutation({ mutationFn: () => del(`/events/${ev.id}`), onSuccess: onDone });
  const minutes = (new Date(ev.end_at).getTime() - new Date(ev.start_at).getTime()) / 60000;
  return (
    <div className="event-detail">
      {editable ? (
        <label>
          Title
          <input value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
      ) : <h3>{ev.title || "(no title)"}</h3>}
      {editable && !ev.all_day && <DateTimeRange value={times} onChange={setTimes} idPrefix="ev" />}
      <p className="muted">
        {(!editable || ev.all_day) && <>{fmtRange(ev, ws.timezone)} · </>}<span style={{ color: ev.colour ?? undefined }}>●</span> {ev.calendar_name}
        {ev.recurring && " · repeats"}
        {!ev.writable && " · read-only here"}
      </p>
      {ev.location && <p>📍 {ev.location}</p>}
      {ev.description && <p className="pre">{ev.description}</p>}
      <label>
        Project
        <ProjectSelect value={project} onChange={(id) => { setProject(id); link.mutate(id); }} />
      </label>
      {log.data ? (
        <p className="ok">
          Logged {hm(log.data.entry.duration_seconds)}
          {log.data.entry.amount && ` (${money(log.data.entry.amount, ws.currency)})`}.
        </p>
      ) : ev.time_entry_id ? (
        <p className="muted">Already logged as time.</p>
      ) : !ev.all_day && (
        <button onClick={() => log.mutate()} disabled={log.isPending}>
          Log {hm(minutes * 60)} as time{project ? "" : " (no project)"}
        </button>
      )}
      {(link.isError || log.isError || remove.isError || save.isError) && (
        <p className="error">{errorMessage(link.error ?? log.error ?? remove.error ?? save.error)}</p>
      )}
      <div className="dialog-actions">
        {ev.writable && (
          <button className="danger" disabled={remove.isPending}
            onClick={() => confirm(`Delete “${ev.title}” from ${ev.calendar_name}? It goes from your phone too.`) && remove.mutate()}>
            Delete event
          </button>
        )}
        <span className="spacer" />
        {changed ? (
          <>
            <button className="ghost" onClick={onDone}>Cancel</button>
            <button onClick={() => save.mutate()} disabled={save.isPending || minutesBetween(times.start, times.end) <= 0}>Save</button>
          </>
        ) : (
          <button className="ghost" onClick={onDone}>Done</button>
        )}
      </div>
    </div>
  );
}

function NewFromSelection({ sel, calendars, onDone }: {
  sel: LocalRange;
  calendars: CalendarInfo[];
  onDone: () => void;
}) {
  const { current: ws } = useWorkspace();
  const [kind, setKind] = useState<"entry" | "event">(sel.allDay ? "event" : "entry");
  const [title, setTitle] = useState("");
  const [project, setProject] = useState("");
  const [times, setTimes] = useState<DTRange>({ start: sel.start, end: sel.end });
  const [calendarId, setCalendarId] = useState(calendars.find((c) => c.is_default)?.id ?? calendars[0]?.id ?? "");
  const save = useMutation({
    mutationFn: () =>
      kind === "event"
        ? post("/events", {
            calendar_id: calendarId || null, title, project_id: project || null,
            start_at: `${times.start}:00`, end_at: `${times.end}:00`, all_day: sel.allDay,
          })
        : post("/time-entries", {
            workspace_id: ws.id, project_id: project || null, notes: title || null,
            start_at: `${times.start}:00`, end_at: `${times.end}:00`,
          }),
    onSuccess: onDone,
  });
  return (
    <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <div className="seg-control wide" role="radiogroup" aria-label="What to add">
        <button type="button" role="radio" aria-checked={kind === "entry"} className={kind === "entry" ? "on" : undefined}
          onClick={() => setKind("entry")} disabled={sel.allDay}>Log time</button>
        <button type="button" role="radio" aria-checked={kind === "event"} className={kind === "event" ? "on" : undefined}
          onClick={() => setKind("event")} disabled={!calendars.length}>Calendar event</button>
      </div>
      {!calendars.length && <p className="muted small">Connect a calendar under Settings to add events.</p>}
      <label>
        {kind === "event" ? "Title" : "Notes"}
        <input value={title} onChange={(e) => setTitle(e.target.value)} required={kind === "event"} autoFocus
          placeholder={kind === "event" ? "Call with Laura" : "What you worked on"} />
      </label>
      <DateTimeRange value={times} onChange={setTimes} allDay={sel.allDay} idPrefix="new" />
      <label>
        Project
        <ProjectSelect value={project} onChange={setProject} />
      </label>
      {kind === "event" && calendars.length > 1 && (
        <label>
          Calendar
          <Select aria-label="Calendar" value={calendarId} onChange={(e) => setCalendarId(e.target.value)}>
            {calendars.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select>
        </label>
      )}
      {save.isError && <p className="error">{errorMessage(save.error)}</p>}
      <div className="dialog-actions">
        <span className="spacer" />
        <button type="button" className="ghost" onClick={onDone}>Cancel</button>
        <button type="submit" disabled={save.isPending || minutesBetween(times.start, times.end) <= 0}>
          {kind === "event" ? "Add event" : "Log time"}
        </button>
      </div>
    </form>
  );
}

/** "You were in X (1:30). Log it as time?" for ended meetings linked to a project. */
export function PendingLog() {
  const { current: ws } = useWorkspace();
  const qc = useQueryClient();
  const invalidateTime = useTimeInvalidation();
  const pending = useQuery({
    queryKey: ["events", "pending", ws.id],
    queryFn: () => api<CalEvent[]>(`/events/pending-log?workspace_id=${ws.id}`),
    refetchInterval: 5 * 60_000,
  });
  const done = () => {
    qc.invalidateQueries({ queryKey: ["events"] });
    invalidateTime();
  };
  const log = useMutation({ mutationFn: (id: string) => post(`/events/${id}/log-time`), onSuccess: done });
  const dismiss = useMutation({ mutationFn: (id: string) => post(`/events/${id}/dismiss-log`), onSuccess: done });
  if (!pending.data?.length) return null;
  return (
    <div className="pending-log">
      {pending.data.slice(0, 3).map((e) => (
        <div key={e.id} className="banner inline" role="status">
          <span>
            <strong>{e.title}</strong> ({hm((new Date(e.end_at).getTime() - new Date(e.start_at).getTime()) / 1000)},{" "}
            {e.project_name}) has ended. Log it as time?
          </span>
          <div className="row">
            <button onClick={() => log.mutate(e.id)} disabled={log.isPending}>Log it</button>
            <button className="ghost" onClick={() => dismiss.mutate(e.id)}>No</button>
          </div>
        </div>
      ))}
    </div>
  );
}
