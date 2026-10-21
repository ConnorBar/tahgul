import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { ApiError, api, post, type Me } from "./api";
import CalendarPage from "./pages/CalendarPage";
import Clients from "./pages/Clients";
import Ideas from "./pages/Ideas";
import Invoices, { InvoicePage } from "./pages/Invoices";
import Login from "./pages/Login";
import ProjectPage from "./pages/ProjectPage";
import Projects from "./pages/Projects";
import Reports from "./pages/Reports";
import Settings from "./pages/Settings";
import Tasks from "./pages/Tasks";
import Time from "./pages/Time";
import Today from "./pages/Today";
import { LongTimerPrompt, TimerBar } from "./timer";
import { WorkspaceProvider, useWorkspace } from "./workspace";

export default function App() {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/auth/me") });

  if (me.isPending) return <div className="splash" aria-busy="true" />;
  if (me.error instanceof ApiError && me.error.status === 401) return <Login />;
  if (me.isError)
    return (
      <div className="splash">
        <p>Can't reach Tahgul. Is Tailscale connected?</p>
        <button onClick={() => me.refetch()}>Try again</button>
      </div>
    );

  return (
    <WorkspaceProvider>
      <Shell me={me.data} />
    </WorkspaceProvider>
  );
}

function WorkspaceSwitcher() {
  const { workspaces, current, setCurrent } = useWorkspace();
  if (workspaces.length < 2) return <span className="ws-name">{current.name}</span>;
  return (
    <select className="ws-switch" aria-label="Workspace" value={current.id}
      onChange={(e) => setCurrent(e.target.value)}>
      {workspaces.map((w) => (
        <option key={w.id} value={w.id}>{w.name}</option>
      ))}
    </select>
  );
}

function Shell({ me }: { me: Me }) {
  const qc = useQueryClient();
  const logout = useMutation({
    mutationFn: () => post("/auth/logout"),
    onSettled: () => qc.resetQueries(),
  });

  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">Tahgul</span>
        <WorkspaceSwitcher />
        <div className="timer-slot">
          <TimerBar />
        </div>
        <nav>
          <NavLink to="/" end>Today</NavLink>
          <NavLink to="/tasks">Tasks</NavLink>
          <NavLink to="/time">Time</NavLink>
          <NavLink to="/calendar">Calendar</NavLink>
          <NavLink to="/projects">Projects</NavLink>
          <NavLink to="/ideas">Ideas</NavLink>
          <NavLink to="/clients">Clients</NavLink>
          <NavLink to="/invoices">Invoices</NavLink>
          <NavLink to="/reports">Reports</NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
        <button className="ghost signout" onClick={() => logout.mutate()} disabled={logout.isPending}>
          Sign out
        </button>
      </header>
      <TabBar onSignOut={() => logout.mutate()} />
      <LongTimerPrompt />
      <main>
        <Routes>
          <Route path="/" element={<Today me={me} />} />
          <Route path="/time" element={<Time />} />
          <Route path="/calendar" element={<CalendarPage />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/projects/:id" element={<ProjectPage />} />
          <Route path="/ideas" element={<Ideas />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/invoices" element={<Invoices />} />
          <Route path="/invoices/:id" element={<InvoicePage />} />
          <Route path="/clients" element={<Clients />} />
          <Route path="/settings/*" element={<Settings me={me} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

const ICONS: Record<string, string> = {
  today: "M4 11.5 12 5l8 6.5V20h-5v-5H9v5H4z",
  tasks: "M5 5h14v14H5zM8.5 12l2.5 2.5L15.5 10",
  time: "M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16zM12 8v4.5l3 2",
  calendar: "M5 6h14v13H5zM5 10h14M9 4v4M15 4v4",
  more: "M6 12h.01M12 12h.01M18 12h.01",
};

function Icon({ name }: { name: string }) {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" fill="none"
      stroke="currentColor" strokeWidth={name === "more" ? 3.2 : 1.8} strokeLinecap="round" strokeLinejoin="round">
      <path d={ICONS[name]} />
    </svg>
  );
}

/** Phone navigation: the four daily screens, everything else under More. */
function TabBar({ onSignOut }: { onSignOut: () => void }) {
  const [more, setMore] = useState(false);
  const location = useLocation();
  useEffect(() => setMore(false), [location.pathname]);
  const tabs: [string, string, string][] = [
    ["/", "today", "Today"],
    ["/tasks", "tasks", "Tasks"],
    ["/time", "time", "Time"],
    ["/calendar", "calendar", "Calendar"],
  ];
  return (
    <>
      {more && <div className="sheet-backdrop" onClick={() => setMore(false)} />}
      {more && (
        <nav className="sheet" aria-label="More">
          {[
            ["/projects", "Projects"],
            ["/ideas", "Ideas"],
            ["/clients", "Clients"],
            ["/invoices", "Invoices"],
            ["/reports", "Reports"],
            ["/settings", "Settings"],
          ].map(([to, label]) => (
            <NavLink key={to} to={to}>{label}</NavLink>
          ))}
          <button className="linkish" onClick={onSignOut}>Sign out</button>
        </nav>
      )}
      <nav className="tabbar" aria-label="Main">
        {tabs.map(([to, icon, label]) => (
          <NavLink key={to} to={to} end={to === "/"}>
            <Icon name={icon} />
            <span>{label}</span>
          </NavLink>
        ))}
        <button type="button" className={more ? "active" : undefined} aria-expanded={more}
          onClick={() => setMore((m) => !m)}>
          <Icon name="more" />
          <span>More</span>
        </button>
      </nav>
    </>
  );
}
