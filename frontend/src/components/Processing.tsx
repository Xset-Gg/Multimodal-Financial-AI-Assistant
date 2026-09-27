"use client";

import clsx from "clsx";
import { AlertTriangle, Check, CircleX, Info } from "lucide-react";

import { fmtDateTime } from "@/lib/format";
import type { ProcessingEvent, StatementStatus } from "@/lib/types";

import { Badge } from "./ui";

export const STAGES: { id: StatementStatus | "uploading"; label: string; hint: string }[] = [
  { id: "uploading", label: "Uploading", hint: "Secure transfer & validation" },
  { id: "extracting", label: "Extracting", hint: "Docling layout + tables, vision fallback" },
  { id: "structuring", label: "Structuring", hint: "Parsing & normalizing transactions" },
  { id: "verifying", label: "Verifying", hint: "Cross-checks, duplicates, flags" },
  { id: "ready", label: "Ready", hint: "Explore and ask questions" },
];

const ORDER: Record<string, number> = { uploading: 0, uploaded: 1, extracting: 1, structuring: 2, verifying: 3, ready: 4 };

export function StatusPill({ status }: { status: StatementStatus | null }) {
  if (!status) return <Badge>…</Badge>;
  if (status === "ready") return <Badge tone="good"><Check className="size-3" /> Ready</Badge>;
  if (status === "failed") return <Badge tone="critical"><CircleX className="size-3" /> Failed</Badge>;
  const label = status === "uploaded" ? "Queued" : status[0].toUpperCase() + status.slice(1);
  return (
    <Badge tone="accent">
      <span className="size-1.5 rounded-full bg-accent animate-pulse-dot" />
      {label}
    </Badge>
  );
}

/**
 * Stage tracker. `current` is the real backend status (or "uploading" while bytes are in flight);
 * `failedAt` is the last stage reached before a failure.
 */
export function StageTracker({ current, failedAt, uploadFraction }: { current: StatementStatus | "uploading" | null; failedAt?: string | null; uploadFraction?: number }) {
  const failed = current === "failed";
  const pos = failed ? ORDER[failedAt ?? "extracting"] ?? 1 : ORDER[current ?? "uploading"] ?? 0;
  return (
    <ol className="grid grid-cols-5 gap-2">
      {STAGES.map((s, i) => {
        const done = !failed && (i < pos || (current === "ready" && i === 4));
        const active = i === pos && !done;
        const isFail = failed && i === pos;
        return (
          <li key={s.id} className="min-w-0">
            <div className={clsx("h-1 rounded-full", done ? "bg-good" : isFail ? "bg-critical" : active ? "bg-accent/40" : "bg-surface-3")}>
              {active && !isFail && (
                <div
                  className={clsx("h-1 rounded-full bg-accent", s.id === "uploading" ? "transition-[width]" : "animate-pulse-dot w-full")}
                  style={s.id === "uploading" ? { width: `${Math.round((uploadFraction ?? 0) * 100)}%` } : undefined}
                />
              )}
            </div>
            <div className={clsx("mt-2 flex items-center gap-1 text-xs font-medium", done ? "text-ink-2" : active ? "text-ink" : isFail ? "text-[#f08a8a]" : "text-ink-3")}>
              {done && <Check className="size-3 text-good" />}
              {isFail && <CircleX className="size-3" />}
              {s.label}
              {active && s.id === "uploading" && uploadFraction !== undefined && <span className="tabular text-ink-3">{Math.round(uploadFraction * 100)}%</span>}
            </div>
            <div className="mt-0.5 hidden truncate text-[11px] text-ink-3 lg:block">{s.hint}</div>
          </li>
        );
      })}
    </ol>
  );
}

export function EventLog({ events, compact }: { events: ProcessingEvent[]; compact?: boolean }) {
  if (!events.length) return <p className="text-xs text-ink-3">No processing events yet.</p>;
  return (
    <ol className={clsx("space-y-1.5 font-mono text-[11.5px]", compact && "max-h-56 overflow-y-auto pr-1")}>
      {events.map((e) => (
        <li key={e.id} className="flex gap-2">
          <span className="shrink-0 text-ink-3 tabular">{fmtDateTime(e.created_at).split(", ").pop()}</span>
          <span className={clsx("w-20 shrink-0", e.level === "error" ? "text-[#f08a8a]" : e.level === "warning" ? "text-warning" : "text-accent-ink")}>{e.stage}</span>
          <span className={clsx("min-w-0", e.level === "error" ? "text-[#f5b5b5]" : e.level === "warning" ? "text-[#fbd27a]" : "text-ink-2")}>
            {e.level === "warning" && <AlertTriangle className="mr-1 inline size-3" />}
            {e.level === "error" && <CircleX className="mr-1 inline size-3" />}
            {e.message}
          </span>
        </li>
      ))}
    </ol>
  );
}

export function lastStageBeforeFailure(events: ProcessingEvent[]): string | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const s = events[i].stage;
    if (s !== "failed" && s !== "uploaded") return s;
  }
  return "extracting";
}

export function InfoLine({ children }: { children: React.ReactNode }) {
  return (
    <p className="flex items-start gap-1.5 text-xs text-ink-3">
      <Info className="mt-px size-3.5 shrink-0" />
      <span>{children}</span>
    </p>
  );
}
