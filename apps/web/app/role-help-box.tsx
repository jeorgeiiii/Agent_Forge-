"use client";
/**
 * The "What is this?" box at the top of a page: a plain explanation, the role
 * buttons, and one example from the chosen job. Used by Saved keys, Files and Triggers.
 */
import type { PageHelp } from "@/lib/page-help";
import { ROLES, useRole } from "@/lib/roles";

export default function RoleHelpBox({ help, extra }: { help: PageHelp; extra?: React.ReactNode }) {
  const [role, setRole] = useRole();
  const current = ROLES.find((r) => r.id === role);
  return (
    <section className="help-box" aria-label="What is this page?">
      <p>{help.what}</p>
      <div className="ge-role-chips" role="group" aria-label="Your role">
        {ROLES.map((r) => (
          <button key={r.id} type="button" className={`ge-role-chip${role === r.id ? " active" : ""}`}
                  aria-pressed={role === r.id} onClick={() => setRole(r.id)}>
            <span aria-hidden="true">{r.icon}</span> {r.label}
          </button>
        ))}
      </div>
      <p className="help-example">
        <strong>Example{role !== "general" && current ? ` for ${current.label}` : ""}:</strong> {help.examples[role]}
      </p>
      {extra}
    </section>
  );
}
