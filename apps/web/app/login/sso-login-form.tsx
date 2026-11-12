"use client";
/**
 * PAUSED - company login (SSO) version of the sign-in form. Not used right now.
 * page.tsx shows it only when NEXT_PUBLIC_SSO_ENABLED=true (and the API needs SSO_ENABLED=true).
 * Kept so SSO work can resume without rewriting it.
 *
 * Email-first sign in (like Slack/Notion):
 *
 * 1. "email" step: the person types only their work email and presses Continue.
 *    We ask the API which way this email signs in (POST /auth/sso/discover).
 * 2a. Company login ("sso"): show "Continue with Acme Okta" - it sends them to their company's own
 *     login page (wired up in SSO step 3). If the company made it required, there is no password box.
 * 2b. Password: the usual password box (or name + password when creating an account).
 *
 * URL options: ?email=… prefills, ?mode=signup opens "create account", ?next=/path returns there after,
 * ?sso_error=… shows a message sent back by a failed company login.
 */
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { discoverLogin, login, signup, ssoStartUrl, type LoginMethod } from "@/lib/api";
import { safeNext } from "@/lib/redirect";
import { useSession } from "@/lib/session";

type Step = "email" | "password" | "sso";

export default function SsoLoginForm() {
  const params = useSearchParams();
  const router = useRouter();
  const { refresh } = useSession();
  const next = safeNext(params.get("next"));
  const [mode, setMode] = useState<"signin" | "signup">(params.get("mode") === "signup" ? "signup" : "signin");
  const [step, setStep] = useState<Step>("email");
  const [method, setMethod] = useState<LoginMethod>({ method: "password" });
  const [email, setEmail] = useState(params.get("email") ?? "");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState(params.get("sso_error") ?? "");
  const [busy, setBusy] = useState(false);
  const signingUp = mode === "signup";

  /** Step 1: finds out whether this email uses company login or a password. */
  async function checkEmail() {
    setBusy(true); setError("");
    try {
      const found = await discoverLogin(email);
      setMethod(found);
      setStep(found.method === "sso" ? "sso" : "password");
    } catch {
      setStep("password"); // lookup failed (e.g. too many tries): passwords still work
    } finally {
      setBusy(false);
    }
  }

  /** Step 2b: password sign-in or account creation. */
  async function submitPassword() {
    setBusy(true); setError("");
    try {
      if (signingUp) await signup(email, password, name);
      else await login(email, password);
      await refresh();
      router.replace(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Something went wrong.");
      setBusy(false);
    }
  }

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (step === "email") void checkEmail();
    else if (step === "password") void submitPassword();
  }

  /** Back to step 1 with a different email. */
  function changeEmail() {
    setStep("email"); setPassword(""); setError(""); setMethod({ method: "password" });
  }

  const title = step === "sso" ? "Sign in with your company"
    : signingUp ? "Create your account" : "Sign in to AgentForge";

  return (
    <main className="auth-page">
      <form className="auth-card" onSubmit={submit}>
        <h1>{title}</h1>

        {step === "email" ? (
          <div className="field">
            <label htmlFor="email">Work email</label>
            <input id="email" className="input-field" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                   autoComplete="email" required maxLength={320} autoFocus placeholder="you@company.com" />
            <small className="muted">If your company uses its own login (Okta, Microsoft, Google…), we&apos;ll send you there.</small>
          </div>
        ) : (
          <div className="auth-email-chip">
            <span>{email}</span>
            <button type="button" className="link-button" onClick={changeEmail}>Change</button>
          </div>
        )}

        {step === "sso" && method.method === "sso" && (
          <>
            <p className="muted auth-note">
              {method.required
                ? <>Your company signs everyone in through <strong>{method.display_name}</strong>. You&apos;ll go to their page, then come straight back here.</>
                : <>Your company has its own login: <strong>{method.display_name}</strong>.</>}
            </p>
            <a className="btn primary wide" href={ssoStartUrl(method.connection_id, next)}>
              Continue with {method.display_name} →
            </a>
            {!method.required && (
              <button type="button" className="link-button auth-alt" onClick={() => setStep("password")}>
                Use my password instead
              </button>
            )}
          </>
        )}

        {step === "password" && (
          <>
            {signingUp && (
              <div className="field">
                <label htmlFor="name">Name</label>
                <input id="name" className="input-field" value={name} onChange={(e) => setName(e.target.value)}
                       autoComplete="name" maxLength={200} />
              </div>
            )}
            <div className="field">
              <label htmlFor="password">Password</label>
              <input id="password" className="input-field" type="password" value={password} autoFocus
                     onChange={(e) => setPassword(e.target.value)} required minLength={signingUp ? 8 : 1} maxLength={256}
                     autoComplete={signingUp ? "new-password" : "current-password"} />
              {signingUp && <small className="muted">At least 8 characters.</small>}
            </div>
          </>
        )}

        {error && <p className="form-error" role="alert">{error}</p>}

        {step !== "sso" && (
          <button className="btn primary wide" disabled={busy}>
            {busy ? "Please wait…" : step === "email" ? "Continue" : signingUp ? "Create account" : "Sign in"}
          </button>
        )}

        {step !== "sso" && (
          <p className="muted switch-mode">
            {signingUp ? "Already have an account?" : "New to AgentForge?"}{" "}
            <button type="button" className="link-button" onClick={() => { setMode(signingUp ? "signin" : "signup"); setError(""); }}>
              {signingUp ? "Sign in" : "Create an account"}
            </button>
          </p>
        )}
      </form>
    </main>
  );
}
