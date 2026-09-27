"use client";

import { Check, Clock, Cpu, Eye, FileLock2, KeyRound, ServerCog, ShieldCheck, Trash2 } from "lucide-react";
import { useState } from "react";

import { Badge, Button, Card, CardHeader, ConfirmDialog, ErrorBanner, PageHeader, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { useAsync } from "@/lib/hooks";

const ACTIONS: Record<string, string> = {
  "statement.upload": "Statement uploaded",
  "statement.delete": "Statement deleted",
  "session.delete_all": "All data deleted",
  "session.retention": "Retention changed",
  "transaction.edit": "Transaction corrected",
};

export default function SettingsPage() {
  const info = useAsync(() => api.sessionInfo(), []);
  const audit = useAsync(() => api.audit(), []);
  const [saving, setSaving] = useState<number | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const s = info.data;

  async function setRetention(days: number) {
    setSaving(days);
    setError(null);
    try {
      const updated = await api.setRetention(days);
      info.setData(() => updated);
      setMessage(`Retention set to ${days} day${days === 1 ? "" : "s"}. Existing statements were rescheduled.`);
      audit.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update retention");
    } finally {
      setSaving(null);
    }
  }

  async function deleteAll() {
    setDeleting(true);
    try {
      const r = await api.deleteAll();
      setMessage(`Deleted ${r.deleted_statements} statement${r.deleted_statements === 1 ? "" : "s"} and all derived data.`);
      setConfirmAll(false);
      info.reload();
      audit.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Deletion failed");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <>
      <PageHeader title="Privacy & settings" description="Control how long your financial documents are kept, and see exactly what happens to them." />
      {message && <div className="mb-4 flex items-center gap-2 rounded-lg border border-good/30 bg-good/5 px-4 py-3 text-sm text-[#8fe08f]"><Check className="size-4" /> {message}</div>}
      {error && <div className="mb-4"><ErrorBanner message={error} /></div>}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Data retention" subtitle="Statements and everything extracted from them are deleted automatically" />
          <div className="p-5">
            {!s ? (
              <Spinner />
            ) : (
              <>
                <div className="grid grid-cols-4 gap-2">
                  {s.allowed_retention_days.map((d) => (
                    <button
                      key={d}
                      onClick={() => setRetention(d)}
                      disabled={saving !== null}
                      className={`flex h-16 flex-col items-center justify-center rounded-lg border text-sm transition-colors ${s.retention_days === d ? "border-accent bg-accent/10 text-ink" : "border-line-strong bg-surface-2 text-ink-2 hover:border-accent/50"}`}
                    >
                      {saving === d ? <Spinner /> : <span className="text-lg font-semibold">{d}</span>}
                      <span className="text-[11px] text-ink-3">day{d === 1 ? "" : "s"}</span>
                    </button>
                  ))}
                </div>
                <p className="mt-3 flex items-start gap-1.5 text-xs text-ink-3"><Clock className="mt-px size-3.5 shrink-0" /> A background job removes expired files, pages, transactions and related chats. Changing this applies to statements you already uploaded.</p>
              </>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="Delete everything" subtitle="Remove all statements, extracted data and conversations in this workspace" />
          <div className="p-5">
            <p className="mb-4 text-sm text-ink-2">
              This deletes {s ? <strong className="text-ink">{s.statement_count} statement{s.statement_count === 1 ? "" : "s"}</strong> : "your statements"} and every file,
              page, transaction and chat message derived from them. A content-free audit entry records that the deletion happened.
            </p>
            <Button variant="danger" icon={<Trash2 className="size-4" />} onClick={() => setConfirmAll(true)} disabled={!s || s.statement_count === 0}>Delete all my data</Button>
          </div>
        </Card>

        <Card>
          <CardHeader title="How your documents are protected" />
          <ul className="space-y-3 p-5 text-sm text-ink-2">
            <li className="flex gap-3"><FileLock2 className="mt-0.5 size-4 shrink-0 text-good" /><span>Files are validated by content signature and size, stored outside the web root under random identifiers, and never executed. Original filenames are only used for display.</span></li>
            <li className="flex gap-3"><Eye className="mt-0.5 size-4 shrink-0 text-good" /><span>Your workspace is tied to a private, HTTP-only session cookie. Other sessions cannot see or even detect your statements.</span></li>
            <li className="flex gap-3"><KeyRound className="mt-0.5 size-4 shrink-0 text-good" /><span>API keys live only on the server. The browser never talks to Groq directly.</span></li>
            <li className="flex gap-3"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-good" /><span>Statement contents are never written to logs; processing events contain only counts and stages.</span></li>
            <li className="flex gap-3"><ServerCog className="mt-0.5 size-4 shrink-0 text-good" /><span>No bank or payment-account connections. SpendLens only works from documents you upload.</span></li>
          </ul>
        </Card>

        <Card>
          <CardHeader title="AI processing" subtitle="What is sent to external models" />
          <div className="space-y-3 p-5 text-sm">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-2 text-ink-2"><Cpu className="size-4" /> Document extraction (Docling)</span>
              <Badge tone="good">Runs on this server</Badge>
            </div>
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-2 text-ink-2"><Eye className="size-4" /> Vision fallback</span>
              {s?.vision_enabled ? <Badge tone="accent">{s.vision_model}</Badge> : <Badge tone="warning">Not configured</Badge>}
            </div>
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-2 text-ink-2"><Cpu className="size-4" /> Question interpretation</span>
              {s?.llm_enabled ? <Badge tone="accent">{s.text_model}</Badge> : <Badge>Built-in rule parser</Badge>}
            </div>
            <p className="pt-1 text-xs leading-relaxed text-ink-3">
              When Groq is configured, only page images that can&apos;t be read as text are sent to the vision model. For questions, the model receives your
              question plus merchant names, categories and the date range — never the whole statement — and it never computes figures.
            </p>
          </div>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader title="Activity log" subtitle="Privacy-relevant actions in this workspace (no document contents are recorded)" />
          <div className="p-5">
            {audit.loading && !audit.data ? (
              <Spinner />
            ) : !audit.data?.length ? (
              <p className="text-sm text-ink-3">No activity yet.</p>
            ) : (
              <ul className="divide-y divide-line/60 text-sm">
                {audit.data.map((e) => (
                  <li key={e.id} className="flex items-center justify-between py-2">
                    <span className="text-ink-2">{ACTIONS[e.action] ?? e.action}{e.detail?.reason === "retention_expired" ? " (retention expiry)" : ""}{e.detail?.demo ? " · demo" : ""}</span>
                    <span className="text-xs tabular text-ink-3">{fmtDateTime(e.created_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Card>
      </div>

      <ConfirmDialog
        open={confirmAll}
        title="Delete all data?"
        confirmLabel="Delete everything"
        busy={deleting}
        onCancel={() => setConfirmAll(false)}
        onConfirm={deleteAll}
        body="Every statement, stored file, extracted page, transaction and conversation in this workspace will be permanently deleted. This cannot be undone."
      />
    </>
  );
}
