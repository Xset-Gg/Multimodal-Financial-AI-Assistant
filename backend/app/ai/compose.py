"""Turn a computed Answer into prose.

Without an LLM the explanation is the deterministic headline plus notes. With an
LLM, the model may only rephrase the computed facts; its output is checked so that
every number it states appears in those facts. If it introduces any other number,
the text is discarded and the deterministic explanation is used instead.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

from app.ai.execute import Answer
from app.ai.llm import GroqLLM, LLMError

EXPLAIN_PROMPT = """You explain answers computed by a financial data system.
Write 1-3 short sentences answering the user's question using ONLY the provided facts.
Do not add, round, convert, or calculate any number; copy numbers exactly as given.
Do not mention transactions, merchants or dates that are not in the facts. Do not give financial advice.
If the facts say nothing was found, say so plainly. Plain text, no markdown tables."""

NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def deterministic_text(ans: Answer) -> str:
    parts = [ans.headline]
    if ans.notes:
        parts.append(" ".join(ans.notes))
    if ans.warnings:
        parts.append(" ".join(ans.warnings))
    return "\n\n".join(parts)


def _numbers(text: str) -> set[str]:
    out = set()
    for n in NUM_RE.findall(text):
        clean = n.replace(",", "").rstrip(".")
        if not clean:
            continue
        out.add(clean)
        if "." in clean:
            out.add(clean.rstrip("0").rstrip("."))
    return out


def numbers_grounded(text: str, facts: list[str]) -> bool:
    allowed = set()
    for f in facts:
        allowed |= _numbers(f)
        # allow date components (e.g. "September 15" when the fact has 2025-09-15)
        for y, m, d in re.findall(r"(\d{4})-(\d{2})-(\d{2})", f):
            allowed |= {y, str(int(m)), str(int(d)), m, d}
    allowed |= {str(i) for i in range(0, 11)}  # small counting words/ordinals
    return all(n in allowed for n in _numbers(text))


async def stream_explanation(question: str, ans: Answer, llm: GroqLLM | None) -> AsyncIterator[tuple[str, str]]:
    """Yield ("token", text) while streaming, then ("final", validated_text)."""
    fallback = deterministic_text(ans)
    if llm is None or ans.kind in ("help",):
        yield ("token", fallback)
        yield ("final", fallback)
        return
    facts = ans.facts or [ans.headline]
    messages = [
        {"role": "system", "content": EXPLAIN_PROMPT},
        {"role": "user", "content": json.dumps({"question": question, "facts": facts})},
    ]
    collected: list[str] = []
    try:
        async for delta in llm.stream_text(messages):
            collected.append(delta)
            yield ("token", delta)
    except LLMError:
        yield ("final", fallback)
        return
    text = "".join(collected).strip()
    if not text or not numbers_grounded(text, facts):
        yield ("final", fallback)
        return
    # Notes and warnings are always shown verbatim, whatever the model wrote.
    extra = " ".join(ans.warnings)
    yield ("final", text + (f"\n\n{extra}" if extra else ""))
