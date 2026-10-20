import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, errorMessage, post, type Me } from "../api";

export default function Login() {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [needsCode, setNeedsCode] = useState(false);

  const login = useMutation({
    mutationFn: () =>
      post<Me>("/auth/login", { email, password, totp_code: needsCode ? code : undefined }),
    onSuccess: (me) => qc.setQueryData(["me"], me),
    onError: (err) => {
      if (err instanceof ApiError && err.detail === "totp_required") setNeedsCode(true);
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate();
  };

  const showError =
    login.isError && !(login.error instanceof ApiError && login.error.detail === "totp_required");

  return (
    <div className="login">
      <form className="card" onSubmit={submit}>
        <h1>Ledger</h1>
        <p className="muted">Sign in to your workspace.</p>

        {!needsCode ? (
          <>
            <label>
              Email
              <input
                type="email"
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoFocus
              />
            </label>
            <label>
              Password
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </label>
          </>
        ) : (
          <label>
            Authenticator code
            <input
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9 ]{6,7}"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              required
              autoFocus
            />
          </label>
        )}

        {showError && <p className="error">{errorMessage(login.error)}</p>}

        <button type="submit" disabled={login.isPending}>
          {login.isPending ? "Signing in…" : needsCode ? "Verify" : "Sign in"}
        </button>
        {needsCode && (
          <button
            type="button"
            className="ghost"
            onClick={() => {
              setNeedsCode(false);
              setCode("");
              login.reset();
            }}
          >
            Back
          </button>
        )}
      </form>
    </div>
  );
}
