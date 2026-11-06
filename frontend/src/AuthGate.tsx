import { FormEvent, useEffect, useState } from "react";
import App from "./App";

type AuthState = "checking" | "signed-out" | "signed-in";

function csrfCookie(): string {
  const item = document.cookie.split("; ").find((part) => part.startsWith("arbiter_csrf="));
  return item ? decodeURIComponent(item.slice("arbiter_csrf=".length)) : "";
}

export function AuthGate() {
  const [state, setState] = useState<AuthState>("checking");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    fetch("/api/auth/me", { credentials: "same-origin" })
      .then((response) => { if (active) setState(response.ok ? "signed-in" : "signed-out"); })
      .catch(() => { if (active) { setError("Cannot reach the server. Check that the backend is running."); setState("signed-out"); } });
    return () => { active = false; };
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "Sign in failed");
      setPassword("");
      setState("signed-in");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed");
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    try {
      await fetch("/api/auth/logout", {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRF-Token": csrfCookie() },
      });
    } finally {
      setState("signed-out");
    }
  }

  if (state === "checking") {
    return <div className="grid min-h-screen place-items-center bg-ink font-mono text-xs text-muted">VERIFYING SECURE SESSION…</div>;
  }
  if (state === "signed-out") {
    return (
      <main className="grid min-h-screen place-items-center bg-ink px-4 text-strong">
        <form onSubmit={submit} className="w-full max-w-sm rounded-xl border border-line bg-panel p-7 shadow-2xl">
          <div className="mb-6 flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-lg border border-delta/40 bg-delta/10 font-mono text-sm text-delta">A</div>
            <div><p className="font-mono text-sm font-semibold tracking-[0.2em]">ARBITER</p><p className="mt-1 text-[10px] uppercase tracking-widest text-muted">Private trading terminal</p></div>
          </div>
          <label htmlFor="admin-password" className="mb-2 block text-xs text-muted">Admin password</label>
          <input id="admin-password" autoFocus type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} className="w-full rounded-md border border-line-strong bg-ink px-3 py-3 font-mono text-sm outline-none focus:border-delta" placeholder="Enter your password" />
          {error && <p role="alert" className="mt-3 text-xs text-down">{error}</p>}
          <button disabled={busy || !password} className="mt-5 w-full rounded-md bg-delta px-4 py-3 font-mono text-xs font-semibold uppercase tracking-widest text-ink disabled:opacity-50">{busy ? "Signing in…" : "Unlock terminal"}</button>
          <p className="mt-5 text-[10px] leading-relaxed text-muted">Exchange API keys remain on the backend. Never share your admin password or API secrets.</p>
        </form>
      </main>
    );
  }
  return <><button onClick={() => void signOut()} className="fixed right-4 top-3 z-[100] rounded border border-line-strong bg-panel/95 px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-muted hover:text-strong">Lock terminal</button><App /></>;
}
