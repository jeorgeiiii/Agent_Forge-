import "@xyflow/react/dist/style.css";
import "./globals.css";
import { SessionProvider } from "@/lib/session";
import SiteHeader from "./site-header";

export const metadata = { title: "AgentForge" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <SessionProvider>
          <SiteHeader />
          {children}
        </SessionProvider>
      </body>
    </html>
  );
}
