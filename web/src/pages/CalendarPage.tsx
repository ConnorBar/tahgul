import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import FullCalendar from "@fullcalendar/react";
import type { DateSelectArg, EventClickArg, EventDropArg, EventInput } from "@fullcalendar/core";
import dayGridPlugin from "@fullcalendar/daygrid";
import interactionPlugin, { type EventResizeDoneArg } from "@fullcalendar/interaction";
import luxonPlugin from "@fullcalendar/luxon3";
import timeGridPlugin from "@fullcalendar/timegrid";
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
import { ProjectSelect, useTimeInvalidation } from "../timer";
import { Dialog, hm, money } from "../ui";
import { useWorkspace } from "../workspace";
import { EntryForm } from "./Time";

type Range = { from: string; to: string };

/** Day/week calendar: events from your calendars next to logged time (SPEC: calendar). */
export default function CalendarPage() {
  const { current: ws } = useWorkspace();
  const qc = useQueryClient();
  const invalidateTime = useTimeInvalidation();
  const cal = useRef<FullCalendar>(null);
  const [range, setRange] = useState<Range | null>(null);
  const [openEvent, setOpenEvent] = useState<CalEvent | null>(null);
  const [openEntry, setOpenEntry] = useState<TimeEntry | null>(null);
  const [selection, setSelection] = useState<{ start: Date; end: Date; allDay: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const calendars = useQuery({ queryKey: ["calendars"], queryFn: () => api<CalendarInfo[]>("/calendars") });
  const events = useQuery({
    queryKey: ["events", ws.id, range],
    queryFn: () => api<CalEvent[]>(`/events?workspace_id=${ws.id}&from=${range!.from}&to=${range!.to}`),
    enabled: !!range,
  });
  const entries = useQuery({
    queryKey: ["entries", ws.id, "cal", range],
    queryFn: () => api<TimeEntry[]>(`/time-entries?workspace_id=${ws.id}&from=${range!.from}&to=${range!.to}`),
    enabled: !!range,
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

  const input: EventInput[] = useMemo(() => {
    const out: EventInput[] = [];
    for (const e of events.data ?? []) {
      // Mirrors of time entries are already shown as time entries.
      if (e.origin === "time_mirror") continue;
      out.push({
        id: `ev:${e.id}:${e.start_at}`,
        title: e.title + (e.project_name ? ` · ${e.project_name}` : ""),
        start: e.start_at,
        end: e.end_at,
        allDay: e.all_day,
        backgroundColor: e.colour ?? "#6b6f76",
        borderColor: e.colour ?? "#6b6f76",
        editable: e.writable && !e.all_day,
        classNames: [e.time_entry_id ? "fc-logged" : "", e.sync_pending ? "fc-pending" : ""],
        extendedProps: { kind: "event", data: e },
      });
    }
    for (const t of entries.data ?? []) {
      if (t.running) continue;
      out.push({
        id: `te:${t.id}`,
        title: `${t.project_name ?? "No project"}${t.notes ? ` – ${t.notes}` : ""}`,
        start: t.start_at,
        end: t.end_at ?? undefined,
        editable: !t.invoice_id,
        classNames: ["fc-entry", t.billable ? "" : "fc-nonbillable"],
        extendedProps: { kind: "entry", data: t },
      });
    }
    return out;
  }, [events.data, entries.data]);

  const writable = calendars.data?.filter((c) => c.writable && c.enabled) ?? [];
  const noCalendars = calendars.data && calendars.data.length === 0;

  const onDrop = (arg: EventDropArg | EventResizeDoneArg) => {
    const { kind, data } = arg.event.extendedProps as { kind: string; data: { id: string } };
    const v = { id: data.id, start: arg.event.start!.toISOString(), end: arg.event.end!.toISOString() };
    (kind === "event" ? moveEvent : moveEntry).mutate(v);
  };

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

      <div className="card flush fc-wrap">
        <FullCalendar
          ref={cal}
          plugins={[timeGridPlugin, dayGridPlugin, interactionPlugin, luxonPlugin]}
          timeZone={ws.timezone}
          initialView={window.innerWidth < 700 ? "timeGridDay" : "timeGridWeek"}
          headerToolbar={{ left: "prev,next today", center: "title", right: "timeGridDay,timeGridWeek,dayGridMonth" }}
          buttonText={{ today: "Today", day: "Day", week: "Week", month: "Month" }}
          firstDay={1}
          nowIndicator
          selectable
          selectMirror
          editable
          slotMinTime="06:00:00"
          slotMaxTime="22:00:00"
          scrollTime="08:00:00"
          height="auto"
          events={input}
          datesSet={(arg) => {
            const from = arg.startStr.slice(0, 10);
            const end = new Date(arg.end.getTime() - 1);
            const to = new Intl.DateTimeFormat("en-CA", { timeZone: ws.timezone }).format(end);
            if (!range || range.from !== from || range.to !== to) setRange({ from, to });
          }}
          select={(arg: DateSelectArg) => setSelection({ start: arg.start, end: arg.end, allDay: arg.allDay })}
          eventClick={(arg: EventClickArg) => {
            const { kind, data } = arg.event.extendedProps as { kind: string; data: CalEvent | TimeEntry };
            if (kind === "event") setOpenEvent(data as CalEvent);
            else setOpenEntry(data as TimeEntry);
          }}
          eventDrop={onDrop}
          eventResize={onDrop}
        />
      </div>

      <Dialog open={openEvent !== null} title="Event" onClose={() => setOpenEvent(null)}>
        {openEvent && <EventDetail ev={openEvent} onDone={() => { setOpenEvent(null); refresh(); }} />}
      </Dialog>
      <Dialog open={openEntry !== null} title="Edit entry" onClose={() => setOpenEntry(null)}>
        {openEntry && <EntryForm entry={openEntry} defaultDate={openEntry.start_at.slice(0, 10)} onDone={() => { setOpenEntry(null); refresh(); }} />}
      </Dialog>
      <Dialog open={selection !== null} title="New" onClose={() => setSelection(null)}>
        {selection && (
          <NewFromSelection sel={selection} calendars={writable}
            onDone={() => { cal.current?.getApi().unselect(); setSelection(null); refresh(); }} />
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
  const link = useMutation({
    mutationFn: (pid: string) => patch(`/events/${ev.id}`, { project_id: pid || null }),
  });
  const log = useMutation({ mutationFn: () => post<{ entry: TimeEntry }>(`/events/${ev.id}/log-time`) });
  const remove = useMutation({ mutationFn: () => del(`/events/${ev.id}`), onSuccess: onDone });
  const minutes = (new Date(ev.end_at).getTime() - new Date(ev.start_at).getTime()) / 60000;
  return (
    <div className="event-detail">
      <h3>{ev.title || "(no title)"}</h3>
      <p className="muted">
        {fmtRange(ev, ws.timezone)} · <span style={{ color: ev.colour ?? undefined }}>●</span> {ev.calendar_name}
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
      {(link.isError || log.isError || remove.isError) && (
        <p className="error">{errorMessage(link.error ?? log.error ?? remove.error)}</p>
      )}
      <div className="dialog-actions">
        {ev.writable && (
          <button className="danger" disabled={remove.isPending}
            onClick={() => confirm(`Delete “${ev.title}” from ${ev.calendar_name}? It goes from your phone too.`) && remove.mutate()}>
            Delete event
          </button>
        )}
        <span className="spacer" />
        <button className="ghost" onClick={onDone}>Done</button>
      </div>
    </div>
  );
}

function NewFromSelection({ sel, calendars, onDone }: {
  sel: { start: Date; end: Date; allDay: boolean };
  calendars: CalendarInfo[];
  onDone: () => void;
}) {
  const { current: ws } = useWorkspace();
  const [kind, setKind] = useState<"entry" | "event">(sel.allDay || !calendars.length ? (calendars.length ? "event" : "entry") : "entry");
  const [title, setTitle] = useState("");
  const [project, setProject] = useState("");
  const [calendarId, setCalendarId] = useState(calendars.find((c) => c.is_default)?.id ?? calendars[0]?.id ?? "");
  const save = useMutation({
    mutationFn: () =>
      kind === "event"
        ? post("/events", {
            calendar_id: calendarId || null, title, project_id: project || null,
            start_at: sel.start.toISOString(), end_at: sel.end.toISOString(), all_day: sel.allDay,
          })
        : post("/time-entries", {
            workspace_id: ws.id, project_id: project || null, notes: title || null,
            start_at: sel.start.toISOString(), end_at: sel.end.toISOString(),
          }),
    onSuccess: onDone,
  });
  const minutes = (sel.end.getTime() - sel.start.getTime()) / 60000;
  return (
    <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <p className="muted">{fmtRange({ start_at: sel.start.toISOString(), end_at: sel.end.toISOString(), all_day: sel.allDay }, ws.timezone)}{!sel.allDay && ` (${hm(minutes * 60)})`}</p>
      <div className="tabs compact seg" role="radiogroup" aria-label="What to add">
        {!sel.allDay && (
          <a href="#entry" role="radio" aria-checked={kind === "entry"} className={kind === "entry" ? "active" : undefined}
            onClick={(e) => { e.preventDefault(); setKind("entry"); }}>Log time</a>
        )}
        {calendars.length > 0 && (
          <a href="#event" role="radio" aria-checked={kind === "event"} className={kind === "event" ? "active" : undefined}
            onClick={(e) => { e.preventDefault(); setKind("event"); }}>Calendar event</a>
        )}
      </div>
      <label>
        {kind === "event" ? "Title" : "Notes"}
        <input value={title} onChange={(e) => setTitle(e.target.value)} required={kind === "event"} autoFocus />
      </label>
      <label>
        Project
        <ProjectSelect value={project} onChange={setProject} />
      </label>
      {kind === "event" && calendars.length > 1 && (
        <label>
          Calendar
          <select aria-label="Calendar" value={calendarId} onChange={(e) => setCalendarId(e.target.value)}>
            {calendars.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
      )}
      {save.isError && <p className="error">{errorMessage(save.error)}</p>}
      <div className="dialog-actions">
        <span className="spacer" />
        <button type="button" className="ghost" onClick={onDone}>Cancel</button>
        <button type="submit" disabled={save.isPending}>{kind === "event" ? "Add event" : "Log time"}</button>
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
