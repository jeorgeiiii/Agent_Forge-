"use client";

import { useEffect, useState } from "react";
import type { CredentialSummary, JsonSchema } from "@/lib/api";
import { FIELD_HELP } from "@/lib/help";

type Config = Record<string, unknown>;

const LONG_TEXT = new Set(["prompt", "system", "template", "body", "message"]);

function pretty(value: unknown): string {
  return value === undefined ? "" : JSON.stringify(value, null, 2);
}

/** A JSON text box that only commits when the text parses. */
function JsonField({ id, value, onChange, rows = 4 }: { id: string; value: unknown; onChange: (v: unknown) => void; rows?: number }) {
  const [text, setText] = useState(pretty(value));
  const [error, setError] = useState("");
  useEffect(() => {
    // only replace the text when the value changed from outside (not while the user types valid JSON)
    try {
      if (JSON.stringify(JSON.parse(text)) === JSON.stringify(value)) return;
    } catch {
      /* text is mid-edit; fall through */
    }
    setText(pretty(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return (
    <>
      <textarea
        id={id}
        className="nf-input nf-mono"
        rows={rows}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          if (!event.target.value.trim()) {
            setError("");
            onChange(undefined);
            return;
          }
          try {
            onChange(JSON.parse(event.target.value));
            setError("");
          } catch {
            setError("Not valid JSON yet.");
          }
        }}
      />
      {error && <small className="nf-error">{error}</small>}
    </>
  );
}

function Field({
  name, schema, required, value, onChange, credentials,
}: {
  name: string; schema: JsonSchema; required: boolean; value: unknown;
  onChange: (value: unknown) => void; credentials: CredentialSummary[];
}) {
  const id = `nf-${name}`;
  const label = (
    <label htmlFor={id} className="nf-label">
      {name.replace(/_/g, " ")}
      {required && <span className="nf-required" title="required"> *</span>}
    </label>
  );
  const help = FIELD_HELP[name] && <small className="nf-help">{FIELD_HELP[name]}</small>;

  if (name === "credential_id") {
    return (
      <div className="nf-field">
        {label}
        <select id={id} className="nf-input" value={value === undefined ? "" : String(value)}
                onChange={(e) => onChange(e.target.value ? Number(e.target.value) : undefined)}>
          <option value="">- choose a credential -</option>
          {credentials.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.type})</option>)}
        </select>
        {help}
        {credentials.length === 0 && <small className="nf-help">No credentials in this workspace yet.</small>}
      </div>
    );
  }
  if (schema.enum) {
    return (
      <div className="nf-field">
        {label}
        <select id={id} className="nf-input" value={value === undefined ? "" : String(value)}
                onChange={(e) => onChange(e.target.value || undefined)}>
          <option value="">- default -</option>
          {schema.enum.map((option) => <option key={String(option)} value={String(option)}>{String(option)}</option>)}
        </select>
        {help}
      </div>
    );
  }
  if (schema.type === "boolean") {
    return (
      <div className="nf-field nf-check">
        <input id={id} type="checkbox" checked={value === true} onChange={(e) => onChange(e.target.checked || undefined)} />
        {label}
        {help && <span className="nf-check-help">{help}</span>}
      </div>
    );
  }
  if (schema.type === "number" || schema.type === "integer") {
    return (
      <div className="nf-field">
        {label}
        <input id={id} className="nf-input" type="number" step={schema.type === "integer" ? 1 : "any"}
               min={schema.minimum} max={schema.maximum} value={value === undefined ? "" : String(value)}
               onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))} />
        {help}
      </div>
    );
  }
  if (schema.type === "object" && schema.properties && Object.keys(schema.properties).length) {
    const nested = (value as Config | undefined) ?? {};
    return (
      <fieldset className="nf-fieldset">
        <legend className="nf-label">{name.replace(/_/g, " ")}</legend>
        {help}
        {Object.entries(schema.properties).map(([key, sub]) => (
          <Field key={key} name={key} schema={sub} required={!!schema.required?.includes(key)} value={nested[key]}
                 credentials={credentials}
                 onChange={(v) => {
                   const next = { ...nested, [key]: v };
                   if (v === undefined) delete next[key];
                   onChange(Object.keys(next).length ? next : undefined);
                 }} />
        ))}
      </fieldset>
    );
  }
  if (schema.type === "object" || schema.type === "array") {
    return (
      <div className="nf-field">
        {label}
        <JsonField id={id} value={value} onChange={onChange} rows={name === "tools" ? 8 : 4} />
        {help}
      </div>
    );
  }
  // strings and "any" (condition, body, body_file ...) are edited as text
  const text = value === undefined ? "" : typeof value === "string" ? value : JSON.stringify(value);
  return (
    <div className="nf-field">
      {label}
      {LONG_TEXT.has(name) ? (
        <textarea id={id} className="nf-input" rows={name === "template" || name === "prompt" ? 5 : 3} value={text}
                  onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)} />
      ) : (
        <input id={id} className="nf-input" value={text} maxLength={schema.maxLength}
               onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)} />
      )}
      {help}
    </div>
  );
}

export default function NodeForm({
  schema, config, onChange, credentials,
}: {
  schema: JsonSchema; config: Config; onChange: (config: Config) => void; credentials: CredentialSummary[];
}) {
  const properties = schema.properties ?? {};
  if (!Object.keys(properties).length) return <p className="nf-help">This node has no settings.</p>;
  return (
    <div className="nf-form">
      {Object.entries(properties).map(([name, sub]) => (
        <Field key={name} name={name} schema={sub} required={!!schema.required?.includes(name)} value={config[name]}
               credentials={credentials}
               onChange={(value) => {
                 const next = { ...config, [name]: value };
                 if (value === undefined) delete next[name];
                 onChange(next);
               }} />
      ))}
    </div>
  );
}
