import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import QRCode from "qrcode";
import { errorMessage, post, type Me } from "../api";

type Setup = { secret: string; otpauth_uri: string };

export default function Security({ me }: { me: Me }) {
  return (
    <>
      <TwoFactor enabled={!!me.totp_enabled} />
      <ChangePassword />
    </>
  );
}

function TwoFactor({ enabled }: { enabled: boolean }) {
  const qc = useQueryClient();
  const [setup, setSetup] = useState<Setup | null>(null);
  const [qr, setQr] = useState<string>("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");

  useEffect(() => {
    if (setup) QRCode.toDataURL(setup.otpauth_uri, { margin: 1, width: 200 }).then(setQr);
  }, [setup]);

  const done = () => {
    setSetup(null);
    setCode("");
    setPassword("");
    qc.invalidateQueries({ queryKey: ["me"] });
  };

  const start = useMutation({ mutationFn: () => post<Setup>("/auth/totp/setup"), onSuccess: setSetup });
  const enable = useMutation({ mutationFn: () => post("/auth/totp/enable", { code }), onSuccess: done });
  const disable = useMutation({
    mutationFn: () => post("/auth/totp/disable", { code, password }),
    onSuccess: done,
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    (enabled ? disable : enable).mutate();
  };

  const err = start.error ?? enable.error ?? disable.error;

  return (
    <div className="card">
      <h2>Two-factor sign-in</h2>
      {enabled ? (
        <>
          <p className="muted">On. Sign-in asks for a code from your authenticator app.</p>
          <form onSubmit={submit}>
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
            <label>
              Current code
              <input
                inputMode="numeric"
                autoComplete="one-time-code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                required
              />
            </label>
            {err && <p className="error">{errorMessage(err)}</p>}
            <button className="danger" type="submit" disabled={disable.isPending}>
              Turn off two-factor
            </button>
          </form>
        </>
      ) : !setup ? (
        <>
          <p className="muted">Off. Turn it on with any authenticator app (1Password, Authy…).</p>
          {err && <p className="error">{errorMessage(err)}</p>}
          <button onClick={() => start.mutate()} disabled={start.isPending}>
            Set up two-factor
          </button>
        </>
      ) : (
        <form onSubmit={submit}>
          <p className="muted">Scan this with your authenticator, then enter the 6-digit code.</p>
          {qr && <img className="qr" src={qr} alt="QR code for your authenticator app" />}
          <p className="muted">
            Or enter the key manually: <code className="mono">{setup.secret}</code>
          </p>
          <label>
            Code
            <input
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              required
              autoFocus
            />
          </label>
          {err && <p className="error">{errorMessage(err)}</p>}
          <div className="row">
            <button type="submit" disabled={enable.isPending}>
              Turn on
            </button>
            <button type="button" className="ghost" onClick={() => setSetup(null)}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

function ChangePassword() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const change = useMutation({
    mutationFn: () => post("/auth/password", { current_password: current, new_password: next }),
    onSuccess: () => {
      setCurrent("");
      setNext("");
    },
  });

  return (
    <form
      className="card"
      onSubmit={(e) => {
        e.preventDefault();
        change.mutate();
      }}
    >
      <h2>Password</h2>
      <label>
        Current password
        <input
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          required
        />
      </label>
      <label>
        New password <span className="muted">(12+ characters)</span>
        <input
          type="password"
          autoComplete="new-password"
          minLength={12}
          value={next}
          onChange={(e) => setNext(e.target.value)}
          required
        />
      </label>
      {change.isError && <p className="error">{errorMessage(change.error)}</p>}
      {change.isSuccess && <p className="ok">Password changed. Other sessions were signed out.</p>}
      <button type="submit" disabled={change.isPending}>
        Change password
      </button>
    </form>
  );
}
