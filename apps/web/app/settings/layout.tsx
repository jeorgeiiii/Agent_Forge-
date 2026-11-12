"use client";
/**
 * Settings area - /settings/*
 * Shared frame for the four settings tabs. Every tab works on the workspace picked
 * in the top bar. Signed-out users are sent to the login page.
 */
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { useSession } from "@/lib/session";

const TABS = [
  { href: "/settings/team", label: "👥 Team" },
  { href: "/settings/usage", label: "📈 Usage & limits" },
  { href: "/settings/activity", label: "🧾 Activity log" },
  { href: "/settings/data", label: "🗂 Data keeping" },
];

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { status, workspace } = useSession();

  useEffect(() => {
    if (status === "signed-out") router.replace(`/login?next=${encodeURIComponent(pathname || "/settings/team")}`);
  }, [status, pathname, router]);

  return (
    <main className="container">
      <div className="page-head">
        <div>
          <h1 style={{ margin: 0 }}>Settings</h1>
          <p className="muted">
            {workspace ? <>Workspace <strong>{workspace.name}</strong> · you are {workspace.role === "owner" ? "an owner" : `a ${workspace.role}`}</> : "Loading…"}
          </p>
        </div>
      </div>
      <nav className="settings-tabs" aria-label="Settings sections">
        {TABS.map((t) => (
          <Link key={t.href} href={t.href} className={`settings-tab${pathname === t.href ? " active" : ""}`}>{t.label}</Link>
        ))}
      </nav>
      {status === "signed-in" && workspace ? children : <p className="muted">Loading…</p>}
    </main>
  );
}
