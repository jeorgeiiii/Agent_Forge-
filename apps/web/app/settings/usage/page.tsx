"use client";
/**
 * Usage & limits tab - /settings/usage
 * Totals (runs, failures, AI calls, tokens, cost) for a chosen period, a breakdown
 * by day / workflow / AI model / trigger, and the workspace limits with how much is used.
 */
import { useEffect, useState } from "react";
import { getLimits, getUsage, type LimitsStatus, type UsageSummary } from "@/lib/api";
import { PAGE_HELP } from "@/lib/page-help";
import { useSession } from "@/lib/session";
import RoleHelpBox from "../../role-help-box";

const PERIODS = [
  { days: 1, label: "Last 24 hours" },
  { days: 7, label: "Last 7 days" },
  { days: 30, label: "Last 30 days" },
  { days: 0, label: "All time" },
];
const GROUPS = [
  { id: "day", label: "By day" },
  { id: "graph", label: "By workflow" },
  { id: "model", label: "By AI model" },
  { id: "trigger", label: "By how it started" },
] as const;

/** 12345 -> "12,345";  0.00321 -> "$0.0032" */
const num = (n: number) => n.toLocaleString();
const money = (n: number) => `$${n < 1 ? n.toFixed(4) : n.toFixed(2)}`;

export default function UsagePage() {
  const { workspace } = useSession();
  const ws = workspace!.id;
  const [days, setDays] = useState(7);
  const [group, setGroup] = useState<(typeof GROUPS)[number]["id"]>("day");
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [limits, setLimits] = useState<LimitsStatus | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const since = days ? new Date(Date.now() - days * 86_400_000).toISOString() : undefined;
    setUsage(null);
    getUsage(ws, group, since).then(setUsage).catch((e) => setError(e.message));
  }, [ws, days, group]);
  useEffect(() => { getLimits(ws).then(setLimits).catch(() => setLimits(null)); }, [ws]);

  const t = usage?.totals;
  return (
    <>
      <RoleHelpBox help={PAGE_HELP.usage} />
      {error && <p className="form-error" role="alert">{error}</p>}

      <div className="filter-row">
        {PERIODS.map((p) => (
          <button key={p.days} type="button" className={`ge-role-chip${days === p.days ? " active" : ""}`} onClick={() => setDays(p.days)}>{p.label}</button>
        ))}
      </div>

      <div className="stat-row">
        <Stat label="Runs" value={t ? num(t.runs) : "…"} />
        <Stat label="Failed runs" value={t ? num(t.failed_runs) : "…"} warn={!!t && t.failed_runs > 0} />
        <Stat label="AI calls" value={t ? num(t.llm_calls) : "…"} />
        <Stat label="AI text used (tokens)" value={t ? num(t.input_tokens + t.output_tokens) : "…"} />
        <Stat label="AI cost" value={t ? money(t.cost_usd) : "…"} />
      </div>

      <section className="panel-box stack">
        <div className="page-head">
          <h2>Breakdown</h2>
          <div className="filter-row" style={{ margin: 0 }}>
            {GROUPS.map((g) => (
              <button key={g.id} type="button" className={`ge-role-chip${group === g.id ? " active" : ""}`} onClick={() => setGroup(g.id)}>{g.label}</button>
            ))}
          </div>
        </div>
        {usage && usage.groups.length === 0 && <p className="muted">Nothing ran in this period yet.</p>}
        {usage && usage.groups.length > 0 && (
          <table className="run-table">
            <thead><tr><th>{GROUPS.find((g) => g.id === group)?.label.replace("By ", "")}</th><th>Runs</th><th>Failed</th><th>AI calls</th><th>Tokens</th><th>Cost</th></tr></thead>
            <tbody>
              {usage.groups.map((row) => (
                <tr key={row.key}>
                  <td>{group === "graph" ? row.key.split(":").slice(1).join(":") || row.key : row.key}</td>
                  <td>{num(row.runs)}</td><td className={row.failed_runs ? "err-text" : ""}>{num(row.failed_runs)}</td>
                  <td>{num(row.llm_calls)}</td><td>{num(row.input_tokens + row.output_tokens)}</td><td>{money(row.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {limits && (
        <section className="panel-box stack">
          <h2>Limits</h2>
          <p className="muted">Set by your administrator. When a limit is reached, new runs wait or are refused until there is room again.</p>
          <LimitBar label="Runs at the same time" used={limits.usage.concurrent_runs} max={limits.limits.max_concurrent_runs} />
          <LimitBar label="Runs today" used={limits.usage.runs_today} max={limits.limits.runs_per_day} />
          <LimitBar label="AI tokens in the last minute" used={limits.usage.tokens_last_minute} max={limits.limits.tokens_per_minute} />
          <LimitBar label="AI tokens today" used={limits.usage.tokens_today} max={limits.limits.tokens_per_day} />
        </section>
      )}
    </>
  );
}

function Stat({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return <div className="stat"><span className="muted">{label}</span><strong className={warn ? "err-text" : ""}>{value}</strong></div>;
}

/** One limit as a bar: green when there is room, amber over 75%, red when full. 0 = no limit. */
function LimitBar({ label, used, max }: { label: string; used: number; max: number }) {
  const pct = max > 0 ? Math.min(100, Math.round((used / max) * 100)) : 0;
  const tone = pct >= 100 ? "full" : pct >= 75 ? "high" : "";
  return (
    <div className="limit">
      <div className="limit-head"><span>{label}</span><span className="muted">{num(used)} / {max > 0 ? num(max) : "no limit"}</span></div>
      <div className="limit-track" role="progressbar" aria-label={label} aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className={`limit-fill ${tone}`} style={{ width: max > 0 ? `${pct}%` : "0%" }} />
      </div>
    </div>
  );
}
