import type { Metadata } from "next";
import { AppShell } from "@/components/app-shell";
import { SessionProvider } from "@/components/session-provider";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "ResolveAI · Access operations",
    template: "%s · ResolveAI",
  },
  description:
    "Policy-grounded IT access requests, human approvals, and verified actions.",
};
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <SessionProvider>
          <AppShell>{children}</AppShell>
        </SessionProvider>
      </body>
    </html>
  );
}
