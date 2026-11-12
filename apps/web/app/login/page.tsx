"use client";
/**
 * Sign-in page - /login
 *
 * Simple email + password sign in, and "Create an account" (name, email, password).
 * URL options: ?mode=signup opens "create account", ?email=… prefills, ?next=/path returns there after.
 *
 * Company login (SSO) is paused: its email-first form lives in ./sso-login-form.tsx and is shown
 * instead only when NEXT_PUBLIC_SSO_ENABLED=true (the API must also have SSO_ENABLED=true).
 */
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { login, signup } from "@/lib/api";
import { safeNext } from "@/lib/redirect";
import { useSession } from "@/lib/session";
import SsoLoginForm from "./sso-login-form";

/** Read at build time; "true" switches the page back to the company-login (SSO) flow. */
const SSO_ENABLED = process.env.NEXT_PUBLIC_SSO_ENABLED === "true";

function PasswordLoginForm() {
  const params = useSearchParams();
  const router = useRouter();
  const { refresh } = useSession();
  const [mode, setMode] = useState<"signin" | "signup">(params.get("mode") === "signup" ? "signup" : "signin");
  const [email, setEmail] = useState(params.get("email") ?? "");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const signingUp = mode === "signup";

  /** Signs in (or creates the account), reloads the session, then goes to ?next= or home. */
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (signingUp) await signup(email, password, name);
      else await login(email, password);
      await refresh();
      router.replace(safeNext(params.get("next")));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Something went wrong.");
      setBusy(false);
    }
  }

  /** Flips between "Sign in" and "Create an account". */
  function switchMode() {
    setMode(signingUp ? "signin" : "signup");
    setError("");
  }

  return (
    <main className="auth-page">
      <form className="auth-card" onSubmit={submit}>
        <h1>{signingUp ? "Create your account" : "Sign in to AgentForge"}</h1>
        {signingUp && (
          <div className="field">
            <label htmlFor="name">Name</label>
            <input id="name" className="input-field" value={name} onChange={(e) => setName(e.target.value)}
                   autoComplete="name" maxLength={200} />
          </div>
        )}
        <div className="field">
          <label htmlFor="email">Email</label>
          <input id="email" className="input-field" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                 autoComplete="email" required maxLength={320} autoFocus />
        </div>
        <div className="field">
          <label htmlFor="password">Password</label>
          <input id="password" className="input-field" type="password" value={password}
                 onChange={(e) => setPassword(e.target.value)} required minLength={signingUp ? 8 : 1} maxLength={256}
                 autoComplete={signingUp ? "new-password" : "current-password"} />
          {signingUp && <small className="muted">At least 8 characters.</small>}
        </div>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="btn primary wide" disabled={busy}>
          {busy ? "Please wait…" : signingUp ? "Create account" : "Sign in"}
        </button>
        <p className="muted switch-mode">
          {signingUp ? "Already have an account?" : "New to AgentForge?"}{" "}
          <button type="button" className="link-button" onClick={switchMode}>
            {signingUp ? "Sign in" : "Create an account"}
          </button>
        </p>
      </form>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<main className="auth-page"><p className="muted">Loading…</p></main>}>
      {SSO_ENABLED ? <SsoLoginForm /> : <PasswordLoginForm />}
    </Suspense>
  );
}
