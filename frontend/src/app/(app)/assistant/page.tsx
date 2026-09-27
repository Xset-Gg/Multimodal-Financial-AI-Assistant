"use client";

import { MessageSquareText } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ChatPanel } from "@/components/ChatPanel";
import { Badge, Button, Card, EmptyState, PageHeader, Spinner, inputClass } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useAsync, useSession } from "@/lib/hooks";

export default function AssistantPage() {
  const session = useSession();
  const statements = useAsync(() => api.statements(), []);
  const ready = (statements.data ?? []).filter((s) => s.status === "ready");
  const [selected, setSelected] = useState<string>("all");
  const current = ready.find((s) => s.id === selected);

  return (
    <>
      <PageHeader
        title="AI assistant"
        description="Ask questions in plain language. The model interprets the question; the numbers are computed by deterministic backend logic."
        actions={
          ready.length > 0 && (
            <select className={`${inputClass} w-72`} value={selected} onChange={(e) => setSelected(e.target.value)} aria-label="Statement scope">
              <option value="all">All processed statements</option>
              {ready.map((s) => <option key={s.id} value={s.id}>{s.display_name}</option>)}
            </select>
          )
        }
      />
      {statements.loading && !statements.data ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : ready.length === 0 ? (
        <Card>
          <EmptyState
            icon={<MessageSquareText className="size-6" />}
            title="Nothing to ask about yet"
            body="The assistant only answers from statements you've uploaded and processed. Upload one or load the demo first."
            action={<Link href="/dashboard"><Button variant="primary">Go to dashboard</Button></Link>}
          />
        </Card>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[1fr_300px]">
          <Card className="flex h-[calc(100vh-190px)] min-h-[520px] flex-col overflow-hidden">
            <ChatPanel key={selected} statementId={selected === "all" ? null : selected} className="min-h-0 flex-1" />
          </Card>
          <div className="space-y-4">
            <Card className="p-4 text-sm">
              <div className="mb-2 font-semibold">Scope</div>
              {current ? (
                <div className="space-y-1 text-xs text-ink-2">
                  <div className="font-medium text-ink">{current.display_name}</div>
                  {current.is_demo && <Badge tone="demo">Demo data</Badge>}
                  <div>{current.transaction_count} transactions · {fmtDate(current.period_start)} – {fmtDate(current.period_end)}</div>
                </div>
              ) : (
                <div className="text-xs text-ink-2">{ready.length} statement{ready.length === 1 ? "" : "s"} · {ready.reduce((n, s) => n + s.transaction_count, 0)} transactions</div>
              )}
            </Card>
            <Card className="p-4 text-xs leading-relaxed text-ink-2">
              <div className="mb-2 text-sm font-semibold text-ink">How answers are produced</div>
              <ol className="list-decimal space-y-1.5 pl-4">
                <li>{session?.llm_enabled ? `The Groq model (${session.text_model}) turns your question into a structured query.` : "A built-in parser turns your question into a structured query (Groq LLM not configured)."}</li>
                <li>The backend filters your stored transactions and calculates totals exactly.</li>
                <li>The explanation may only restate those computed figures — any other number is rejected.</li>
                <li>Every answer links to the exact transactions it used.</li>
              </ol>
              <p className="mt-3 text-ink-3">If the statement doesn&apos;t contain the information, the assistant says so instead of guessing.</p>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
