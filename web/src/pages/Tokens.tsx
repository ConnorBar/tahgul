import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { TOKEN_SCOPES, api, del, errorMessage, post, type ApiToken } from "../api";

type Created = ApiToken & { token: string };

const SCOPE_HELP: Record<string, string> = {
  read: "See everything",
  write: "Create and change tasks, time and events",
  invoice: "Draft and manage invoices",
};

export default function Tokens() {
  const qc = useQueryClient();
  const tokens = useQuery({ queryKey: ["tokens"], queryFn: () => api<ApiToken[]>("/tokens") });

  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["read", "write"]);
  const [created, setCreated] = useState<Created | null>(null);
  const [copied, setCopied] = useState(false);

  const create = useMutation({
    mutationFn: () => post<Created>("/tokens", { name, scopes }),
    onSuccess: (t) => {
      setCreated(t);
      setCopied(false);
      setName("");
      qc.invalidateQueries({ queryKey: ["tokens"] });
    },
  });

  const revoke = useMutation({
    mutationFn: (id: string) => del(`/tokens/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tokens"] }),
  });

  const toggle = (s: string) =>
    setScopes((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };

  return (
    <section className="page">
      <h1>API tokens</h1>
      <p className="muted">
        One token per client (Siri, the agent, scripts). Send it as{" "}
        <code>Authorization: Bearer …</code>. Tokens can't manage other tokens or security
        settings.
      </p>

      {created && (
        <div className="card reveal">
          <h2>Copy “{created.name}” now</h2>
          <p className="muted">This is the only time the full token is shown.</p>
          <code className="secret">{created.token}</code>
          <div className="row">
            <button
              onClick={async () => {
                await navigator.clipboard.writeText(created.token);
                setCopied(true);
              }}
            >
              {copied ? "Copied" : "Copy"}
            </button>
            <button className="ghost" onClick={() => setCreated(null)}>
              Done
            </button>
          </div>
        </div>
      )}

      <form className="card" onSubmit={submit}>
        <h2>New token</h2>
        <label>
          Name
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Siri, local agent"
            maxLength={100}
            required
          />
        </label>
        <fieldset>
          <legend>Scopes</legend>
          {TOKEN_SCOPES.map((s) => (
            <label key={s} className="check">
              <input type="checkbox" checked={scopes.includes(s)} onChange={() => toggle(s)} />
              <span>
                <strong>{s}</strong> <span className="muted">— {SCOPE_HELP[s]}</span>
              </span>
            </label>
          ))}
        </fieldset>
        {create.isError && <p className="error">{errorMessage(create.error)}</p>}
        <button type="submit" disabled={create.isPending || scopes.length === 0}>
          Create token
        </button>
      </form>

      <div className="card">
        <h2>Existing tokens</h2>
        {tokens.isPending && <p className="muted">Loading…</p>}
        {tokens.data?.length === 0 && <p className="muted">No tokens yet.</p>}
        {tokens.data && tokens.data.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Scopes</th>
                  <th>Last used</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {tokens.data.map((t) => (
                  <tr key={t.id} className={t.revoked_at ? "revoked" : undefined}>
                    <td>
                      {t.name}
                      <div className="muted mono">thg_{t.prefix}_…</div>
                    </td>
                    <td>{t.scopes.join(", ")}</td>
                    <td>{t.last_used_at ? new Date(t.last_used_at).toLocaleString() : "Never"}</td>
                    <td className="right">
                      {t.revoked_at ? (
                        <span className="muted">Revoked</span>
                      ) : (
                        <button
                          className="danger"
                          onClick={() => {
                            if (confirm(`Revoke “${t.name}”? Anything using it stops working.`))
                              revoke.mutate(t.id);
                          }}
                        >
                          Revoke
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
