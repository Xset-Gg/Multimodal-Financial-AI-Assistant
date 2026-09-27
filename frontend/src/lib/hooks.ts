"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import { openSse, readSse } from "./sse";
import type { ProcessingEvent, SessionInfo, StatementStatus } from "./types";

type Deps = (string | number | boolean | null | undefined)[];

/**
 * Load data for a set of primitive dependencies. Results are tagged with the request key that
 * produced them, so `loading` is derived (key mismatch) rather than toggled inside the effect,
 * and stale data stays visible while a refresh is in flight.
 */
export function useAsync<T>(fn: () => Promise<T>, deps: Deps) {
  const [tick, setTick] = useState(0);
  const key = JSON.stringify([...deps, tick]);
  const [state, setState] = useState<{ key: string | null; data: T | null; error: string | null }>({ key: null, data: null, error: null });

  useEffect(() => {
    let alive = true;
    fn()
      .then((d) => alive && setState({ key, data: d, error: null }))
      .catch((e: unknown) => alive && setState((s) => ({ key, data: s.data, error: e instanceof Error ? e.message : String(e) })));
    return () => {
      alive = false;
    };
    // `fn` is re-created every render; the request is keyed on its primitive inputs instead.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  const setData = useCallback((update: (d: T | null) => T | null) => setState((s) => ({ ...s, data: update(s.data) })), []);
  return { data: state.data, error: state.error, loading: state.key !== key, reload, setData };
}

let cachedSession: SessionInfo | null = null;

export function useSession() {
  const [session, setSession] = useState<SessionInfo | null>(cachedSession);
  useEffect(() => {
    api
      .session()
      .then((s) => {
        cachedSession = s;
        setSession(s);
      })
      .catch(() => undefined);
  }, []);
  return session;
}

export interface StreamState {
  status: StatementStatus | null;
  error: string | null;
  transactionCount: number;
  events: ProcessingEvent[];
  connected: boolean;
}

const INITIAL: StreamState = { status: null, error: null, transactionCount: 0, events: [], connected: false };

/** Live processing state for one statement, straight from the backend's SSE stream. */
export function useStatementStream(statementId: string | null, onDone?: (status: StatementStatus) => void, nonce = 0) {
  const key = `${statementId}:${nonce}`;
  const [tagged, setTagged] = useState<{ key: string; state: StreamState }>({ key: "", state: INITIAL });
  const doneRef = useRef(onDone);
  useEffect(() => {
    doneRef.current = onDone;
  });

  useEffect(() => {
    if (!statementId) return;
    const ctrl = new AbortController();
    let lastId = 0;
    let stopped = false;
    const update = (fn: (s: StreamState) => StreamState) =>
      setTagged((t) => ({ key, state: fn(t.key === key ? t.state : INITIAL) }));

    async function connect(attempt: number): Promise<void> {
      try {
        await api.session();
        const res = await openSse(`/api/statements/${statementId}/stream`, {
          signal: ctrl.signal,
          headers: lastId ? { "last-event-id": String(lastId) } : {},
        });
        update((s) => ({ ...s, connected: true }));
        for await (const ev of readSse(res, ctrl.signal)) {
          if (ev.event === "progress") {
            const e = ev.data as ProcessingEvent;
            lastId = Math.max(lastId, e.id);
            update((s) => (s.events.some((x) => x.id === e.id) ? s : { ...s, events: [...s.events, e] }));
          } else if (ev.event === "status" || ev.event === "end") {
            const d = ev.data as { status: StatementStatus; error_message: string | null; transaction_count: number };
            update((s) => ({ ...s, status: d.status, error: d.error_message, transactionCount: d.transaction_count }));
            if (ev.event === "end") {
              stopped = true;
              doneRef.current?.(d.status);
            }
          } else if (ev.event === "deleted") {
            stopped = true;
          }
        }
      } catch {
        if (ctrl.signal.aborted) return;
      }
      update((s) => ({ ...s, connected: false }));
      if (!stopped && !ctrl.signal.aborted && attempt < 20) {
        await new Promise((r) => setTimeout(r, Math.min(1000 * 2 ** attempt, 8000)));
        return connect(attempt + 1);
      }
    }
    connect(0);
    return () => ctrl.abort();
  }, [statementId, key]);

  return tagged.key === key ? tagged.state : INITIAL;
}
