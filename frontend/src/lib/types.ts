// Mirrors backend/app/schemas. Money values are decimal strings (exact), never floats.

export type StatementStatus = "uploaded" | "extracting" | "structuring" | "verifying" | "ready" | "failed";

export interface SessionInfo {
  retention_days: number;
  allowed_retention_days: number[];
  statement_count: number;
  vision_enabled: boolean;
  llm_enabled: boolean;
  vision_model: string | null;
  text_model: string | null;
  max_upload_mb: number;
  max_pdf_pages: number;
  created_at: string;
}

export interface DocumentInfo {
  original_filename: string;
  mime_type: string;
  kind: "pdf" | "image";
  size_bytes: number;
  page_count: number | null;
}

export interface CurrencyTotals {
  debits: string;
  credits: string;
  debit_count: number;
  credit_count: number;
}

export interface Statement {
  id: string;
  display_name: string;
  status: StatementStatus;
  is_demo: boolean;
  issuer: string | null;
  statement_currency: string | null;
  period_start: string | null;
  period_end: string | null;
  error_message: string | null;
  extraction_methods: string[];
  created_at: string;
  updated_at: string;
  expires_at: string | null;
  document: DocumentInfo | null;
  transaction_count: number;
  needs_review_count: number;
  totals: Record<string, CurrencyTotals>;
}

export interface VerificationCheck {
  name: string;
  label: string;
  status: "pass" | "warn" | "fail";
  message: string;
  expected?: string;
  actual?: string;
  currency?: string | null;
}

export interface StatementDetail extends Statement {
  detected_totals: Record<string, string>;
  verification: {
    checks?: VerificationCheck[];
    needs_review?: number;
    possible_duplicates?: number;
    merged_extraction_duplicates?: number;
  };
}

export interface ExtractedPage {
  page_number: number;
  method: string;
  text: string;
  tables: string[][][];
  char_count: number;
  is_scanned: boolean;
  notes: string[];
}

export interface ProcessingEvent {
  id: number;
  stage: string;
  level: "info" | "warning" | "error";
  message: string;
  data: Record<string, unknown>;
  created_at: string;
}

export interface Transaction {
  id: string;
  statement_id: string;
  seq: number;
  txn_date: string | null;
  posting_date: string | null;
  merchant: string | null;
  description: string;
  amount: string | null;
  currency: string | null;
  original_amount: string | null;
  original_currency: string | null;
  txn_type: "debit" | "credit";
  category: string;
  category_source: string;
  page_number: number | null;
  source_method: string;
  raw_text: string;
  confidence: number;
  uncertain_fields: string[];
  flags: string[];
  review_status: "ok" | "needs_review" | "user_verified";
  excluded: boolean;
  duplicate_of_id: string | null;
}

export interface TransactionPage {
  items: Transaction[];
  total: number;
  /** Backend-computed sums over the whole filtered set, per billing currency. */
  totals: Record<string, CurrencyTotals>;
  limit: number;
  offset: number;
}

export interface GroupRow {
  label: string;
  total: string;
  count: number;
  ids: string[];
}

export interface Analytics {
  currency: string | null;
  currencies: string[];
  totals: Record<string, CurrencyTotals>;
  transaction_count: number;
  needs_review: number;
  date_range: [string, string] | null;
  by_category: GroupRow[];
  by_month: GroupRow[];
  by_day: GroupRow[];
  top_merchants: GroupRow[];
  distribution: { label: string; min: string; max: string | null; count: number; total: string }[];
  currency_breakdown: { currency: string; count: number; original_total: string; billed: Record<string, string> }[];
  statement_ids: string[];
}

export interface AnswerTableColumn {
  key: string;
  label: string;
  type: "text" | "money" | "number" | "date";
}

export interface AnswerResult {
  kind: string;
  headline: string;
  metrics: { label: string; value: string | number; currency: string | null }[];
  table: {
    columns: AnswerTableColumn[];
    rows: Record<string, unknown>[];
    total_rows: number;
    truncated: boolean;
  } | null;
  transaction_ids: string[];
  transaction_count: number;
  filters: string[];
  notes: string[];
  warnings: string[];
  not_found: boolean;
  scope: { statements?: string[]; date_range?: [string, string] | null; demo?: boolean };
  computed_by: string;
}

export interface QuerySpec {
  intent: string;
  source: "rules" | "llm";
  notes: string[];
  [key: string]: unknown;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  query_spec: QuerySpec | null;
  result: AnswerResult | null;
  transaction_ids: string[];
  status: "complete" | "error";
  created_at: string;
}

export interface Conversation {
  id: string;
  statement_id: string | null;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[];
}

export interface AuditEntry {
  id: number;
  action: string;
  statement_id: string | null;
  detail: Record<string, unknown>;
  created_at: string;
}
