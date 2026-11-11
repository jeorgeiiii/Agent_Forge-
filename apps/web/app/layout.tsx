import "@xyflow/react/dist/style.css";
import "./globals.css";
import Link from "next/link";

export const metadata = { title: "AgentForge" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="header">
          <Link href="/" className="brand">AgentForge</Link>
          <span className="muted">v0 · local</span>
        </header>
        {children}
      </body>
    </html>
  );
}