"use client";

import clsx from "clsx";
import { AlertTriangle, ArrowUp, Calculator, Info, ListTree, PanelRightClose, Plus, RotateCcw, Sparkles } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { fmtDate, money } from "@/lib/format";
import { openSse, readSse } from "@/lib/sse";
import type { AnswerResult, ChatMessage, QuerySpec, Transaction } from "@/lib/types";

import { TransactionTable } from "./Transactions";
import { Badge, Button, ErrorBanner, Modal, Spinner } from "./ui";

export const EXAMPLE_QUESTIONS = [
  "How much did I spend this month?",
  "What were my largest transactions?",
  "How much did I spend at restaurants?",
  "Show transactions above 500 QAR",
  "What did I spend on September 15?",
  "Which merchant did I spend the most with?",
  "How many transactions were made in USD?",
  "Spending by category",
];

interface LiveMessage extends ChatMessage {
  pending?: boolean;
  stage?: string;
  retryOf?: string;
}

function renderCell(value: unknown, type: string, row: Record<string, unknown>, key: string, compact?: boolean): React.ReactNode {
  if (value === null || value === undefined || value === "") return <span className="text-ink-3">—</span>;
  if (type === "money") {
    const cur = (key === "amount" ? row.currency : row.currency) as string | null;
    const sign = row.type === "credit" ? "+" : "";
    return (
      <span className={clsx("tabular", row.type === "credit" && "text-[#4fd14f]")}>
        {sign}
        {money(value as string, cur)}
        {key === "amount" && !compact && typeof row.original_currency === "string" && (
          <span className="ml-1 text-[11px] text-ink-3">({money(row.original_amount as string, row.original_currency)})</span>
        )}
      </span>
    );
  }
  if (type === "date")
    return <span className="tabular">{compact ? new Date(`${value as string}T00:00:00`).toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) : fmtDate(value as string)}</span>;
  return String(value);
}

function ResultCard({ result, spec, onShowTransactions, compact }: { result: AnswerResult; spec: QuerySpec | null; onShowTransactions: () => void; compact?: boolean }) {
  // In the narrow side panel keep only the columns that identify a row.
  const columns = (result.table?.columns ?? []).filter((c) => !compact || ["date", "merchant", "amount", "label", "total", "count"].includes(c.key));
  const maxRows = compact ? 6 : 12;
  const metrics = result.metrics.filter((m) => m.value !== null && m.value !== undefined);
  return (
    <div className="mt-3 space-y-3">
      {metrics.length > 0 && !result.not_found && (
        <div className="flex flex-wrap gap-2">
          {metrics.slice(0, 4).map((m, i) => (
            <div key={i} className="min-w-[120px] rounded-lg border border-line bg-bg px-3 py-2">
              <div className="truncate text-[11px] text-ink-3">{m.label}</div>
              <div className="tabular text-base font-semibold">{typeof m.value === "number" && !m.currency ? m.value : money(m.value, m.currency)}</div>
            </div>
          ))}
        </div>
      )}
      {result.table && result.table.rows.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-line">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-line bg-surface-2">
                {columns.map((c) => (
                  <th key={c.key} className={clsx("px-2.5 py-1.5 font-medium text-ink-3", c.type === "money" || c.type === "number" ? "text-right" : "text-left")}>
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.table.rows.slice(0, maxRows).map((row, i) => (
                <tr key={i} className="border-b border-line/50 last:border-0">
                  {columns.map((c) => (
                    <td key={c.key} className={clsx("px-2.5 py-1.5", c.type === "money" || c.type === "number" ? "whitespace-nowrap text-right tabular" : "text-left", c.type === "date" && "whitespace-nowrap", c.key === "merchant" && "max-w-[180px] truncate")}>
                      {renderCell(row[c.key], c.type, row, c.key, compact)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {(result.table.rows.length > maxRows || result.table.truncated) && (
            <div className="border-t border-line px-2.5 py-1.5 text-[11px] text-ink-3">
              Showing {Math.min(maxRows, result.table.rows.length)} of {result.table.total_rows} rows — open the transactions below for the full list.
            </div>
          )}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {result.transaction_ids.length > 0 && (
          <Button size="sm" variant="secondary" icon={<ListTree className="size-3.5" />} onClick={onShowTransactions}>
            View {result.transaction_count} transaction{result.transaction_count === 1 ? "" : "s"} used for this answer
          </Button>
        )}
        <Badge title="All figures are computed by deterministic backend logic from your stored transactions">
          <Calculator className="size-3" /> Calculated from your data
        </Badge>
        {spec && (
          <Badge title={spec.source === "llm" ? "The question was interpreted by the Groq LLM; the numbers were not." : "Interpreted by the built-in rule parser"}>
            <Sparkles className="size-3" /> {spec.source === "llm" ? "LLM-interpreted" : "Rule-interpreted"}
          </Badge>
        )}
        {result.scope.demo && <Badge tone="demo">Demo data</Badge>}
      </div>
      {result.filters.length > 0 && (
        <div className="text-[11px] text-ink-3">
          Filters: {result.filters.join(" · ")}
        </div>
      )}
    </div>
  );
}

export function ChatPanel({ statementId, className, compact, onClose }: { statementId: string | null; className?: string; compact?: boolean; onClose?: () => void }) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<LiveMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [drawer, setDrawer] = useState<{ ids: string[]; title: string } | null>(null);
  const [drawerRows, setDrawerRows] = useState<Transaction[] | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // The parent keys this component by statement, so state starts fresh for each scope.
    let alive = true;
    (async () => {
      try {
        const convs = await api.conversations(statementId ?? undefined);
        const match = convs.find((c) => (statementId ? c.statement_id === statementId : c.statement_id === null));
        if (match && alive) {
          const detail = await api.conversation(match.id);
          if (!alive) return;
          setConversationId(match.id);
          setMessages(detail.messages);
        }
        setLoadError(null);
      } catch (e) {
        if (alive) setLoadError(e instanceof Error ? e.message : "Could not load conversation");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [statementId]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  function openDrawer(ids: string[], title: string) {
    setDrawer({ ids, title });
    setDrawerRows(null);
    api.lookup(ids).then(setDrawerRows).catch(() => setDrawerRows([]));
  }

  const send = useCallback(
    async (question: string) => {
      const q = question.trim();
      if (!q || busy) return;
      setBusy(true);
      setInput("");
      const tempUser: LiveMessage = { id: `u-${Date.now()}`, role: "user", content: q, query_spec: null, result: null, transaction_ids: [], status: "complete", created_at: new Date().toISOString() };
      const tempId = `a-${Date.now()}`;
      const tempAssistant: LiveMessage = { id: tempId, role: "assistant", content: "", query_spec: null, result: null, transaction_ids: [], status: "complete", created_at: new Date().toISOString(), pending: true, stage: "Sending" };
      setMessages((m) => [...m, tempUser, tempAssistant]);
      const update = (patch: Partial<LiveMessage>) => setMessages((m) => m.map((x) => (x.id === tempId ? { ...x, ...patch } : x)));
      try {
        await api.session();
        let convId = conversationId;
        if (!convId) {
          convId = (await api.createConversation(statementId)).id;
          setConversationId(convId);
        }
        const res = await openSse(`/api/conversations/${convId}/messages`, { method: "POST", body: JSON.stringify({ content: q }) });
        let text = "";
        for await (const ev of readSse(res)) {
          if (ev.event === "status") update({ stage: (ev.data as { message: string }).message });
          else if (ev.event === "interpretation") update({ query_spec: ev.data as QuerySpec });
          else if (ev.event === "result") update({ result: ev.data as AnswerResult });
          else if (ev.event === "token") {
            text += (ev.data as { text: string }).text;
            update({ content: text });
          } else if (ev.event === "final") {
            const d = ev.data as { message_id: string; content: string; result: AnswerResult; query_spec: QuerySpec };
            // The server-validated text replaces the streamed draft (it may differ if the draft failed the grounding check).
            update({ id: d.message_id, content: d.content, result: d.result, query_spec: d.query_spec, pending: false, stage: undefined });
          } else if (ev.event === "error") {
            update({ content: (ev.data as { message: string }).message, status: "error", pending: false, retryOf: q });
          }
        }
        setMessages((m) => m.map((x) => (x.id === tempId && x.pending ? { ...x, pending: false, status: "error", content: "The answer stream ended unexpectedly.", retryOf: q } : x)));
      } catch (e) {
        update({ pending: false, status: "error", content: e instanceof Error ? e.message : "Request failed", retryOf: q });
      } finally {
        setBusy(false);
      }
    },
    [busy, conversationId, statementId],
  );

  async function newConversation() {
    setConversationId(null);
    setMessages([]);
  }

  return (
    <div className={clsx("flex min-h-0 flex-col", className)}>
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <div>
          <div className="flex items-center gap-1.5 text-sm font-semibold"><Sparkles className="size-4 text-accent-ink" /> Ask about {statementId ? "this statement" : "all statements"}</div>
          <div className="text-[11px] text-ink-3">Answers are calculated from extracted transactions — never guessed.</div>
        </div>
        <div className="flex items-center gap-1">
          {messages.length > 0 && (
            <Button size="sm" variant="ghost" icon={<Plus className="size-3.5" />} onClick={newConversation} disabled={busy}>New</Button>
          )}
          {onClose && (
            <button onClick={onClose} className="rounded-md p-1.5 text-ink-3 hover:bg-surface-2" aria-label="Close assistant"><PanelRightClose className="size-4" /></button>
          )}
        </div>
      </div>

      <div ref={scroller} className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
        {loadError && <ErrorBanner message={loadError} />}
        {loading ? (
          <div className="flex justify-center py-10"><Spinner /></div>
        ) : messages.length === 0 ? (
          <div className="py-4">
            <p className="mb-3 text-sm text-ink-2">Try one of these:</p>
            <div className={clsx("grid gap-2", compact ? "grid-cols-1" : "sm:grid-cols-2")}>
              {EXAMPLE_QUESTIONS.map((q) => (
                <button key={q} onClick={() => send(q)} className="rounded-lg border border-line bg-surface-2 px-3 py-2 text-left text-sm text-ink-2 transition-colors hover:border-accent/50 hover:text-ink">
                  {q}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="flex justify-end">
                <div className="max-w-[85%] rounded-2xl rounded-br-md bg-accent/90 px-3.5 py-2 text-sm text-white">{m.content}</div>
              </div>
            ) : (
              <div key={m.id} className="max-w-full">
                <div className={clsx("rounded-2xl rounded-bl-md border px-4 py-3 text-sm", m.status === "error" ? "border-critical/40 bg-critical/10" : "border-line bg-surface-2")}>
                  {m.status === "error" ? (
                    <div className="flex items-start gap-2 text-[#f5b5b5]">
                      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                      <div className="flex-1">{m.content}</div>
                      {m.retryOf && <Button size="sm" variant="ghost" icon={<RotateCcw className="size-3.5" />} onClick={() => send(m.retryOf!)} disabled={busy}>Retry</Button>}
                    </div>
                  ) : (
                    <>
                      {m.content ? (
                        <div className="whitespace-pre-wrap leading-relaxed text-ink">{m.content}</div>
                      ) : (
                        m.pending && <div className="flex items-center gap-2 text-ink-3"><Spinner className="size-3.5" /> {m.stage ?? "Thinking"}…</div>
                      )}
                      {m.pending && m.content && <div className="mt-2 flex items-center gap-2 text-xs text-ink-3"><Spinner className="size-3" /> {m.stage}</div>}
                      {m.result && (
                        <ResultCard
                          compact={compact}
                          result={m.result}
                          spec={m.query_spec}
                          onShowTransactions={() => openDrawer(m.result!.transaction_ids, m.result!.headline)}
                        />
                      )}
                      {m.result?.not_found && (
                        <div className="mt-2 flex items-center gap-1.5 text-xs text-ink-3"><Info className="size-3.5" /> Nothing matching was found in the extracted data, so no figure is given.</div>
                      )}
                    </>
                  )}
                </div>
              </div>
            ),
          )
        )}
      </div>

      <form
        className="border-t border-line p-3"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <div className="flex items-end gap-2 rounded-xl border border-line-strong bg-surface-2 p-1.5 focus-within:border-accent">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
            rows={1}
            maxLength={1000}
            placeholder="Ask about spending, merchants, dates, amounts…"
            className="max-h-32 min-h-9 flex-1 resize-none bg-transparent px-2 py-2 text-sm placeholder:text-ink-3 focus:outline-none"
          />
          <Button type="submit" variant="primary" size="sm" className="h-9 w-9 px-0" disabled={!input.trim() || busy} loading={busy} aria-label="Send">
            {!busy && <ArrowUp className="size-4" />}
          </Button>
        </div>
      </form>

      <Modal open={!!drawer} onClose={() => setDrawer(null)} title="Transactions used for this answer" wide>
        {drawer && <p className="mb-3 text-sm text-ink-2">{drawer.title}</p>}
        {drawerRows === null ? (
          <div className="flex justify-center py-8"><Spinner /></div>
        ) : drawerRows.length === 0 ? (
          <p className="text-sm text-ink-3">These transactions are no longer available (they may have been deleted).</p>
        ) : (
          <div className="max-h-[60vh] overflow-auto rounded-lg border border-line">
            <TransactionTable items={drawerRows} dense />
          </div>
        )}
      </Modal>
    </div>
  );
}
