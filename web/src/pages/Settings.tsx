import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import type { Me } from "../api";
import Activities from "./Activities";
import CalendarSettings from "./CalendarSettings";
import Security from "./Security";
import Tokens from "./Tokens";
import Webhooks from "./Webhooks";
import WorkspaceSettings from "./WorkspaceSettings";

export default function Settings({ me }: { me: Me }) {
  return (
    <section className="page">
      <h1>Settings</h1>
      <nav className="tabs" aria-label="Settings sections">
        <NavLink to="workspace">Workspace</NavLink>
        <NavLink to="activities">Activities</NavLink>
        <NavLink to="calendars">Calendars</NavLink>
        <NavLink to="tokens">API tokens</NavLink>
        <NavLink to="webhooks">Webhooks</NavLink>
        <NavLink to="security">Security</NavLink>
      </nav>
      <Routes>
        <Route path="workspace" element={<WorkspaceSettings />} />
        <Route path="activities" element={<Activities />} />
        <Route path="calendars" element={<CalendarSettings />} />
        <Route path="tokens" element={<Tokens />} />
        <Route path="webhooks" element={<Webhooks />} />
        <Route path="security" element={<Security me={me} />} />
        <Route path="*" element={<Navigate to="workspace" replace />} />
      </Routes>
    </section>
  );
}
