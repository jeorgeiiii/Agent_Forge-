"use client";
/**
 * Run window - opens when you press ▶ Run in the editor (replaces the old browser prompt).
 *
 * It looks through the workflow's blocks for {{ $input.something }} and shows one box per input,
 * so people never have to type JSON. "Edit as JSON" is there for nested data.
 * Nothing runs until the input is valid; errors show inside the window, not in a corner.
 */
import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

const INPUT_REF = /\$input\.([A-Za-z_][A-Za-z0-9_]*)/g;

/** Input names used anywhere in the blocks' settings, in first-seen order. */
export function findInputNames(configs: unknown[]): string[] {
  const names = new Set<string>();
  for (const config of configs) {
    for (const match of JSON.stringify(config ?? {}).matchAll(INPUT_REF)) names.add(match[1]);
  }
  return [...names];
}

/** Text boxes give strings; "42", "true", "[1,2]" are turned into a number / boolean / list. */
function guessValue(text: string): unknown {
  const trimmed = text.trim();
  if (/^(-?\d+(\.\d+)?|true|false|null|\[[\s\S]*\]|\{[\s\S]*\})$/.test(trimmed)) {
    try { return JSON.parse(trimmed); } catch { /* keep as text */ }
  }
  return text;
}

export default function RunDialog({ inputNames, onRun, onCancel, busy }: {
  inputNames: string[]; onRun: (input: Record<string, unknown>) => void; onCancel: () => void; busy: boolean;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [jsonMode, setJsonMode] = useState(false);
  const [jsonText, setJsonText] = useState("{}");
  const [error, setError] = useState("");
  const asObject = useMemo(
    () => Object.fromEntries(inputNames.map((n) => [n, guessValue(values[n] ?? "")])), [inputNames, values]);

  useEffect(() => {
    const close = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onCancel]);

  function switchToJson() {
    setJsonText(JSON.stringify(asObject, null, 2));
    setJsonMode(true);
    setError("");
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!jsonMode) {
      const missing = inputNames.filter((n) => !(values[n] ?? "").trim());
      if (missing.length) return setError(`Please fill in: ${missing.join(", ")}.`);
      return onRun(asObject);
    }
    try {
      const parsed = JSON.parse(jsonText || "{}");
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
      onRun(parsed as Record<string, unknown>);
    } catch {
      setError('This is not valid JSON. It must look like {"topic": "Alan_Turing"} - keys and text in double quotes.');
    }
  }

  // Rendered straight into <body> so no editor panel (canvas, split view, copilot) can cover or clip it.
  return createPortal(
    <div className="rd-backdrop" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <form className="rd-card" role="dialog" aria-modal="true" aria-labelledby="rd-title" onSubmit={submit}>
        <h2 id="rd-title">Run this workflow</h2>
        {!jsonMode && inputNames.length === 0 && (
          <p className="muted">This workflow doesn&apos;t ask for any input. Press <strong>Run</strong> to start it.</p>
        )}
        {!jsonMode && inputNames.length > 0 && (
          <>
            <p className="muted help-small">Your blocks use these inputs (written as <code>{"{{ $input.name }}"}</code> in their settings):</p>
            {inputNames.map((name, index) => (
              <label key={name} className="field">
                <span>{name.replace(/_/g, " ")}</span>
                <input className="input-field" value={values[name] ?? ""} autoFocus={index === 0}
                       onChange={(e) => { setValues({ ...values, [name]: e.target.value }); setError(""); }} />
              </label>
            ))}
          </>
        )}
        {jsonMode && (
          <label className="field">
            <span>Run input (JSON)</span>
            <textarea className="input-field nf-mono" rows={7} value={jsonText} autoFocus
                      onChange={(e) => { setJsonText(e.target.value); setError(""); }} />
          </label>
        )}
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="row-actions">
          <button className="btn primary" type="submit" disabled={busy}>{busy ? "Starting…" : "▶ Run"}</button>
          <button className="btn" type="button" onClick={onCancel}>Cancel</button>
          {!jsonMode && <button className="link-button rd-json" type="button" onClick={switchToJson}>Edit as JSON</button>}
        </div>
      </form>
    </div>,
    document.body,
  );
}
