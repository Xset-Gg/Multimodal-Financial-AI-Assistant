const THREE = new Set(["KWD", "BHD", "OMR", "JOD", "TND"]);
const ZERO = new Set(["JPY", "KRW", "VND", "CLP", "IDR"]);

export function decimals(currency: string | null | undefined): number {
  if (!currency) return 2;
  return THREE.has(currency) ? 3 : ZERO.has(currency) ? 0 : 2;
}

/** Format an exact decimal string for display. Never used for arithmetic. */
export function money(value: string | number | null | undefined, currency?: string | null, opts: { sign?: boolean } = {}): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return String(value);
  const d = decimals(currency);
  const s = n.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  const prefix = opts.sign && n > 0 ? "+" : "";
  return currency ? `${prefix}${s} ${currency}` : `${prefix}${s}`;
}

export function compactMoney(value: number, currency?: string | null): string {
  const s = value.toLocaleString("en-US", { notation: "compact", maximumFractionDigits: 1 });
  return currency ? `${s} ${currency}` : s;
}

export function fmtDate(iso: string | null | undefined, style: "short" | "long" = "short"): string {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", style === "short" ? { day: "2-digit", month: "short", year: "numeric" } : { day: "numeric", month: "long", year: "numeric" });
}

export function fmtDateTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function relativeTime(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function daysUntil(iso: string | null): number | null {
  if (!iso) return null;
  return Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / 86400000));
}

export const FIELD_LABELS: Record<string, string> = {
  txn_date: "date",
  amount: "amount",
  currency: "currency",
  merchant: "merchant",
  original_amount: "foreign amount",
  original_currency: "foreign currency",
};

export const FLAG_LABELS: Record<string, string> = {
  ambiguous_day_month_order: "Day/month order ambiguous",
  inferred_year: "Year inferred",
  missing_year: "Year missing",
  impossible_date: "Impossible date",
  unparseable_date: "Date unreadable",
  missing_date: "Date missing",
  missing_amount: "Amount missing",
  malformed_amount: "Amount malformed",
  unusual_decimal_places: "Unusual decimals",
  possible_duplicate: "Possible duplicate",
  future_date: "Future date",
  date_outside_statement_period: "Outside statement period",
  zero_amount: "Zero amount",
  unusually_large_amount: "Unusually large",
  amount_not_found_in_text_layer: "Amount not in PDF text",
  original_amount_mismatch: "Foreign amount mismatch",
};

export const METHOD_LABELS: Record<string, string> = {
  docling: "Docling",
  docling_table: "Docling · table",
  docling_text: "Docling · text",
  pdf_text: "PDF text layer",
  vision: "Groq vision",
  "docling+vision": "Docling + vision",
};
