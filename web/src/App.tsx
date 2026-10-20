import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { ApiError, api, post, type Me } from "./api";
import Login from "./pages/Login";
import Today from "./pages/Today";
import Security from "./pages/Security";
import Tokens from "./pages/Tokens";

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

  return <Shell me={me.data} />;
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
        {/* The running-timer bar lands here in milestone 3. */}
        <div className="timer-slot" aria-hidden="true" />
        <nav>
          <NavLink to="/" end>
            Today
          </NavLink>
          <NavLink to="/settings/tokens">API tokens</NavLink>
          <NavLink to="/settings/security">Security</NavLink>
        </nav>
        <button className="ghost" onClick={() => logout.mutate()} disabled={logout.isPending}>
          Sign out
        </button>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Today me={me} />} />
          <Route path="/settings/tokens" element={<Tokens />} />
          <Route path="/settings/security" element={<Security me={me} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
