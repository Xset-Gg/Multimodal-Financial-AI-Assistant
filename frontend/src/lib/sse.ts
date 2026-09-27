/** Minimal Server-Sent Events reader over fetch (supports POST bodies and custom headers). */

export interface SseEvent {
  event: string;
  data: unknown;
  id?: string;
}

export async function* readSse(res: Response, signal?: AbortSignal): AsyncGenerator<SseEvent> {
  if (!res.body) return;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      if (signal?.aborted) return;
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        let event = "message";
        let id: string | undefined;
        const data: string[] = [];
        for (const line of block.split("\n")) {
          if (line.startsWith(":")) continue;
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
          else if (line.startsWith("id:")) id = line.slice(3).trim();
        }
        if (!data.length) continue;
        let parsed: unknown = data.join("\n");
        try {
          parsed = JSON.parse(parsed as string);
        } catch {
          /* plain text */
        }
        yield { event, data: parsed, id };
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export async function openSse(path: string, init: RequestInit & { signal?: AbortSignal } = {}): Promise<Response> {
  const res = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { accept: "text/event-stream", "x-spendlens-client": "1", ...(init.body ? { "content-type": "application/json" } : {}), ...init.headers },
  });
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try {
      const b = await res.json();
      if (typeof b.detail === "string") msg = b.detail;
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  return res;
}
