"use client";

import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { compactMoney, fmtDate, money } from "@/lib/format";
import type { GroupRow } from "@/lib/types";

const SERIES = "var(--series-1)";

/** Ranked horizontal bars (magnitude by category/merchant). One hue; the label carries identity. */
export function BarList({
  rows,
  currency,
  onSelect,
  max = 10,
  emptyLabel = "No data",
}: {
  rows: { label: string; total: string; count: number }[];
  currency: string | null;
  onSelect?: (label: string) => void;
  max?: number;
  emptyLabel?: string;
}) {
  const shown = rows.slice(0, max);
  const top = Math.max(...shown.map((r) => Number(r.total)), 0);
  const [hover, setHover] = useState<string | null>(null);
  if (!shown.length) return <p className="py-6 text-center text-sm text-ink-3">{emptyLabel}</p>;
  return (
    <ul className="space-y-[2px]">
      {shown.map((r) => {
        const pct = top > 0 ? (Number(r.total) / top) * 100 : 0;
        const Tag = onSelect ? "button" : "div";
        return (
          <li key={r.label}>
            <Tag
              {...(onSelect ? { onClick: () => onSelect(r.label), type: "button" as const } : {})}
              onMouseEnter={() => setHover(r.label)}
              onMouseLeave={() => setHover(null)}
              className="group relative block w-full rounded-md px-2 py-1.5 text-left hover:bg-surface-2"
              title={`${r.label}: ${money(r.total, currency)} across ${r.count} transaction${r.count === 1 ? "" : "s"}`}
            >
              <div className="relative z-10 flex items-center justify-between gap-3 text-sm">
                <span className="truncate text-ink-2 group-hover:text-ink">{r.label}</span>
                <span className="shrink-0 tabular text-ink">
                  {money(r.total, currency)}
                  <span className="ml-2 text-xs text-ink-3">{hover === r.label ? `${r.count} txn` : ""}</span>
                </span>
              </div>
              <div className="mt-1 h-1.5 w-full rounded-full bg-surface-3/60">
                <div className="h-1.5 rounded-full" style={{ width: `${Math.max(pct, 1.5)}%`, background: SERIES, opacity: hover && hover !== r.label ? 0.55 : 1 }} />
              </div>
            </Tag>
          </li>
        );
      })}
    </ul>
  );
}

function ChartTooltip({ active, payload, currency, labelFormat }: { active?: boolean; payload?: { payload: Record<string, unknown> }[]; currency: string | null; labelFormat: (p: Record<string, unknown>) => string }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-lg border border-line-strong bg-surface-2 px-3 py-2 text-xs shadow-xl">
      <div className="text-ink-3">{labelFormat(p)}</div>
      <div className="mt-0.5 tabular text-sm font-semibold text-ink">{money(p.raw as string, currency)}</div>
      <div className="text-ink-3">{String(p.count)} transaction{p.count === 1 ? "" : "s"}</div>
    </div>
  );
}

const axisProps = { stroke: "var(--axis)", tick: { fill: "#7d8494", fontSize: 11 }, tickLine: false } as const;

/** Spending per day (or month) — vertical bars on one shared axis. */
export function SpendOverTime({ rows, currency, granularity }: { rows: GroupRow[]; currency: string | null; granularity: "day" | "month" }) {
  const data = rows
    .filter((r) => r.label !== "(no date)")
    .map((r) => ({ label: r.label, value: Number(r.total), raw: r.total, count: r.count }));
  if (!data.length) return <p className="py-10 text-center text-sm text-ink-3">No dated transactions.</p>;
  const tickFmt = (v: string) =>
    granularity === "day" ? new Date(`${v}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : new Date(`${v}-01T00:00:00`).toLocaleDateString("en-GB", { month: "short", year: "2-digit" });
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barCategoryGap={2}>
        <CartesianGrid vertical={false} stroke="var(--grid)" />
        <XAxis dataKey="label" {...axisProps} tickFormatter={tickFmt} minTickGap={16} />
        <YAxis {...axisProps} axisLine={false} width={56} tickFormatter={(v: number) => compactMoney(v)} />
        <Tooltip
          cursor={{ fill: "rgba(255,255,255,0.04)" }}
          content={<ChartTooltip currency={currency} labelFormat={(p) => (granularity === "day" ? fmtDate(p.label as string, "long") : tickFmt(p.label as string))} />}
        />
        <Bar dataKey="value" fill={SERIES} radius={[4, 4, 0, 0]} maxBarSize={28} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Histogram of purchase sizes. */
export function DistributionChart({ rows, currency }: { rows: { label: string; count: number; total: string }[]; currency: string | null }) {
  const data = rows.map((r) => ({ label: r.label, value: r.count, raw: r.total, count: r.count }));
  if (!data.some((d) => d.value > 0)) return <p className="py-10 text-center text-sm text-ink-3">No purchases.</p>;
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barCategoryGap={2}>
        <CartesianGrid vertical={false} stroke="var(--grid)" />
        <XAxis dataKey="label" {...axisProps} interval={0} tick={{ fill: "#7d8494", fontSize: 10 }} />
        <YAxis {...axisProps} axisLine={false} width={32} allowDecimals={false} />
        <Tooltip
          cursor={{ fill: "rgba(255,255,255,0.04)" }}
          content={<ChartTooltip currency={currency} labelFormat={(p) => `Purchases of ${p.label as string}${currency ? ` ${currency}` : ""} · total`} />}
        />
        <Bar dataKey="value" fill={SERIES} radius={[4, 4, 0, 0]} maxBarSize={48} />
      </BarChart>
    </ResponsiveContainer>
  );
}
