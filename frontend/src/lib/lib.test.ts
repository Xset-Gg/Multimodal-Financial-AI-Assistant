import { describe, expect, it } from "vitest";

import { toQuery } from "./api";
import { decimals, money } from "./format";
import { readSse } from "./sse";

function streamOf(chunks: string[]): Response {
  const enc = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      chunks.forEach((c) => controller.enqueue(enc.encode(c)));
      controller.close();
    },
  });
  return new Response(body);
}

describe("readSse", () => {
  it("parses events split across chunks, ignores comments, and decodes JSON", async () => {
    const res = streamOf([
      "retry: 2000\n\n: keep-alive\n\nid: 7\nevent: progress\nda",
      'ta: {"stage":"extracting"}\n\nevent: token\r\ndata: {"text":"Hi"}\r\n\r\n',
      "event: final\ndata: plain text\n\n",
    ]);
    const events = [];
    for await (const e of readSse(res)) events.push(e);
    expect(events).toEqual([
      { event: "progress", data: { stage: "extracting" }, id: "7" },
      { event: "token", data: { text: "Hi" }, id: undefined },
      { event: "final", data: "plain text", id: undefined },
    ]);
  });
});

describe("money", () => {
  it("formats exact decimal strings with currency-specific precision", () => {
    expect(money("10914.52", "QAR")).toBe("10,914.52 QAR");
    expect(money("12.5", "KWD")).toBe("12.500 KWD");
    expect(money("1500", "JPY")).toBe("1,500 JPY");
    expect(money(null, "QAR")).toBe("—");
    expect(money("5", "USD", { sign: true })).toBe("+5.00 USD");
    expect(decimals("BHD")).toBe(3);
  });
});

describe("toQuery", () => {
  it("drops empty values and repeats array params", () => {
    expect(toQuery({ statement_id: ["a", "b"], search: "", min_amount: "500", missing: undefined })).toBe(
      "?statement_id=a&statement_id=b&min_amount=500",
    );
    expect(toQuery({})).toBe("");
  });
});
