"use client";

import clsx from "clsx";
import { AlertTriangle, ArrowDown, ArrowUp, BadgeCheck, EyeOff, FileText, Globe2 } from "lucide-react";
import { useState } from "react";

import { api } from "@/lib/api";
import { FIELD_LABELS, FLAG_LABELS, METHOD_LABELS, fmtDate, money } from "@/lib/format";
import type { Transaction } from "@/lib/types";

import { Badge, Button, ErrorBanner, Modal, inputClass } from "./ui";

export function ReviewBadge({ t }: { t: Transaction }) {
  if (t.excluded) return <Badge><EyeOff className="size-3" /> Excluded</Badge>;
  if (t.review_status === "user_verified") return <Badge tone="good"><BadgeCheck className="size-3" /> Verified</Badge>;
  if (t.review_status === "needs_review") {
    const reasons = [...t.uncertain_fields.map((f) => `Uncertain ${FIELD_LABELS[f] ?? f}`), ...t.flags.map((f) => FLAG_LABELS[f] ?? f)];
    return (
      <Badge tone="warning" title={reasons.join(" · ")}>
        <AlertTriangle className="size-3" /> Review
      </Badge>
    );
  }
  return null;
}

type SortKey = "date" | "amount" | "merchant" | "category" | "confidence";

export function TransactionTable({
  items,
  onSelect,
  sort,
  direction,
  onSort,
  highlightIds,
  dense,
  showStatementColumn,
  statementNames,
}: {
  items: Transaction[];
  onSelect?: (t: Transaction) => void;
  sort?: SortKey;
  direction?: "asc" | "desc";
  onSort?: (k: SortKey) => void;
  highlightIds?: Set<string>;
  dense?: boolean;
  showStatementColumn?: boolean;
  statementNames?: Record<string, string>;
}) {
  const header = (key: SortKey | null, label: string, align: "left" | "right" = "left") => (
    <th className={clsx("sticky top-0 z-10 bg-surface px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-ink-3", align === "right" ? "text-right" : "text-left")}>
      {key && onSort ? (
        <button className={clsx("inline-flex items-center gap-1 hover:text-ink-2", sort === key && "text-ink-2")} onClick={() => onSort(key)}>
          {label}
          {sort === key && (direction === "desc" ? <ArrowDown className="size-3" /> : <ArrowUp className="size-3" />)}
        </button>
      ) : (
        label
      )}
    </th>
  );
  return (
    <table className="w-full border-collapse text-sm">
      <thead>
        <tr className="border-b border-line">
          {header("date", "Date")}
          {header("merchant", "Merchant")}
          {header("category", "Category")}
          {showStatementColumn && header(null, "Statement")}
          {header(null, "Source")}
          {header("amount", "Amount", "right")}
        </tr>
      </thead>
      <tbody>
        {items.map((t) => (
          <tr
            key={t.id}
            onClick={() => onSelect?.(t)}
            className={clsx(
              "border-b border-line/60 transition-colors",
              onSelect && "cursor-pointer hover:bg-surface-2",
              highlightIds?.has(t.id) && "bg-accent/10",
              t.excluded && "opacity-50",
            )}
          >
            <td className={clsx("whitespace-nowrap px-3 tabular text-ink-2", dense ? "py-1.5" : "py-2.5")}>
              {t.txn_date ? fmtDate(t.txn_date) : <span className="text-warning">Unreadable</span>}
            </td>
            <td className="max-w-[280px] px-3">
              <div className="flex items-center gap-2">
                <span className="truncate text-ink">{t.merchant ?? <span className="text-warning">Unknown merchant</span>}</span>
                <ReviewBadge t={t} />
              </div>
              {!dense && t.description && t.description !== t.merchant && <div className="truncate text-xs text-ink-3">{t.description}</div>}
            </td>
            <td className="whitespace-nowrap px-3 text-ink-2">{t.category}</td>
            {showStatementColumn && <td className="max-w-[160px] truncate px-3 text-xs text-ink-3">{statementNames?.[t.statement_id] ?? "—"}</td>}
            <td className="whitespace-nowrap px-3 text-xs text-ink-3">
              {t.page_number ? `p.${t.page_number} · ` : ""}
              {METHOD_LABELS[t.source_method] ?? t.source_method}
            </td>
            <td className="whitespace-nowrap px-3 text-right tabular">
              <div className={clsx(t.txn_type === "credit" ? "text-[#4fd14f]" : "text-ink", t.amount === null && "text-warning")}>
                {t.amount === null ? "Unreadable" : `${t.txn_type === "credit" ? "+" : ""}${money(t.amount, t.currency)}`}
              </div>
              {t.original_currency && (
                <div className="flex items-center justify-end gap-1 text-[11px] text-ink-3">
                  <Globe2 className="size-3" />
                  {money(t.original_amount, t.original_currency)}
                </div>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[130px_1fr] gap-3 border-b border-line/60 py-2 text-sm last:border-0">
      <dt className="text-ink-3">{label}</dt>
      <dd className="min-w-0 text-ink">{children}</dd>
    </div>
  );
}

export function TransactionDetail({
  txn,
  onClose,
  onUpdated,
  categories,
}: {
  txn: Transaction;
  onClose: () => void;
  onUpdated: (t: Transaction) => void;
  categories: string[];
}) {
  const t = txn;
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ merchant: "", amount: "", txn_date: "", category: "", txn_type: "debit" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function startEdit() {
    setForm({ merchant: t.merchant ?? "", amount: t.amount ?? "", txn_date: t.txn_date ?? "", category: t.category, txn_type: t.txn_type });
    setError(null);
    setEditing(true);
  }

  async function patch(body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.patchTransaction(t.id, body);
      onUpdated(updated);
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Update failed");
    } finally {
      setBusy(false);
    }
  }

  function save() {
    const body: Record<string, unknown> = {};
    if (form.merchant !== (t.merchant ?? "")) body.merchant = form.merchant;
    if (form.amount !== (t.amount ?? "") && form.amount !== "") body.amount = form.amount;
    if (form.txn_date !== (t.txn_date ?? "") && form.txn_date !== "") body.txn_date = form.txn_date;
    if (form.category !== t.category) body.category = form.category;
    if (form.txn_type !== t.txn_type) body.txn_type = form.txn_type;
    if (!Object.keys(body).length) return setEditing(false);
    patch(body);
  }

  return (
    <Modal
      open
      wide
      onClose={onClose}
      title={
        <span className="flex items-center gap-2">
          Transaction details <ReviewBadge t={t} />
        </span>
      }
      footer={
        editing ? (
          <>
            <Button variant="ghost" onClick={() => setEditing(false)} disabled={busy}>Cancel</Button>
            <Button variant="primary" onClick={save} loading={busy}>Save correction</Button>
          </>
        ) : (
          <>
            <Button variant="ghost" onClick={() => patch({ excluded: !t.excluded })} loading={busy}>
              {t.excluded ? "Include in totals" : "Exclude from totals"}
            </Button>
            <Button onClick={startEdit}>Correct values</Button>
            {t.review_status !== "user_verified" && (
              <Button variant="primary" icon={<BadgeCheck className="size-4" />} onClick={() => patch({ verified: true })} loading={busy}>
                Mark verified
              </Button>
            )}
          </>
        )
      }
    >
      {error && <div className="mb-3"><ErrorBanner message={error} /></div>}
      <div className="grid gap-6 md:grid-cols-2">
        <dl>
          {editing ? (
            <div className="space-y-3">
              <label className="block text-xs text-ink-3">Merchant<input className={inputClass} value={form.merchant} onChange={(e) => setForm({ ...form, merchant: e.target.value })} /></label>
              <label className="block text-xs text-ink-3">Date<input type="date" className={inputClass} value={form.txn_date} onChange={(e) => setForm({ ...form, txn_date: e.target.value })} /></label>
              <label className="block text-xs text-ink-3">Amount ({t.currency ?? "—"})<input inputMode="decimal" className={inputClass} value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} /></label>
              <label className="block text-xs text-ink-3">Type
                <select className={inputClass} value={form.txn_type} onChange={(e) => setForm({ ...form, txn_type: e.target.value })}>
                  <option value="debit">Debit (purchase/charge)</option>
                  <option value="credit">Credit (payment/refund)</option>
                </select>
              </label>
              <label className="block text-xs text-ink-3">Category
                <select className={inputClass} value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                  {categories.map((c) => <option key={c}>{c}</option>)}
                </select>
              </label>
              <p className="text-xs text-ink-3">Corrections mark the row as verified by you. The original extracted text is kept below.</p>
            </div>
          ) : (
            <>
              <Row label="Date">{t.txn_date ? fmtDate(t.txn_date, "long") : <span className="text-warning">Not readable</span>}{t.posting_date && <span className="text-ink-3"> · posted {fmtDate(t.posting_date)}</span>}</Row>
              <Row label="Merchant">{t.merchant ?? <span className="text-warning">Not determined</span>}</Row>
              <Row label="Amount">
                <span className="tabular">{t.amount === null ? <span className="text-warning">Not readable</span> : money(t.amount, t.currency)}</span>{" "}
                <Badge>{t.txn_type === "credit" ? "Credit" : "Debit"}</Badge>
              </Row>
              {t.original_currency && <Row label="Original amount"><span className="tabular">{money(t.original_amount, t.original_currency)}</span></Row>}
              <Row label="Category">{t.category} <span className="text-xs text-ink-3">({t.category_source})</span></Row>
              <Row label="Confidence"><span className="tabular">{Math.round(t.confidence * 100)}%</span></Row>
              <Row label="Source">{t.page_number ? `Page ${t.page_number} · ` : ""}{METHOD_LABELS[t.source_method] ?? t.source_method}</Row>
              {(t.uncertain_fields.length > 0 || t.flags.length > 0) && (
                <Row label="Needs checking">
                  <div className="flex flex-wrap gap-1">
                    {t.uncertain_fields.map((f) => <Badge key={f} tone="warning">Uncertain {FIELD_LABELS[f] ?? f}</Badge>)}
                    {t.flags.map((f) => <Badge key={f} tone="warning">{FLAG_LABELS[f] ?? f}</Badge>)}
                  </div>
                </Row>
              )}
            </>
          )}
        </dl>
        <div>
          <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-ink-3"><FileText className="size-3.5" /> Original extracted text</div>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-bg p-3 font-mono text-xs text-ink-2">{t.raw_text || "—"}</pre>
          <p className="mt-2 text-xs text-ink-3">This is exactly what was read from the document; compare it with the original on page {t.page_number ?? "?"}.</p>
        </div>
      </div>
    </Modal>
  );
}
