"use client";

import { BarChart3, Table as TableIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { BarList, DistributionChart, SpendOverTime } from "@/components/Charts";
import { Badge, Card, CardHeader, EmptyState, ErrorBanner, PageHeader, Skeleton, Stat, inputClass } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDate, money } from "@/lib/format";
import { useAsync } from "@/lib/hooks";

export default function AnalyticsPage() {
  const router = useRouter();
  const statements = useAsync(() => api.statements(), []);
  const [statement, setStatement] = useState("");
  const [currency, setCurrency] = useState("");
  const [granularity, setGranularity] = useState<"day" | "month">("day");
  const [tableView, setTableView] = useState(false);
  const analytics = useAsync(() => api.analytics({ statement_id: statement ? [statement] : undefined, currency: currency || undefined }), [statement, currency]);
  const ready = (statements.data ?? []).filter((s) => s.status === "ready");
  const a = analytics.data;
  const cur = a?.currency ?? null;
  const t = cur && a ? a.totals[cur] : undefined;
  const demo = ready.some((s) => s.is_demo && (!statement || s.id === statement));

  const explore = (params: Record<string, string>) => {
    const q = new URLSearchParams({ ...params, ...(statement ? { statement } : {}) });
    router.push(`/transactions?${q}`);
  };

  return (
    <>
      <PageHeader
        title="Spending analytics"
        description="Every chart is computed from extracted transactions. Amounts in different currencies are never added together."
        eyebrow={demo ? <Badge tone="demo">Includes demo data</Badge> : undefined}
        actions={
          <div className="flex gap-2">
            <select className={inputClass} value={statement} onChange={(e) => setStatement(e.target.value)} aria-label="Statement">
              <option value="">All statements</option>
              {ready.map((s) => <option key={s.id} value={s.id}>{s.display_name}</option>)}
            </select>
            <select className={`${inputClass} w-32`} value={currency} onChange={(e) => setCurrency(e.target.value)} aria-label="Currency">
              <option value="">Main currency</option>
              {(a?.currencies ?? []).map((c) => <option key={c}>{c}</option>)}
            </select>
          </div>
        }
      />
      {analytics.error && <div className="mb-4"><ErrorBanner message={analytics.error} onRetry={analytics.reload} /></div>}

      {analytics.loading && !a ? (
        <div className="grid gap-4 md:grid-cols-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-64" />)}</div>
      ) : !a || a.transaction_count === 0 ? (
        <Card><EmptyState icon={<BarChart3 className="size-6" />} title="No data to chart yet" body="Once a statement is processed, its spending breakdowns appear here." /></Card>
      ) : (
        <>
          <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label={`Spent (${cur})`} value={t ? money(t.debits, cur) : "—"} hint={`${t?.debit_count ?? 0} purchases & charges`} />
            <Stat label={`Payments & credits (${cur})`} value={t ? money(t.credits, cur) : "—"} hint={`${t?.credit_count ?? 0} transactions`} />
            <Stat label="Largest category" value={a.by_category[0]?.label ?? "—"} hint={a.by_category[0] ? `${money(a.by_category[0].total, cur)} · ${a.by_category[0].count} purchases` : undefined} />
            <Stat label="Period covered" value={<span className="text-lg">{a.date_range ? `${fmtDate(a.date_range[0])} – ${fmtDate(a.date_range[1])}` : "—"}</span>} hint={a.needs_review ? `${a.needs_review} transactions flagged for review` : "All rows passed validation"} />
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card className="lg:col-span-2">
              <CardHeader
                title="Spending over time"
                subtitle={`Purchases per ${granularity} in ${cur}`}
                action={
                  <div className="flex gap-1 text-xs">
                    {(["day", "month"] as const).map((g) => (
                      <button key={g} onClick={() => setGranularity(g)} className={`rounded-md px-2 py-1 ${granularity === g ? "bg-surface-3 text-ink" : "text-ink-3"}`}>{g === "day" ? "Daily" : "Monthly"}</button>
                    ))}
                    <button onClick={() => setTableView((v) => !v)} className="ml-1 rounded-md px-2 py-1 text-ink-3 hover:text-ink" title="Toggle table view"><TableIcon className="size-3.5" /></button>
                  </div>
                }
              />
              <div className="p-4">
                {tableView ? (
                  <div className="max-h-72 overflow-auto">
                    <table className="w-full text-sm">
                      <thead><tr className="text-left text-xs text-ink-3"><th className="py-1">{granularity === "day" ? "Day" : "Month"}</th><th className="py-1 text-right">Total</th><th className="py-1 text-right">Transactions</th></tr></thead>
                      <tbody>
                        {(granularity === "day" ? a.by_day : a.by_month).map((r) => (
                          <tr key={r.label} className="border-t border-line/60"><td className="py-1.5">{r.label}</td><td className="py-1.5 text-right tabular">{money(r.total, cur)}</td><td className="py-1.5 text-right tabular">{r.count}</td></tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <SpendOverTime rows={granularity === "day" ? a.by_day : a.by_month} currency={cur} granularity={granularity} />
                )}
              </div>
            </Card>

            <Card>
              <CardHeader title="Spending by category" subtitle="Click a category to see its transactions" />
              <div className="p-3"><BarList rows={a.by_category} currency={cur} max={13} onSelect={(c) => explore({ category: c })} /></div>
            </Card>
            <Card>
              <CardHeader title="Top merchants" subtitle="By total purchases" />
              <div className="p-3"><BarList rows={a.top_merchants} currency={cur} max={10} onSelect={(m) => explore({ merchant: m })} /></div>
            </Card>
            <Card>
              <CardHeader title="Transaction size distribution" subtitle={`Number of purchases by amount (${cur})`} />
              <div className="p-4"><DistributionChart rows={a.distribution} currency={cur} /></div>
            </Card>
            <Card>
              <CardHeader title="Currency breakdown" subtitle="Purchases by the currency they were made in" />
              <div className="p-4">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-line text-left text-[11px] uppercase tracking-wide text-ink-3">
                      <th className="pb-2 font-medium">Made in</th>
                      <th className="pb-2 text-right font-medium">Count</th>
                      <th className="pb-2 text-right font-medium">Original amount</th>
                      <th className="pb-2 text-right font-medium">Billed as</th>
                    </tr>
                  </thead>
                  <tbody>
                    {a.currency_breakdown.map((c) => (
                      <tr key={c.currency} className="cursor-pointer border-b border-line/60 last:border-0 hover:bg-surface-2" onClick={() => explore({ currency: c.currency })}>
                        <td className="py-2 font-medium">{c.currency}</td>
                        <td className="py-2 text-right tabular">{c.count}</td>
                        <td className="py-2 text-right tabular">{money(c.original_total, c.currency)}</td>
                        <td className="py-2 text-right tabular text-ink-2">{Object.entries(c.billed).map(([bc, v]) => money(v, bc)).join(", ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="mt-3 text-xs text-ink-3">Foreign purchases are shown in their original currency and in the amount your card was billed.</p>
              </div>
            </Card>
          </div>
        </>
      )}
    </>
  );
}
