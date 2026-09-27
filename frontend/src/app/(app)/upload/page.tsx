"use client";

import clsx from "clsx";
import { ArrowRight, FileImage, FileText, RotateCcw, ShieldCheck, UploadCloud, X } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";

import { PrivacyNote } from "@/components/AppShell";
import { EventLog, StageTracker, StatusPill, lastStageBeforeFailure } from "@/components/Processing";
import { Badge, Button, Card, CardHeader, ErrorBanner, PageHeader } from "@/components/ui";
import { ApiError, api, uploadStatement } from "@/lib/api";
import { fmtBytes } from "@/lib/format";
import { useSession, useStatementStream } from "@/lib/hooks";
import type { Statement } from "@/lib/types";

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.webp,application/pdf,image/png,image/jpeg,image/webp";
const EXT = /\.(pdf|png|jpe?g|webp)$/i;

export default function UploadPage() {
  const session = useSession();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [validation, setValidation] = useState<string | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [statement, setStatement] = useState<Statement | null>(null);
  const abortRef = useRef<(() => void) | null>(null);
  const [attempt, setAttempt] = useState(0);
  const stream = useStatementStream(statement?.id ?? null, undefined, attempt);
  const maxMb = session?.max_upload_mb ?? 20;

  function choose(f: File | undefined | null) {
    setUploadError(null);
    setStatement(null);
    setProgress(null);
    if (!f) return;
    if (!EXT.test(f.name)) {
      setValidation("Unsupported file type. Upload a PDF, PNG, JPEG or WEBP statement.");
      setFile(null);
      return;
    }
    if (f.size === 0) {
      setValidation("That file is empty.");
      setFile(null);
      return;
    }
    if (f.size > maxMb * 1024 * 1024) {
      setValidation(`That file is ${fmtBytes(f.size)}; the limit is ${maxMb} MB.`);
      setFile(null);
      return;
    }
    setValidation(null);
    setFile(f);
  }

  async function start() {
    if (!file) return;
    setUploadError(null);
    setProgress(0);
    const { promise, abort } = uploadStatement(file, setProgress);
    abortRef.current = abort;
    try {
      setStatement(await promise);
    } catch (e) {
      setUploadError(e instanceof ApiError ? e.message : "Upload failed");
      setProgress(null);
    } finally {
      abortRef.current = null;
    }
  }

  async function retry() {
    if (!statement) return;
    const s = await api.retry(statement.id);
    setStatement({ ...s });
    setAttempt((n) => n + 1);
  }

  function reset() {
    setFile(null);
    setStatement(null);
    setProgress(null);
    setUploadError(null);
  }

  const status = stream.status;
  const uploading = progress !== null && !statement;
  const current = uploading ? "uploading" : statement ? (status ?? "uploaded") : null;
  const isImage = file?.type.startsWith("image/") || /\.(png|jpe?g|webp)$/i.test(file?.name ?? "");

  return (
    <>
      <PageHeader title="Upload a statement" description="PDF statements are read with Docling; images and scanned pages go to the Groq vision model." />

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {!statement && (
            <Card>
              <div
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragging(false);
                  choose(e.dataTransfer.files?.[0]);
                }}
                className={clsx(
                  "m-4 flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-14 text-center transition-colors",
                  dragging ? "border-accent bg-accent/5" : "border-line-strong",
                )}
              >
                <div className="mb-4 rounded-2xl border border-line bg-surface-2 p-4"><UploadCloud className="size-7 text-accent-ink" /></div>
                <p className="text-sm font-medium">Drag and drop your statement here</p>
                <p className="mt-1 text-xs text-ink-3">PDF, PNG, JPEG or WEBP · up to {maxMb} MB · up to {session?.max_pdf_pages ?? 60} pages</p>
                <Button className="mt-5" onClick={() => inputRef.current?.click()} disabled={uploading}>Choose file</Button>
                <input ref={inputRef} type="file" accept={ACCEPT} className="hidden" onChange={(e) => choose(e.target.files?.[0])} />
              </div>
              {validation && <div className="mx-4 mb-4"><ErrorBanner message={validation} /></div>}
              {file && (
                <div className="mx-4 mb-4 flex items-center gap-3 rounded-lg border border-line bg-surface-2 px-4 py-3">
                  {isImage ? <FileImage className="size-5 text-ink-3" /> : <FileText className="size-5 text-ink-3" />}
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{file.name}</div>
                    <div className="text-xs text-ink-3">{fmtBytes(file.size)} · {isImage ? "image → vision pipeline" : "PDF → Docling pipeline"}</div>
                  </div>
                  {uploading ? (
                    <Button size="sm" variant="ghost" onClick={() => abortRef.current?.()}>Cancel</Button>
                  ) : (
                    <>
                      <button onClick={reset} className="rounded-md p-1.5 text-ink-3 hover:bg-surface-3" aria-label="Remove file"><X className="size-4" /></button>
                      <Button variant="primary" onClick={start}>Upload & analyze</Button>
                    </>
                  )}
                </div>
              )}
              {isImage && session && !session.vision_enabled && file && (
                <div className="mx-4 mb-4 rounded-lg border border-warning/40 bg-warning/10 px-4 py-3 text-xs text-[#fbd27a]">
                  Image uploads need the Groq vision model, but no GROQ_API_KEY is configured on the server. This upload will fail until it is set.
                </div>
              )}
              {uploadError && <div className="mx-4 mb-4"><ErrorBanner message={uploadError} onRetry={start} /></div>}
            </Card>
          )}

          {current && (
            <Card>
              <CardHeader
                title={statement ? statement.display_name : file?.name}
                subtitle={statement ? "Live status from the analysis worker" : "Uploading over an encrypted connection"}
                action={statement && <StatusPill status={status ?? "uploaded"} />}
              />
              <div className="space-y-5 p-5">
                <StageTracker current={current} failedAt={status === "failed" ? lastStageBeforeFailure(stream.events) : null} uploadFraction={progress ?? 0} />
                {status === "failed" && (
                  <ErrorBanner message={stream.error ?? "Processing failed."} onRetry={retry} />
                )}
                {status === "ready" && (
                  <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-good/30 bg-good/5 px-4 py-3">
                    <div className="text-sm">
                      <span className="font-medium text-ink">{stream.transactionCount} transactions extracted.</span>{" "}
                      <span className="text-ink-3">Review the flagged ones against the original document.</span>
                    </div>
                    <div className="flex gap-2">
                      <Button variant="ghost" icon={<RotateCcw className="size-4" />} onClick={reset}>Upload another</Button>
                      <Link href={`/statements/${statement!.id}`}><Button variant="primary">Open workspace <ArrowRight className="size-4" /></Button></Link>
                    </div>
                  </div>
                )}
                {statement && (
                  <div>
                    <div className="mb-2 flex items-center justify-between text-xs font-medium text-ink-3">
                      Processing log
                      {!stream.connected && status !== "ready" && status !== "failed" && <span className="text-warning">Reconnecting…</span>}
                    </div>
                    <div className="rounded-lg border border-line bg-bg p-3"><EventLog events={stream.events} /></div>
                  </div>
                )}
              </div>
            </Card>
          )}
        </div>

        <div className="space-y-4">
          <Card className="p-5">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold"><ShieldCheck className="size-4 text-good" /> How your file is handled</div>
            <ul className="space-y-2.5 text-xs leading-relaxed text-ink-2">
              <li>File type is verified from its content (not the extension) and size-limited before anything is stored.</li>
              <li>It is saved under a random internal ID, outside any web-served folder, and never executed.</li>
              <li>Text PDFs are processed locally with Docling. Only image-based pages are sent to the Groq vision model.</li>
              <li>Statement contents are never written to logs.</li>
              <li>Auto-deleted after {session?.retention_days ?? "…"} days — or delete it any time.</li>
            </ul>
          </Card>
          <Card className="p-5">
            <div className="mb-2 text-sm font-semibold">Pipeline</div>
            <div className="flex flex-wrap gap-1.5 text-[11px]">
              {["Validation", "Docling", "Vision fallback", "Parsing", "Normalization", "Validation checks", "PostgreSQL", "Q&A"].map((s) => <Badge key={s}>{s}</Badge>)}
            </div>
            <p className="mt-3 text-xs text-ink-3">Extraction can be imperfect. Uncertain fields are flagged for review rather than guessed.</p>
          </Card>
          <PrivacyNote />
        </div>
      </div>
    </>
  );
}
