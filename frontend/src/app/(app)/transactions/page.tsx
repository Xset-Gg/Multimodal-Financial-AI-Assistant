"use client";

import { Download, FilterX, Search, Table2 } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { TransactionDetail, TransactionTable } from "@/components/Transactions";
import { Button, Card, EmptyState, ErrorBanner, PageHeader, Spinner, inputClass } from "@/components/ui";
import { api, type TxnQuery } from "@/lib/api";
import { money } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import type { Transaction } from "@/lib/types";

const PAGE = 50;
type SortKey = "date" | "amount" | "merchant" | "category" | "confidence";

interface Filters {
  statement: string;
  search: string;
  merchant: string;
  category: string;
  currency: string;
  txn_type: "" | "debit" | "credit";
  review: string;
  date_from: string;
  date_to: string;
  min_amount: string;
  max_amount: string;
  include_excluded: boolean;
}

const EMPTY: Filters = { statement: "", search: "", merchant: "", category: "", currency: "", txn_type: "", review: "", date_from: "", date_to: "", min_amount: "", max_amount: "", include_excluded: false };

function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function Explorer() {
  const params = useSearchParams();
  const [filters, setFilters] = useState<Filters>({
    ...EMPTY,
    category: params.get("category") ?? "",
    statement: params.get("statement") ?? "",
    merchant: params.get("merchant") ?? "",
    currency: params.get("currency") ?? "",
  });
  const [sort, setSort] = useState<SortKey>("date");
  const [direction, setDirection] = useState<"asc" | "desc">("desc");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Transaction | null>(null);
  const debounced = useDebounced(filters);

  const statements = useAsync(() => api.statements(), []);
  const categories = useAsync(() => api.categories(), []);
  const ready = (statements.data ?? []).filter((s) => s.status === "ready");
  const names = Object.fromEntries(ready.map((s) => [s.id, s.display_name]));

  const query: TxnQuery = useMemo(
    () => ({
      statement_id: debounced.statement ? [debounced.statement] : undefined,
      search: debounced.search || undefined,
      merchant: debounced.merchant || undefined,
      category: debounced.category ? [debounced.category] : undefined,
      currency: debounced.currency || undefined,
      txn_type: debounced.txn_type || undefined,
      review: debounced.review || undefined,
      date_from: debounced.date_from || undefined,
      date_to: debounced.date_to || undefined,
      min_amount: debounced.min_amount || undefined,
      max_amount: debounced.max_amount || undefined,
      include_excluded: debounced.include_excluded || undefined,
      sort,
      direction,
      limit: PAGE,
      offset,
    }),
    [debounced, sort, direction, offset],
  );
  const data = useAsync(() => api.transactions(query), [JSON.stringify(query)]);
  // Totals for the whole filtered set (not just this page), computed by the backend.
  const analytics = useAsync(
    () => api.analytics({ statement_id: query.statement_id, date_from: query.date_from, date_to: query.date_to }),
    [query.statement_id?.join(","), query.date_from, query.date_to],
  );
  const currencies = analytics.data?.currencies ?? [];

  // Any filter or sort change starts from the first page.
  const set = <K extends keyof Filters>(k: K, v: Filters[K]) => {
    setFilters((f) => ({ ...f, [k]: v }));
    setOffset(0);
  };
  const active = Object.entries(filters).filter(([k, v]) => v && (EMPTY as unknown as Record<string, unknown>)[k] !== v).length;

  function onSort(k: SortKey) {
    setOffset(0);
    if (sort === k) setDirection((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSort(k);
      setDirection(k === "merchant" || k === "category" ? "asc" : "desc");
    }
  }

  async function exportCsv() {
    const rows: Transaction[] = [];
    for (let off = 0; ; off += 500) {
      const page = await api.transactions({ ...query, limit: 500, offset: off });
      rows.push(...page.items);
      if (rows.length >= page.total || page.items.length === 0) break;
    }
    const esc = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const header = ["date", "merchant", "description", "amount", "currency", "original_amount", "original_currency", "type", "category", "page", "source", "review_status", "uncertain_fields", "flags"];
    const lines = rows.map((t) => [t.txn_date, t.merchant, t.description, t.amount, t.currency, t.original_amount, t.original_currency, t.txn_type, t.category, t.page_number, t.source_method, t.review_status, t.uncertain_fields.join(";"), t.flags.join(";")].map(esc).join(","));
    const blob = new Blob([[header.join(","), ...lines].join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "spendlens-transactions.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  const page = data.data;
  return (
    <>
      <PageHeader
        title="Transaction explorer"
        description="Search, filter and inspect every extracted transaction. Click a row for its source text and to correct it."
        actions={<Button icon={<Download className="size-4" />} onClick={exportCsv} disabled={!page?.total}>Export CSV</Button>}
      />
      <Card className="mb-4 p-4">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
          <div className="relative xl:col-span-2">
            <Search className="pointer-events-none absolute left-3 top-2.5 size-4 text-ink-3" />
            <input className={`${inputClass} pl-9`} placeholder="Search merchant, description, category…" value={filters.search} onChange={(e) => set("search", e.target.value)} />
          </div>
          <select className={inputClass} value={filters.statement} onChange={(e) => set("statement", e.target.value)} aria-label="Statement">
            <option value="">All statements</option>
            {ready.map((s) => <option key={s.id} value={s.id}>{s.display_name}</option>)}
          </select>
          <select className={inputClass} value={filters.category} onChange={(e) => set("category", e.target.value)} aria-label="Category">
            <option value="">All categories</option>
            {(categories.data ?? []).map((c) => <option key={c}>{c}</option>)}
          </select>
          <select className={inputClass} value={filters.currency} onChange={(e) => set("currency", e.target.value)} aria-label="Currency">
            <option value="">Any currency</option>
            {currencies.map((c) => <option key={c}>{c}</option>)}
            {analytics.data?.currency_breakdown.map((c) => c.currency).filter((c) => !currencies.includes(c)).map((c) => <option key={c}>{c}</option>)}
          </select>
          <select className={inputClass} value={filters.txn_type} onChange={(e) => set("txn_type", e.target.value as Filters["txn_type"])} aria-label="Type">
            <option value="">Debits & credits</option>
            <option value="debit">Purchases / debits</option>
            <option value="credit">Payments / credits</option>
          </select>
          <input className={inputClass} placeholder="Merchant contains…" value={filters.merchant} onChange={(e) => set("merchant", e.target.value)} />
          <label className="flex items-center gap-2 text-xs text-ink-3">From<input type="date" className={inputClass} value={filters.date_from} onChange={(e) => set("date_from", e.target.value)} /></label>
          <label className="flex items-center gap-2 text-xs text-ink-3">To<input type="date" className={inputClass} value={filters.date_to} onChange={(e) => set("date_to", e.target.value)} /></label>
          <input className={inputClass} inputMode="decimal" placeholder="Min amount" value={filters.min_amount} onChange={(e) => set("min_amount", e.target.value.replace(/[^\d.]/g, ""))} />
          <input className={inputClass} inputMode="decimal" placeholder="Max amount" value={filters.max_amount} onChange={(e) => set("max_amount", e.target.value.replace(/[^\d.]/g, ""))} />
          <select className={inputClass} value={filters.review} onChange={(e) => set("review", e.target.value)} aria-label="Review status">
            <option value="">Any review status</option>
            <option value="needs_review">Needs review</option>
            <option value="user_verified">Verified by me</option>
            <option value="ok">Passed validation</option>
          </select>
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-ink-3">
          <label className="flex items-center gap-2"><input type="checkbox" checked={filters.include_excluded} onChange={(e) => set("include_excluded", e.target.checked)} className="accent-[var(--accent)]" /> Include rows I excluded</label>
          {active > 0 && <Button size="sm" variant="ghost" icon={<FilterX className="size-3.5" />} onClick={() => {
                setFilters(EMPTY);
                setOffset(0);
              }}>Clear {active} filter{active === 1 ? "" : "s"}</Button>}
        </div>
      </Card>

      {data.error && <div className="mb-4"><ErrorBanner message={data.error} onRetry={data.reload} /></div>}
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5 text-xs text-ink-3">
          <span>
            {page ? `${page.total.toLocaleString()} matching transaction${page.total === 1 ? "" : "s"}` : "Loading…"}
            {page && page.total > 0 &&
              Object.entries(page.totals).map(([c, t]) => (
                <span key={c} className="ml-3 tabular">
                  <span className="text-ink-2">{money(t.debits, c)}</span> debits · <span className="text-[#4fd14f]">{money(t.credits, c)}</span> credits
                </span>
              ))}
          </span>
          {data.loading && <Spinner className="size-3.5" />}
        </div>
        {!page && data.loading ? (
          <div className="flex justify-center py-16"><Spinner /></div>
        ) : page && page.total === 0 ? (
          <EmptyState icon={<Table2 className="size-6" />} title={ready.length ? "No transactions match these filters" : "No processed statements yet"} body={ready.length ? "Try widening the date range or clearing filters." : "Upload a statement or load the demo from the dashboard."} />
        ) : (
          <div className="overflow-x-auto">
            <TransactionTable items={page?.items ?? []} onSelect={setSelected} sort={sort} direction={direction} onSort={onSort} showStatementColumn={ready.length > 1} statementNames={names} />
          </div>
        )}
        {page && page.total > PAGE && (
          <div className="flex items-center justify-between border-t border-line px-4 py-2.5 text-xs text-ink-3">
            <span>{offset + 1}–{Math.min(offset + PAGE, page.total)} of {page.total}</span>
            <div className="flex gap-2">
              <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
              <Button size="sm" disabled={offset + PAGE >= page.total} onClick={() => setOffset(offset + PAGE)}>Next</Button>
            </div>
          </div>
        )}
      </Card>
      {selected && (
      <TransactionDetail
        key={selected.id}
        txn={selected}
        onClose={() => setSelected(null)}
        categories={categories.data ?? []}
        onUpdated={(t) => {
          setSelected(t);
          data.setData((d) => (d ? { ...d, items: d.items.map((x) => (x.id === t.id ? t : x)) } : d));
        }}
      />
      )}
    </>
  );
}

export default function TransactionsPage() {
  return (
    <Suspense fallback={<div className="flex justify-center py-16"><Spinner /></div>}>
      <Explorer />
    </Suspense>
  );
}
