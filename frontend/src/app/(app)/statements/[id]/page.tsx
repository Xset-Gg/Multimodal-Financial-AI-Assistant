"use client";

import clsx from "clsx";
import { AlertTriangle, CheckCircle2, ChevronLeft, FileSearch, MessageSquareText, RotateCcw, ScrollText, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { ChatPanel } from "@/components/ChatPanel";
import { EventLog, StageTracker, StatusPill, lastStageBeforeFailure } from "@/components/Processing";
import { TransactionDetail, TransactionTable } from "@/components/Transactions";
import { Badge, Button, Card, CardHeader, ConfirmDialog, EmptyState, ErrorBanner, Skeleton, Spinner, Tabs } from "@/components/ui";
import { api } from "@/lib/api";
import { METHOD_LABELS, daysUntil, fmtDate, money } from "@/lib/format";
import { useAsync, useStatementStream } from "@/lib/hooks";
import type { Transaction, VerificationCheck } from "@/lib/types";

const TOTAL_LABELS: Record<string, string> = {
  previous_balance: "Previous balance",
  total_debits: "Purchases & charges",
  total_credits: "Payments & credits",
  closing_balance: "New balance",
  minimum_due: "Minimum due",
};

function DocumentViewer({ id, kind, page }: { id: string; kind: "pdf" | "image"; page: number }) {
  const src = `${api.fileUrl(id)}${kind === "pdf" ? `#page=${page}&view=FitH` : ""}`;
  return (
    <div className="h-[72vh] min-h-[480px] overflow-hidden rounded-b-xl bg-[#1f232b]">
      {kind === "pdf" ? (
        <iframe key={src} src={src} title="Original statement" className="h-full w-full" />
      ) : (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={api.fileUrl(id)} alt="Uploaded statement" className="mx-auto h-full w-auto max-w-full object-contain" />
      )}
    </div>
  );
}

function Checks({ checks }: { checks: VerificationCheck[] }) {
  if (!checks.length)
    return <p className="text-xs text-ink-3">No printed totals were found on the statement to cross-check against, so extracted figures could not be reconciled automatically.</p>;
  return (
    <ul className="space-y-2">
      {checks.map((c) => (
        <li key={c.name} className="flex items-start gap-2 text-sm">
          {c.status === "pass" ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-good" /> : <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" />}
          <div className="min-w-0">
            <div className="font-medium text-ink">
              {c.label} <span className={clsx("ml-1 text-xs font-normal", c.status === "pass" ? "text-[#4fd14f]" : "text-warning")}>{c.status === "pass" ? "Pass" : "Check"}</span>
            </div>
            <div className="text-xs text-ink-3">{c.message}</div>
            {c.expected !== undefined && c.currency !== undefined && (
              <div className="mt-0.5 text-xs tabular text-ink-3">
                Printed {money(c.expected, c.currency)} · extracted {money(c.actual ?? null, c.currency)}
              </div>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

type Tab = "verify" | "text" | "log";

export default function StatementWorkspace() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [refresh, setRefresh] = useState(0); // reload data when processing finishes
  const [attempt, setAttempt] = useState(0); // reconnect the stream after a retry
  const nonce = refresh + attempt;
  const detail = useAsync(() => api.statement(id), [id, nonce]);
  const stream = useStatementStream(id, () => setRefresh((n) => n + 1), attempt);
  const ready = (stream.status ?? detail.data?.status) === "ready";
  const txns = useAsync(() => (ready ? api.transactions({ statement_id: [id], limit: 500, include_excluded: true }) : Promise.resolve(null)), [id, ready, nonce]);
  const pages = useAsync(() => (ready ? api.pages(id) : Promise.resolve([])), [id, ready, nonce]);
  const categories = useAsync(() => api.categories(), []);
  const [tab, setTab] = useState<Tab>("verify");
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState<"all" | "review">("all");
  const [selected, setSelected] = useState<Transaction | null>(null);
  const [chatOpen, setChatOpen] = useState(true);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [retrying, setRetrying] = useState(false);

  const s = detail.data;
  const status = stream.status ?? s?.status ?? null;
  const items = useMemo(() => {
    const all = txns.data?.items ?? [];
    return filter === "review" ? all.filter((t) => t.review_status === "needs_review") : all;
  }, [txns.data, filter]);
  const reviewCount = (txns.data?.items ?? []).filter((t) => t.review_status === "needs_review").length;

  useEffect(() => {
    if (detail.error?.includes("not found")) router.replace("/dashboard");
  }, [detail.error, router]);

  async function retry() {
    setRetrying(true);
    try {
      await api.retry(id);
      setAttempt((n) => n + 1);
    } finally {
      setRetrying(false);
    }
  }

  async function remove() {
    setDeleting(true);
    try {
      await api.deleteStatement(id);
      router.push("/dashboard");
    } finally {
      setDeleting(false);
    }
  }

  function onUpdated(t: Transaction) {
    txns.setData((d) => (d ? { ...d, items: d.items.map((x) => (x.id === t.id ? t : x)) } : d));
    setSelected(t);
  }

  if (detail.loading && !s) {
    return <div className="space-y-4"><Skeleton className="h-8 w-80" /><Skeleton className="h-24 w-full" /><Skeleton className="h-[60vh] w-full" /></div>;
  }
  if (detail.error && !s) return <ErrorBanner message={detail.error} onRetry={detail.reload} />;
  if (!s) return null;

  const cur = s.statement_currency ?? Object.keys(s.totals)[0] ?? null;
  const tot = cur ? s.totals[cur] : undefined;
  const checks = s.verification?.checks ?? [];
  const left = daysUntil(s.expires_at);
  const processing = status !== "ready" && status !== "failed";

  return (
    <div className={clsx("transition-[padding]", chatOpen && ready && "xl:pr-[420px]")}>
      <Link href="/dashboard" className="mb-3 inline-flex items-center gap-1 text-xs text-ink-3 hover:text-ink-2"><ChevronLeft className="size-3.5" /> Dashboard</Link>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-xl font-semibold tracking-tight">{s.display_name.replace(/^\[DEMO\] /, "")}</h1>
            {s.is_demo && <Badge tone="demo">Demo data · fictional</Badge>}
            <StatusPill status={status} />
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-3">
            {s.issuer && <span>{s.issuer}</span>}
            {s.period_start && <span>Period {fmtDate(s.period_start)} – {fmtDate(s.period_end)}</span>}
            {s.statement_currency && <span>Billing currency {s.statement_currency}</span>}
            {s.extraction_methods.length > 0 && <span>Extracted with {s.extraction_methods.map((m) => METHOD_LABELS[m] ?? m).join(" + ")}</span>}
            <span>{left === null ? "Kept until deleted" : `Auto-deletes in ${left} day${left === 1 ? "" : "s"}`}</span>
          </div>
        </div>
        <div className="flex gap-2">
          {status === "failed" && <Button icon={<RotateCcw className="size-4" />} onClick={retry} loading={retrying}>Retry processing</Button>}
          {ready && !chatOpen && <Button variant="primary" icon={<MessageSquareText className="size-4" />} onClick={() => setChatOpen(true)}>Ask AI</Button>}
          <Button variant="danger" icon={<Trash2 className="size-4" />} onClick={() => setConfirmDelete(true)}>Delete</Button>
        </div>
      </div>

      {(processing || status === "failed") && (
        <Card className="mb-6">
          <CardHeader title={status === "failed" ? "Processing failed" : "Analyzing your statement"} subtitle="Live status from the backend worker" />
          <div className="space-y-5 p-5">
            <StageTracker current={status === "uploaded" ? "extracting" : status} failedAt={status === "failed" ? lastStageBeforeFailure(stream.events) : null} />
            {status === "failed" && <ErrorBanner message={stream.error ?? s.error_message ?? "Processing failed."} onRetry={retry} />}
            <div className="rounded-lg border border-line bg-bg p-3"><EventLog events={stream.events} /></div>
          </div>
        </Card>
      )}

      {ready && (
        <>
          <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Card className="px-5 py-4"><div className="text-xs text-ink-3">Purchases & charges</div><div className="mt-1 whitespace-nowrap text-xl font-semibold tabular">{tot ? money(tot.debits, cur) : "—"}</div><div className="text-xs text-ink-3">{tot?.debit_count ?? 0} transactions</div></Card>
            <Card className="px-5 py-4"><div className="text-xs text-ink-3">Payments & credits</div><div className="mt-1 whitespace-nowrap text-xl font-semibold tabular text-[#4fd14f]">{tot ? money(tot.credits, cur) : "—"}</div><div className="text-xs text-ink-3">{tot?.credit_count ?? 0} transactions</div></Card>
            <Card className="px-5 py-4"><div className="text-xs text-ink-3">Extracted rows</div><div className="mt-1 whitespace-nowrap text-xl font-semibold tabular">{s.transaction_count}</div><div className="text-xs text-ink-3">{s.verification?.possible_duplicates ? `${s.verification.possible_duplicates} possible duplicate(s)` : "No duplicates detected"}</div></Card>
            <Card className="px-5 py-4">
              <div className="text-xs text-ink-3">Needs review</div>
              <div className={clsx("mt-1 whitespace-nowrap text-xl font-semibold tabular", s.needs_review_count ? "text-warning" : "text-[#4fd14f]")}>{s.needs_review_count}</div>
              <div className="text-xs text-ink-3">{s.needs_review_count ? "Check these against the document" : "All rows passed validation"}</div>
            </Card>
          </div>

          <div className="mb-6 grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader title="Verification" subtitle="Extracted data compared with figures printed on the statement" />
              <div className="p-5"><Checks checks={checks} /></div>
            </Card>
            <Card>
              <CardHeader title="Statement summary (as printed)" subtitle="Read from the document, not calculated" />
              <div className="p-5">
                {Object.keys(s.detected_totals).length === 0 ? (
                  <p className="text-xs text-ink-3">No summary figures were detected on this statement.</p>
                ) : (
                  <dl className="grid grid-cols-2 gap-3">
                    {Object.entries(s.detected_totals).map(([k, v]) => (
                      <div key={k}><dt className="text-xs text-ink-3">{TOTAL_LABELS[k] ?? k}</dt><dd className="tabular text-sm font-medium">{money(v, cur)}</dd></div>
                    ))}
                  </dl>
                )}
                <p className="mt-4 text-xs text-ink-3">Extraction is automated and can be wrong. Nothing here is 100% guaranteed — verify flagged rows against the original.</p>
              </div>
            </Card>
          </div>

          <Tabs<Tab>
            value={tab}
            onChange={setTab}
            tabs={[
              { id: "verify", label: <span className="flex items-center gap-1.5"><FileSearch className="size-3.5" /> Document & extracted data</span> },
              { id: "text", label: <span className="flex items-center gap-1.5"><ScrollText className="size-3.5" /> Extracted text</span> },
              { id: "log", label: "Processing log" },
            ]}
          />
          <div className="mt-4">
            {tab === "verify" && (
              <div className="grid gap-4 2xl:grid-cols-2">
                <Card>
                  <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
                    <span className="text-sm font-medium">Original document</span>
                    {s.document?.kind === "pdf" && (s.document.page_count ?? 1) > 1 && (
                      <div className="flex gap-1">
                        {Array.from({ length: s.document.page_count ?? 1 }, (_, i) => (
                          <button key={i} onClick={() => setPage(i + 1)} className={clsx("h-7 min-w-7 rounded-md px-2 text-xs", page === i + 1 ? "bg-accent text-white" : "bg-surface-2 text-ink-3 hover:text-ink")}>
                            {i + 1}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                  {s.document && <DocumentViewer id={id} kind={s.document.kind} page={page} />}
                </Card>
                <Card className="flex min-h-0 flex-col">
                  <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
                    <span className="text-sm font-medium">Extracted transactions</span>
                    <div className="flex gap-1 text-xs">
                      <button onClick={() => setFilter("all")} className={clsx("rounded-md px-2 py-1", filter === "all" ? "bg-surface-3 text-ink" : "text-ink-3")}>All {txns.data?.total ?? ""}</button>
                      <button onClick={() => setFilter("review")} className={clsx("rounded-md px-2 py-1", filter === "review" ? "bg-surface-3 text-ink" : "text-ink-3")}>Needs review {reviewCount}</button>
                    </div>
                  </div>
                  <div className="h-[72vh] min-h-[480px] overflow-auto">
                    {txns.loading && !txns.data ? (
                      <div className="flex justify-center py-10"><Spinner /></div>
                    ) : items.length === 0 ? (
                      <EmptyState title={filter === "review" ? "Nothing needs review" : "No transactions were extracted"} body={filter === "review" ? "All rows passed validation." : "See the Verification panel and Extracted text tab for what was read."} />
                    ) : (
                      <TransactionTable
                        items={items}
                        dense
                        onSelect={(t) => {
                          setSelected(t);
                          if (t.page_number) setPage(t.page_number);
                        }}
                      />
                    )}
                  </div>
                </Card>
              </div>
            )}
            {tab === "text" && (
              <div className="space-y-4">
                {(pages.data ?? []).map((p) => (
                  <Card key={p.page_number}>
                    <CardHeader
                      title={`Page ${p.page_number}`}
                      subtitle={`${METHOD_LABELS[p.method] ?? p.method} · ${p.char_count.toLocaleString()} characters · ${p.tables.length} table(s)${p.is_scanned ? " · image-only page" : ""}`}
                    />
                    <div className="space-y-3 p-5">
                      {p.notes.map((n) => <div key={n} className="text-xs text-warning">{n}</div>)}
                      {p.tables.map((t, ti) => (
                        <div key={ti} className="overflow-x-auto rounded-lg border border-line">
                          <table className="w-full text-xs">
                            <tbody>
                              {t.map((row, ri) => (
                                <tr key={ri} className={clsx("border-b border-line/50 last:border-0", ri === 0 && "bg-surface-2 font-medium")}>
                                  {row.map((c, ci) => <td key={ci} className="px-2 py-1 text-ink-2">{c}</td>)}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      ))}
                      {p.text && <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-bg p-3 font-mono text-xs text-ink-2">{p.text}</pre>}
                    </div>
                  </Card>
                ))}
              </div>
            )}
            {tab === "log" && (
              <Card className="p-5"><EventLog events={stream.events} /></Card>
            )}
          </div>
        </>
      )}

      {ready && chatOpen && (
        <aside className="mt-6 flex h-[80vh] flex-col overflow-hidden rounded-xl border border-line bg-surface xl:fixed xl:bottom-4 xl:right-4 xl:top-4 xl:mt-0 xl:h-auto xl:w-[400px]">
          <ChatPanel key={id} statementId={id} compact onClose={() => setChatOpen(false)} className="min-h-0 flex-1" />
        </aside>
      )}

      {selected && <TransactionDetail key={selected.id} txn={selected} onClose={() => setSelected(null)} onUpdated={onUpdated} categories={categories.data ?? []} />}
      <ConfirmDialog
        open={confirmDelete}
        title="Delete this statement?"
        confirmLabel="Delete permanently"
        busy={deleting}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={remove}
        body="The stored file, extracted pages, all transactions and related conversations will be permanently deleted."
      />
    </div>
  );
}
