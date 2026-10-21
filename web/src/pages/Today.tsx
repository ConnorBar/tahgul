import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  api,
  type CalEvent,
  type Idea,
  type InvoiceTotals,
  type Me,
  type Summary,
  type Task,
  type Unbilled,
} from "../api";
import { DueLabel, PriorityBadge } from "../tasks/common";
import { TaskDrawer } from "../tasks/TaskDrawer";
import { dayLabel, hoursLabel, money, todayIn, zoned } from "../ui";
import { useWorkspace } from "../workspace";
import { PendingLog } from "./CalendarPage";

/** The home screen: what's due, what's on the calendar, and how the week is going. */
export default function Today({ me }: { me: Me }) {
  const { current: ws } = useWorkspace();
  const today = todayIn(ws.timezone);
  const [openTask, setOpenTask] = useState<string | null>(null);

  const day = useQuery({
    queryKey: ["summary", ws.id, "today", today],
    queryFn: () => api<Summary>(`/reports/summary?workspace_id=${ws.id}&from=${today}&to=${today}`),
  });
  const week = useQuery({
    queryKey: ["summary", ws.id, "week"],
    queryFn: () => api<Summary>(`/reports/summary?workspace_id=${ws.id}`),
  });
  const due = useQuery({
    queryKey: ["tasks", "today", ws.id, today],
    queryFn: () =>
      api<Task[]>(`/tasks?workspace_id=${ws.id}&open=true&due_before=${today}&sort=due&top_level=true`),
  });
  const events = useQuery({
    queryKey: ["events", "today", ws.id, today],
    queryFn: () => api<CalEvent[]>(`/events?workspace_id=${ws.id}&from=${today}&to=${today}`),
    retry: false,
  });
  const unbilled = useQuery({
    queryKey: ["unbilled", ws.id],
    queryFn: () => api<Unbilled[]>(`/invoices/unbilled?workspace_id=${ws.id}`),
    enabled: me.kind === "owner" || me.scopes.includes("invoice"),
  });
  const totals = useQuery({
    queryKey: ["invoice-totals", ws.id],
    queryFn: () => api<InvoiceTotals>(`/invoices/totals?workspace_id=${ws.id}`),
    enabled: me.kind === "owner" || me.scopes.includes("invoice"),
  });
  const ideas = useQuery({
    queryKey: ["ideas", ws.id],
    queryFn: () => api<Idea[]>(`/ideas?workspace_id=${ws.id}`),
  });

  const unbilledTotal = unbilled.data?.reduce((n, u) => n + Number(u.amount), 0);
  const overdueTasks = due.data?.filter((t) => t.overdue) ?? [];
  const dueToday = due.data?.filter((t) => !t.overdue) ?? [];

  return (
    <section className="page today">
      <div className="page-head">
        <div>
          <p className="muted small">{dayLabel(today, { weekday: "long", month: "long", day: "numeric" })}</p>
          <h1>Today</h1>
        </div>
      </div>

      {!me.totp_enabled && me.kind === "owner" && (
        <div className="notice">
          Two-factor sign-in is off. <Link to="/settings/security">Turn it on</Link> before
          putting real client data in.
        </div>
      )}
      {!ws.default_rate && (
        <div className="notice">
          No default hourly rate yet. <Link to="/settings/workspace">Set one</Link> so every
          project has a rate to fall back on.
        </div>
      )}

      <PendingLog />

      <div className="stats">
        <Link to="/time" className="stat">
          <span className="muted small">Today</span>
          <span className="stat-n">{day.data ? `${hoursLabel(day.data.totals.hours) || 0} h` : "…"}</span>
          <span className="muted small">{day.data && money(day.data.totals.amount, ws.currency)}</span>
        </Link>
        <Link to="/reports" className="stat">
          <span className="muted small">This week</span>
          <span className="stat-n">{week.data ? `${hoursLabel(week.data.totals.hours) || 0} h` : "…"}</span>
          <span className="muted small">{week.data && money(week.data.totals.amount, ws.currency)}</span>
        </Link>
        {unbilledTotal !== undefined && (
          <Link to="/invoices" className="stat">
            <span className="muted small">Ready to bill</span>
            <span className="stat-n">{money(String(unbilledTotal), ws.currency)}</span>
            <span className="muted small">
              {unbilled.data?.length ?? 0} client{unbilled.data?.length === 1 ? "" : "s"}
            </span>
          </Link>
        )}
        {totals.data && (
          <Link to="/invoices" className="stat">
            <span className="muted small">Outstanding</span>
            <span className="stat-n">{money(totals.data.outstanding, ws.currency)}</span>
            <span className={`small${Number(totals.data.overdue) > 0 ? " danger-text" : " muted"}`}>
              {Number(totals.data.overdue) > 0 ? `${money(totals.data.overdue, ws.currency)} overdue` : "nothing overdue"}
            </span>
          </Link>
        )}
      </div>

      <div className="today-grid">
        <div className="card">
          <div className="row card-head">
            <h2>Due</h2>
            <span className="spacer" />
            <Link to="/tasks" className="small">All tasks</Link>
          </div>
          {due.data && !due.data.length && <p className="muted">Nothing due today. Nice.</p>}
          {[...overdueTasks, ...dueToday].length > 0 && (
            <ul className="mini-list">
              {[...overdueTasks, ...dueToday].slice(0, 10).map((t) => (
                <li key={t.id}>
                  <button className="linkish mini-task" onClick={() => setOpenTask(t.id)}>
                    <span className="dot inline-dot" style={{ background: t.colour }} />
                    <span className="mini-title">{t.title}</span>
                    <span className="mini-meta">
                      <PriorityBadge p={t.priority} />
                      <DueLabel task={t} timeZone={ws.timezone} />
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="card">
          <div className="row card-head">
            <h2>Calendar</h2>
            <span className="spacer" />
            <Link to="/calendar" className="small">Week</Link>
          </div>
          {events.isError && <p className="muted small">Calendars aren't connected. <Link to="/settings/calendars">Set them up</Link>.</p>}
          {events.data && !events.data.length && <p className="muted">Nothing on the calendar today.</p>}
          {events.data && events.data.length > 0 && (
            <ul className="mini-list">
              {events.data.map((e) => (
                <li key={e.id} className="mini-event">
                  <span className="mini-time muted">
                    {e.all_day ? "All day" : `${zoned(e.start_at, ws.timezone).time}–${zoned(e.end_at, ws.timezone).time}`}
                  </span>
                  <span className="dot inline-dot" style={{ background: e.colour ?? "var(--muted)" }} />
                  <span className="mini-title">{e.title}</span>
                  {e.project_name && <span className="muted small">{e.project_name}</span>}
                </li>
              ))}
            </ul>
          )}
        </div>

        {ideas.data && ideas.data.length > 0 && (
          <div className="card">
            <div className="row card-head">
              <h2>Ideas</h2>
              <span className="spacer" />
              <Link to="/ideas" className="small">Inbox</Link>
            </div>
            <ul className="mini-list">
              {ideas.data.slice(0, 4).map((i) => (
                <li key={i.id}>
                  <Link to="/ideas" className="mini-task">
                    <span className="mini-title">{i.name}</span>
                    {i.voice_note_count > 0 && <span className="muted small">🎙 {i.voice_note_count}</span>}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <TaskDrawer taskId={openTask} onClose={() => setOpenTask(null)} onOpen={setOpenTask} />
    </section>
  );
}
