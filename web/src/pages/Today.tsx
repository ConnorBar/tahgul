import { Link } from "react-router-dom";
import type { Me } from "../api";

export default function Today({ me }: { me: Me }) {
  return (
    <section className="page">
      <h1>Today</h1>
      <p className="muted">Signed in as {me.email}.</p>

      {!me.totp_enabled && (
        <div className="notice">
          Two-factor sign-in is off. <Link to="/settings/security">Turn it on</Link> before
          putting real client data in.
        </div>
      )}

      <div className="card">
        <h2>Coming next</h2>
        <p className="muted">
          This is the skeleton. Clients and projects, the timer, the board, calendar sync and
          invoicing arrive in the milestones that follow. The full API is documented at{" "}
          <a href="/api/docs">/api/docs</a>.
        </p>
      </div>
    </section>
  );
}
