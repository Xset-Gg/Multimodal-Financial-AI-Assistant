import { ArrowRight, Calculator, Eye, FileSearch, ListChecks, Lock, MessageSquareText, ScanLine, ShieldCheck, Table2 } from "lucide-react";
import Link from "next/link";

import { Logo } from "@/components/Logo";

const PIPELINE = [
  { icon: ShieldCheck, title: "Validate", body: "Content-signature, size and page checks before anything is stored." },
  { icon: FileSearch, title: "Docling", body: "Layout analysis and table-structure recognition for text PDFs." },
  { icon: ScanLine, title: "Vision fallback", body: "Scanned pages and photos are transcribed by a Groq vision model." },
  { icon: ListChecks, title: "Normalize & verify", body: "One schema, uncertain fields flagged, duplicates detected, totals reconciled." },
  { icon: Calculator, title: "Deterministic answers", body: "Questions become queries; totals are computed from stored rows." },
];

export default function Landing() {
  return (
    <div className="relative min-h-screen overflow-hidden">
      <div className="bg-grid pointer-events-none absolute inset-0 [mask-image:radial-gradient(ellipse_at_top,black_30%,transparent_75%)]" />
      <header className="relative mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
        <Logo />
        <nav className="flex items-center gap-2 text-sm">
          <Link href="/settings" className="hidden px-3 py-2 text-ink-3 hover:text-ink sm:block">Privacy</Link>
          <Link href="/dashboard" className="rounded-lg border border-line-strong bg-surface-2 px-4 py-2 font-medium hover:bg-surface-3">Open app</Link>
        </nav>
      </header>

      <main className="relative mx-auto max-w-6xl px-6">
        <section className="pb-20 pt-16 text-center md:pt-24">
          <div className="mx-auto mb-6 inline-flex items-center gap-2 rounded-full border border-line-strong bg-surface px-3 py-1 text-xs text-ink-2">
            <Lock className="size-3.5 text-good" /> Works only from statements you upload — no bank connections
          </div>
          <h1 className="mx-auto max-w-3xl text-4xl font-semibold tracking-tight md:text-6xl">
            Ask your credit-card statement <span className="text-accent-ink">anything.</span>
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-base text-ink-2 md:text-lg">
            SpendLens reads PDFs and photos of statements with Docling and vision AI, turns them into verified transactions,
            and answers questions with figures calculated from your data — each one traceable to the rows behind it.
          </p>
          <div className="mt-9 flex flex-wrap justify-center gap-3">
            <Link href="/upload" className="inline-flex h-11 items-center gap-2 rounded-lg bg-accent px-5 text-sm font-medium text-white hover:bg-[#4d95ea]">
              Upload a statement <ArrowRight className="size-4" />
            </Link>
            <Link href="/dashboard" className="inline-flex h-11 items-center gap-2 rounded-lg border border-line-strong bg-surface-2 px-5 text-sm font-medium hover:bg-surface-3">
              Try the demo statement
            </Link>
          </div>
        </section>

        <section className="mb-20 grid gap-4 md:grid-cols-2">
          <div className="rounded-2xl border border-line bg-surface p-6">
            <div className="mb-4 flex items-center justify-between">
              <span className="flex items-center gap-2 text-sm font-semibold"><MessageSquareText className="size-4 text-accent-ink" /> How an answer is built</span>
              <span className="text-[11px] text-ink-3">Illustration of the flow</span>
            </div>
            <ol className="space-y-3 text-sm">
              <li className="rounded-lg border border-line bg-surface-2 px-4 py-3"><span className="text-ink-3">You ask · </span>“How much did I spend at restaurants?”</li>
              <li className="rounded-lg border border-line bg-surface-2 px-4 py-3 font-mono text-xs text-ink-2">{`{ intent: "sum", categories: ["Dining"], txn_type: "debit" }`}</li>
              <li className="rounded-lg border border-line bg-surface-2 px-4 py-3 text-ink-2">PostgreSQL filters your extracted rows · the backend sums them per currency</li>
              <li className="rounded-lg border border-accent/40 bg-accent/10 px-4 py-3 text-ink">The answer, plus <span className="text-accent-ink">“View transactions used for this answer”</span></li>
            </ol>
          </div>
          <div className="grid gap-4">
            {[
              { icon: Eye, title: "See what the system understood", body: "The original document sits beside the extracted transactions, with the raw text each row came from." },
              { icon: ListChecks, title: "Honest about uncertainty", body: "Unreadable dates, ambiguous amounts and possible duplicates are flagged for your review — never silently invented." },
              { icon: Table2, title: "Explore and chart", body: "Filter by merchant, category, currency, amount and date. Charts use only extracted data." },
            ].map((f) => (
              <div key={f.title} className="flex gap-4 rounded-2xl border border-line bg-surface p-5">
                <f.icon className="mt-0.5 size-5 shrink-0 text-accent-ink" />
                <div>
                  <div className="text-sm font-semibold">{f.title}</div>
                  <p className="mt-1 text-sm text-ink-3">{f.body}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="mb-20">
          <h2 className="mb-6 text-center text-sm font-semibold uppercase tracking-widest text-ink-3">The pipeline</h2>
          <div className="grid gap-3 md:grid-cols-5">
            {PIPELINE.map((p, i) => (
              <div key={p.title} className="relative rounded-xl border border-line bg-surface p-4">
                <div className="mb-3 flex items-center justify-between">
                  <p.icon className="size-5 text-accent-ink" />
                  <span className="font-mono text-[11px] text-ink-3">0{i + 1}</span>
                </div>
                <div className="text-sm font-semibold">{p.title}</div>
                <p className="mt-1 text-xs leading-relaxed text-ink-3">{p.body}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mb-16 rounded-2xl border border-line bg-surface p-8 md:flex md:items-center md:justify-between">
          <div className="max-w-2xl">
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold"><ShieldCheck className="size-4 text-good" /> Built for sensitive documents</div>
            <p className="text-sm text-ink-3">
              Files are stored under random IDs outside the web root and auto-deleted on your schedule. Contents never reach logs. API keys stay server-side.
              Delete a statement — or everything — at any time.
            </p>
          </div>
          <Link href="/settings" className="mt-5 inline-flex items-center gap-1 text-sm text-accent-ink hover:underline md:mt-0">Privacy controls <ArrowRight className="size-4" /></Link>
        </section>
      </main>
      <footer className="relative border-t border-line py-6 text-center text-xs text-ink-3">SpendLens AI · Extraction can be imperfect; flagged rows should be verified against your statement.</footer>
    </div>
  );
}
