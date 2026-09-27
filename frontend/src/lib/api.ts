import type {
  Analytics,
  AuditEntry,
  Conversation,
  ConversationDetail,
  ExtractedPage,
  ProcessingEvent,
  SessionInfo,
  Statement,
  StatementDetail,
  Transaction,
  TransactionPage,
} from "./types";

// State-changing requests carry this header; the backend rejects mutations without it (CSRF defence).
const CLIENT_HEADER = { "x-spendlens-client": "1" };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let sessionPromise: Promise<SessionInfo> | null = null;

export function ensureSession(): Promise<SessionInfo> {
  if (!sessionPromise) {
    sessionPromise = rawRequest<SessionInfo>("/api/session", { method: "POST" }).catch((e) => {
      sessionPromise = null;
      throw e;
    });
  }
  return sessionPromise;
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return body.detail.map((d: { msg: string }) => d.msg).join("; ");
  } catch {
    /* not JSON */
  }
  return `Request failed (${res.status})`;
}

async function rawRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { ...CLIENT_HEADER, ...(init.body && !(init.body instanceof FormData) ? { "content-type": "application/json" } : {}), ...init.headers },
  });
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Request with transparent session bootstrap: a 401 creates the anonymous session and retries once. */
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  try {
    return await rawRequest<T>(path, init);
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      sessionPromise = null;
      await ensureSession();
      return rawRequest<T>(path, init);
    }
    throw e;
  }
}

export type TxnQuery = {
  statement_id?: string[];
  date_from?: string;
  date_to?: string;
  merchant?: string;
  category?: string[];
  currency?: string;
  min_amount?: string;
  max_amount?: string;
  txn_type?: "debit" | "credit";
  search?: string;
  review?: string;
  include_excluded?: boolean;
  sort?: string;
  direction?: "asc" | "desc";
  limit?: number;
  offset?: number;
};

export function toQuery(params: Record<string, unknown>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => q.append(k, String(x)));
    else q.append(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const api = {
  session: () => ensureSession(),
  sessionInfo: () => request<SessionInfo>("/api/session"),
  setRetention: (days: number) => request<SessionInfo>("/api/session/retention", { method: "PUT", body: JSON.stringify({ days }) }),
  deleteAll: () => request<{ deleted_statements: number }>("/api/session/data", { method: "DELETE" }),
  audit: () => request<AuditEntry[]>("/api/session/audit"),

  statements: () => request<Statement[]>("/api/statements"),
  statement: (id: string) => request<StatementDetail>(`/api/statements/${id}`),
  deleteStatement: (id: string) => request<void>(`/api/statements/${id}`, { method: "DELETE" }),
  retry: (id: string) => request<Statement>(`/api/statements/${id}/retry`, { method: "POST" }),
  loadDemo: () => request<Statement>("/api/statements/demo", { method: "POST" }),
  pages: (id: string) => request<ExtractedPage[]>(`/api/statements/${id}/pages`),
  events: (id: string) => request<ProcessingEvent[]>(`/api/statements/${id}/events`),
  fileUrl: (id: string) => `/api/statements/${id}/file`,

  transactions: (q: TxnQuery) => request<TransactionPage>(`/api/transactions${toQuery(q)}`),
  lookup: (ids: string[]) => request<Transaction[]>("/api/transactions/lookup", { method: "POST", body: JSON.stringify({ ids }) }),
  patchTransaction: (id: string, body: Record<string, unknown>) =>
    request<Transaction>(`/api/transactions/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  analytics: (q: { statement_id?: string[]; currency?: string; date_from?: string; date_to?: string }) =>
    request<Analytics>(`/api/analytics${toQuery(q)}`),
  categories: () => request<string[]>("/api/categories"),

  conversations: (statementId?: string) => request<Conversation[]>(`/api/conversations${toQuery({ statement_id: statementId })}`),
  conversation: (id: string) => request<ConversationDetail>(`/api/conversations/${id}`),
  createConversation: (statementId: string | null) =>
    request<Conversation>("/api/conversations", { method: "POST", body: JSON.stringify({ statement_id: statementId }) }),
  deleteConversation: (id: string) => request<void>(`/api/conversations/${id}`, { method: "DELETE" }),
};

/** Upload with real byte-level progress (fetch has no upload progress events). */
export function uploadStatement(file: File, onProgress: (fraction: number) => void): { promise: Promise<Statement>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const promise = ensureSession().then(
    () =>
      new Promise<Statement>((resolve, reject) => {
        xhr.open("POST", "/api/statements");
        xhr.setRequestHeader("x-spendlens-client", "1");
        xhr.withCredentials = true;
        xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
        xhr.onload = () => {
          let body: unknown = null;
          try {
            body = JSON.parse(xhr.responseText);
          } catch {
            /* ignore */
          }
          if (xhr.status >= 200 && xhr.status < 300) resolve(body as Statement);
          else {
            const detail = (body as { detail?: unknown } | null)?.detail;
            reject(new ApiError(xhr.status, typeof detail === "string" ? detail : `Upload failed (${xhr.status})`));
          }
        };
        xhr.onerror = () => reject(new ApiError(0, "Network error during upload"));
        xhr.onabort = () => reject(new ApiError(0, "Upload cancelled"));
        const form = new FormData();
        form.append("file", file);
        xhr.send(form);
      }),
  );
  return { promise, abort: () => xhr.abort() };
}
