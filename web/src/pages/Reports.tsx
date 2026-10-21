import { Link } from "react-router-dom";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Client, type Summary } from "../api";
import { Empty, addDays, dayLabel, hoursLabel, money, todayIn } from "../ui";
import { useWorkspace } from "../workspace";

type Range = "this-week" | "last-week" | "this-month" | "last-month" | "this-year" | "custom";

function rangeFor(r: Range, today: string): [string, string] {
  const d = new Date(`${today}T12:00:00Z`);
  const monday = addDays(today, -((d.getUTCDay() + 6) % 7));
  const y = d.getUTCFullYear();
  const m = d.getUTCMonth();
  const iso = (dt: Date) => dt.toISOString().slice(0, 10);
  switch (r) {
    case "this-week":
      return [monday, addDays(monday, 6)];
    case "last-week":
      return [addDays(monday, -7), addDays(monday, -1)];
    case "this-month":
      return [iso(new Date(Date.UTC(y, m, 1))), iso(new Date(Date.UTC(y, m + 1, 0)))];
    case "last-month":
      return [iso(new Date(Date.UTC(y, m - 1, 1))), iso(new Date(Date.UTC(y, m, 0)))];
    case "this-year":
      return [`${y}-01-01`, `${y}-12-31`];
    default:
      return [monday, addDays(monday, 6)];
  }
}

export default function Reports() {
  const { current: ws } = useWorkspace();
  const today = todayIn(ws.timezone);
  const [range, setRange] = useState<Range>("this-week");
  const [custom, setCustom] = useState(() => rangeFor("this-month", today));
  const [client, setClient] = useState("");
  const [from, to] = range === "custom" ? custom : rangeFor(range, today);

  const clients = useQuery({
    queryKey: ["clients", ws.id],
    queryFn: () => api<Client[]>(`/clients?workspace_id=${ws.id}`),
  });
  const summary = useQuery({
    queryKey: ["summary", ws.id, from, to, client],
    queryFn: () =>
      api<Summary>(
        `/reports/summary?workspace_id=${ws.id}&from=${from}&to=${to}${client ? `&client=${client}` : ""}`,
      ),
  });
  const s = summary.data;
  const cur = ws.currency;

  return (
    <section className="page wide">
      <div className="page-head">
        <h1>Reports</h1>
        <div className="row">
          <select aria-label="Date range" value={range} onChange={(e) => setRange(e.target.value as Range)}>
            <option value="this-week">This week</option>
            <option value="last-week">Last week</option>
            <option value="this-month">This month</option>
            <option value="last-month">Last month</option>
            <option value="this-year">This year</option>
            <option value="custom">Custom…</option>
          </select>
          <select aria-label="Client" value={client} onChange={(e) => setClient(e.target.value)}>
            <option value="">All clients</option>
            {clients.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
      </div>
      {range === "custom" && (
        <div className="row custom-range">
          <label className="inline">
            From
            <input type="date" value={custom[0]} onChange={(e) => setCustom([e.target.value, custom[1]])} />
          </label>
          <label className="inline">
            To
            <input type="date" value={custom[1]} onChange={(e) => setCustom([custom[0], e.target.value])} />
          </label>
        </div>
      )}
      <p className="muted">
        {dayLabel(from, { month: "short", day: "numeric", year: "numeric" })} –{" "}
        {dayLabel(to, { month: "short", day: "numeric", year: "numeric" })}
      </p>

      {s && (
        <div className="stats">
          <div className="stat">
            <span className="stat-n">{hoursLabel(s.totals.hours) || "0"}</span>
            <span className="muted">hours</span>
          </div>
          <div className="stat">
            <span className="stat-n">{hoursLabel(s.totals.billable_hours) || "0"}</span>
            <span className="muted">billable hours</span>
          </div>
          <div className="stat">
            <span className="stat-n">{money(s.totals.amount, cur)}</span>
            <span className="muted">billable amount</span>
          </div>
          <div className="stat">
            <span className="stat-n">{money(s.totals.unbilled_amount, cur)}</span>
            <span className="muted">not yet invoiced</span>
          </div>
          <div className="stat">
            <span className="stat-n">{money(s.totals.effective_rate, cur)}</span>
            <span className="muted">effective rate /h</span>
          </div>
        </div>
      )}

      {s?.entry_count === 0 && <Empty>No time logged in this range.</Empty>}

      {s && s.entry_count > 0 && (
        <>
          <div className="card flush">
            <h2 className="table-title">By client</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Client</th>
                    <th className="num">Hours</th>
                    <th className="num">Billable</th>
                    <th className="num">Amount</th>
                    <th className="num">Effective /h</th>
                    <th className="share-col">Share</th>
                  </tr>
                </thead>
                <tbody>
                  {s.by_client.map((c) => (
                    <tr key={c.client_id ?? "none"}>
                      <td><strong>{c.name}</strong></td>
                      <td className="num">{hoursLabel(c.hours)}</td>
                      <td className="num">{hoursLabel(c.billable_hours) || "—"}</td>
                      <td className="num">{money(c.amount, cur)}</td>
                      <td className="num">{money(c.effective_rate, cur)}</td>
                      <td className="share-col">
                        <span className="bar" aria-hidden="true">
                          <span style={{ width: `${(Number(c.hours) / Number(s.totals.hours)) * 100}%` }} />
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card flush">
            <h2 className="table-title">By project</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Project</th>
                    <th className="num">Hours</th>
                    <th className="num">Amount</th>
                    <th>Budget</th>
                  </tr>
                </thead>
                <tbody>
                  {s.by_project.map((p) => (
                    <tr key={p.project_id ?? "none"}>
                      <td>
                        {p.colour && <span className="dot inline-dot" style={{ background: p.colour }} />}
                        <strong>{p.name}</strong>
                        {p.client_name && <span className="muted"> · {p.client_name}</span>}
                      </td>
                      <td className="num">{hoursLabel(p.hours)}</td>
                      <td className="num">{money(p.amount, cur)}</td>
                      <td><Budget p={p} currency={cur} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {s.by_scope.length > 0 && (
            <div className="card flush">
              <h2 className="table-title">By scope</h2>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Scope</th>
                      <th className="num">Hours</th>
                      <th className="num">Amount</th>
                      <th>Budget (all time)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {s.by_scope.map((x) => (
                      <tr key={`${x.project_id}-${x.scope_id}`}>
                        <td>
                          <Link to={`/projects/${x.project_id}?view=scopes`}>
                            {x.scope_id ? <strong>{x.title}</strong> : <em>{x.title}</em>}
                          </Link>
                          <span className="muted"> · {x.project_name}</span>
                        </td>
                        <td className="num">{hoursLabel(x.hours)}</td>
                        <td className="num">{money(x.amount, cur)}</td>
                        <td className="small">
                          {x.budget_hours ? (
                            <span className={x.burn_warning === "over" ? "danger-text" : x.burn_warning === "near" ? "warn-text" : undefined}>
                              {hoursLabel(x.used_hours_all_time ?? "0") || 0} of {hoursLabel(x.budget_hours)} h
                            </span>
                          ) : x.scope_id ? <span className="muted">—</span> : <span className="muted">possible scope creep</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {s.by_week.length > 1 && (
            <div className="card flush">
              <h2 className="table-title">By week</h2>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Week of</th>
                      <th className="num">Hours</th>
                      <th className="num">Billable</th>
                      <th className="num">Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {s.by_week.map((w) => (
                      <tr key={w.week_start}>
                        <td>{dayLabel(w.week_start, { month: "short", day: "numeric" })}</td>
                        <td className="num">{hoursLabel(w.hours)}</td>
                        <td className="num">{hoursLabel(w.billable_hours) || "—"}</td>
                        <td className="num">{money(w.amount, cur)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function Budget({ p, currency }: { p: Summary["by_project"][number]; currency: string }) {
  const parts: { used: number; total: number; label: string }[] = [];
  if (p.budget_hours)
    parts.push({
      used: Number(p.used_hours_all_time),
      total: Number(p.budget_hours),
      label: `${hoursLabel(p.used_hours_all_time) || 0} of ${hoursLabel(p.budget_hours)} h`,
    });
  if (p.budget_amount)
    parts.push({
      used: Number(p.used_amount_all_time),
      total: Number(p.budget_amount),
      label: `${money(p.used_amount_all_time, currency)} of ${money(p.budget_amount, currency)}`,
    });
  if (!parts.length) return <span className="muted">—</span>;
  return (
    <span className="budgets">
      {parts.map((b) => {
        const pct = b.total ? (b.used / b.total) * 100 : 0;
        const level = pct >= 100 ? "over" : pct >= 80 ? "warn" : "ok";
        return (
          <span key={b.label} className="budget">
            <span className={`bar ${level}`} aria-hidden="true">
              <span style={{ width: `${Math.min(pct, 100)}%` }} />
            </span>
            <span className="small">{b.label}</span>
          </span>
        );
      })}
    </span>
  );
}
