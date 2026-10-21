import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { ApiError, api, post, type Me } from "./api";
import Clients from "./pages/Clients";
import Login from "./pages/Login";
import Projects from "./pages/Projects";
import Settings from "./pages/Settings";
import Today from "./pages/Today";
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
        {/* The running-timer bar lands here in milestone 3. */}
        <div className="timer-slot" aria-hidden="true" />
        <nav>
          <NavLink to="/" end>Today</NavLink>
          <NavLink to="/projects">Projects</NavLink>
          <NavLink to="/clients">Clients</NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
        <button className="ghost" onClick={() => logout.mutate()} disabled={logout.isPending}>
          Sign out
        </button>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Today me={me} />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/clients" element={<Clients />} />
          <Route path="/settings/*" element={<Settings me={me} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
