"use client";

import { FileText, FlaskConical, Trash2, Upload } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { PrivacyNote } from "@/components/AppShell";
import { BarList, SpendOverTime } from "@/components/Charts";
import { StatusPill } from "@/components/Processing";
import { Badge, Button, Card, CardHeader, ConfirmDialog, EmptyState, ErrorBanner, PageHeader, Skeleton, Stat } from "@/components/ui";
import { api } from "@/lib/api";
import { daysUntil, fmtDate, money, relativeTime } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import type { Statement } from "@/lib/types";

const PROCESSING = new Set(["uploaded", "extracting", "structuring", "verifying"]);

export default function DashboardPage() {
  const router = useRouter();
  const statements = useAsync(() => api.statements(), []);
  const readyKey = (statements.data ?? []).filter((s) => s.status === "ready").map((s) => s.id).join(",");
  const analytics = useAsync(() => api.analytics({}), [readyKey]);
  const [demoBusy, setDemoBusy] = useState(false);
  const [demoError, setDemoError] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<Statement | null>(null);
  const [deleting, setDeleting] = useState(false);

  // While anything is processing, refresh the list from the backend (statuses are real backend state).
  const anyProcessing = (statements.data ?? []).some((s) => PROCESSING.has(s.status));
  const { reload } = statements;
  useEffect(() => {
    if (!anyProcessing) return;
    const t = setInterval(reload, 2000);
    return () => clearInterval(t);
  }, [anyProcessing, reload]);

  async function loadDemo() {
    setDemoBusy(true);
    setDemoError(null);
    try {
      const s = await api.loadDemo();
      router.push(`/statements/${s.id}`);
    } catch (e) {
      setDemoError(e instanceof Error ? e.message : "Could not load the demo");
      setDemoBusy(false);
    }
  }

  async function confirmDelete() {
    if (!toDelete) return;
    setDeleting(true);
    try {
      await api.deleteStatement(toDelete.id);
      setToDelete(null);
      statements.reload();
    } finally {
      setDeleting(false);
    }
  }

  const a = analytics.data;
  const cur = a?.currency ?? null;
  const spend = cur && a?.totals[cur] ? a.totals[cur].debits : null;
  const hasData = (a?.transaction_count ?? 0) > 0;
  const list = statements.data ?? [];

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Everything here is computed from successfully extracted transactions."
        actions={
          <>
            <Button icon={<FlaskConical className="size-4" />} onClick={loadDemo} loading={demoBusy}>Load demo statement</Button>
            <Link href="/upload"><Button variant="primary" icon={<Upload className="size-4" />}>Upload statement</Button></Link>
          </>
        }
      />
      {demoError && <div className="mb-4"><ErrorBanner message={demoError} /></div>}
      {statements.error && <div className="mb-4"><ErrorBanner message={statements.error} onRetry={statements.reload} /></div>}

      <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label={`Total spending${cur ? ` (${cur})` : ""}`}
          loading={analytics.loading && !a}
          value={spend ? money(spend, cur) : "—"}
          hint={a && a.currencies.length > 1 ? `Also billed in ${a.currencies.filter((c) => c !== cur).join(", ")} (not summed)` : "Purchases & charges, excluding payments"}
        />
        <Stat label="Transactions" loading={analytics.loading && !a} value={a ? a.transaction_count.toLocaleString() : "—"} hint={a && a.needs_review ? `${a.needs_review} flagged for review` : "Across processed statements"} />
        <Stat
          label="Date range"
          loading={analytics.loading && !a}
          value={<span className="text-lg">{a?.date_range ? `${fmtDate(a.date_range[0])} – ${fmtDate(a.date_range[1])}` : "—"}</span>}
        />
        <Stat
          label="Statements"
          loading={statements.loading && !statements.data}
          value={list.length}
          hint={anyProcessing ? "Processing in progress…" : `${list.filter((s) => s.status === "ready").length} ready`}
        />
      </div>

      <Card className="mb-6">
        <CardHeader title="Your statements" subtitle="Uploaded documents and their analysis status" />
        {statements.loading && !statements.data ? (
          <div className="space-y-2 p-5">{[0, 1].map((i) => <Skeleton key={i} className="h-10 w-full" />)}</div>
        ) : list.length === 0 ? (
          <EmptyState
            icon={<FileText className="size-6" />}
            title="No statements yet"
            body="Upload a credit-card statement (PDF or image), or load the fictional demo statement to explore the product safely."
            action={
              <div className="flex gap-2">
                <Button icon={<FlaskConical className="size-4" />} onClick={loadDemo} loading={demoBusy}>Load demo</Button>
                <Link href="/upload"><Button variant="primary" icon={<Upload className="size-4" />}>Upload</Button></Link>
              </div>
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-[11px] uppercase tracking-wide text-ink-3">
                  <th className="px-5 py-2 font-medium">Statement</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Period</th>
                  <th className="px-3 py-2 text-right font-medium">Transactions</th>
                  <th className="px-3 py-2 text-right font-medium">Spending</th>
                  <th className="px-3 py-2 font-medium">Auto-delete</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {list.map((s) => {
                  const c = s.statement_currency;
                  const t = c ? s.totals[c] : Object.values(s.totals)[0];
                  const left = daysUntil(s.expires_at);
                  return (
                    <tr key={s.id} className="border-b border-line/60 last:border-0 hover:bg-surface-2/50">
                      <td className="px-5 py-3">
                        <Link href={`/statements/${s.id}`} className="flex items-center gap-2 font-medium text-ink hover:text-accent-ink">
                          <FileText className="size-4 shrink-0 text-ink-3" />
                          <span className="truncate">{s.display_name.replace(/^\[DEMO\] /, "")}</span>
                          {s.is_demo && <Badge tone="demo">Demo data</Badge>}
                        </Link>
                        <div className="mt-0.5 pl-6 text-xs text-ink-3">
                          {s.document?.kind.toUpperCase()} · uploaded {relativeTime(s.created_at)}
                          {s.issuer ? ` · ${s.issuer}` : ""}
                        </div>
                      </td>
                      <td className="px-3">
                        <StatusPill status={s.status} />
                        {s.status === "failed" && <div className="mt-1 max-w-[220px] truncate text-xs text-[#f5b5b5]" title={s.error_message ?? ""}>{s.error_message}</div>}
                      </td>
                      <td className="whitespace-nowrap px-3 text-ink-2">{s.period_start ? `${fmtDate(s.period_start)} – ${fmtDate(s.period_end)}` : "—"}</td>
                      <td className="px-3 text-right tabular">
                        {s.status === "ready" ? s.transaction_count : "—"}
                        {s.needs_review_count > 0 && <div className="text-xs text-warning">{s.needs_review_count} to review</div>}
                      </td>
                      <td className="whitespace-nowrap px-3 text-right tabular">{t ? money(t.debits, c ?? Object.keys(s.totals)[0]) : "—"}</td>
                      <td className="px-3 text-xs text-ink-3">{left === null ? "Never" : `in ${left} day${left === 1 ? "" : "s"}`}</td>
                      <td className="px-3 text-right">
                        <button onClick={() => setToDelete(s)} className="rounded-md p-1.5 text-ink-3 hover:bg-critical/10 hover:text-[#f08a8a]" aria-label={`Delete ${s.display_name}`}>
                          <Trash2 className="size-4" />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {hasData && a && (
        <div className="grid gap-6 lg:grid-cols-5">
          <Card className="lg:col-span-3">
            <CardHeader title="Spending over time" subtitle={`Daily purchases${cur ? ` in ${cur}` : ""}`} action={<Link href="/analytics" className="text-xs text-accent-ink hover:underline">Full analytics</Link>} />
            <div className="p-4"><SpendOverTime rows={a.by_day} currency={cur} granularity="day" /></div>
          </Card>
          <Card className="lg:col-span-2">
            <CardHeader title="Top categories" subtitle="Click to explore the transactions" />
            <div className="p-3">
              <BarList rows={a.by_category} currency={cur} max={7} onSelect={(label) => router.push(`/transactions?category=${encodeURIComponent(label)}`)} />
            </div>
          </Card>
        </div>
      )}
      <PrivacyNote className="mt-6" />

      <ConfirmDialog
        open={!!toDelete}
        title="Delete statement?"
        confirmLabel="Delete permanently"
        busy={deleting}
        onCancel={() => setToDelete(null)}
        onConfirm={confirmDelete}
        body={<>This permanently deletes <strong className="text-ink">{toDelete?.display_name}</strong>, the stored file, all extracted pages and transactions, and conversations about it. This cannot be undone.</>}
      />
    </>
  );
}
