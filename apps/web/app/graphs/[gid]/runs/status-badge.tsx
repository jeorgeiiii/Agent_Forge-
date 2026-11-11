export default function StatusBadge({ status }: { status: string }) {
  const kind = status === "success" ? "ok" : status === "failed" || status === "cancelled" ? "err" : "wait";
  return <span className={`badge ${kind} run-status-${status}`}>{status}</span>;
}
