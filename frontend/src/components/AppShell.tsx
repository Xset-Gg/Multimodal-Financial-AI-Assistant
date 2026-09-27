"use client";

import clsx from "clsx";
import { BarChart3, LayoutDashboard, Lock, MessageSquareText, Settings, Table2, Upload } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { useSession } from "@/lib/hooks";

import { Logo } from "./Logo";

const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/upload", label: "Upload", icon: Upload },
  { href: "/transactions", label: "Transactions", icon: Table2 },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/assistant", label: "Assistant", icon: MessageSquareText },
  { href: "/settings", label: "Privacy & settings", icon: Settings },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const session = useSession();
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-line bg-[#0c0e13] px-3 py-4 md:flex">
        <Link href="/" className="px-2 pb-6">
          <Logo />
        </Link>
        <nav className="flex flex-1 flex-col gap-0.5">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || (href !== "/dashboard" && pathname.startsWith(href)) || (href === "/dashboard" && pathname.startsWith("/statements"));
            return (
              <Link
                key={href}
                href={href}
                className={clsx(
                  "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors",
                  active ? "bg-surface-2 text-ink" : "text-ink-3 hover:bg-surface hover:text-ink-2",
                )}
              >
                <Icon className={clsx("size-4", active && "text-accent-ink")} />
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="rounded-lg border border-line bg-surface px-3 py-3 text-xs text-ink-3">
          <div className="mb-1.5 flex items-center gap-1.5 font-medium text-ink-2">
            <Lock className="size-3.5 text-good" /> Private workspace
          </div>
          Files are stored server-side under random IDs and auto-deleted after{" "}
          <span className="text-ink-2">{session ? `${session.retention_days} day${session.retention_days === 1 ? "" : "s"}` : "…"}</span>.
          <Link href="/settings" className="mt-1.5 block text-accent-ink hover:underline">
            Manage data
          </Link>
        </div>
        <div className="mt-3 flex items-center gap-2 px-1 text-[11px] text-ink-3">
          <span className={clsx("size-1.5 rounded-full", session?.vision_enabled ? "bg-good" : "bg-warning")} />
          {session ? (session.vision_enabled ? "Groq vision + LLM connected" : "Groq not configured · text PDFs only") : "Connecting…"}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* mobile nav */}
        <header className="flex items-center gap-3 overflow-x-auto border-b border-line px-4 py-3 md:hidden">
          <Link href="/dashboard">
            <Logo />
          </Link>
          {NAV.map(({ href, icon: Icon, label }) => (
            <Link key={href} href={href} aria-label={label} className={clsx("rounded-md p-2", pathname.startsWith(href) ? "bg-surface-2 text-ink" : "text-ink-3")}>
              <Icon className="size-4" />
            </Link>
          ))}
        </header>
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 md:px-8 md:py-8">{children}</main>
      </div>
    </div>
  );
}

export function PrivacyNote({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <p className={clsx("flex items-start gap-1.5 text-xs text-ink-3", className)}>
      <Lock className="mt-px size-3.5 shrink-0 text-good" />
      <span>{children ?? "Your statement never leaves this server except page images sent to the configured Groq model for vision extraction."}</span>
    </p>
  );
}
