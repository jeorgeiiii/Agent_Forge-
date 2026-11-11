"""Cluster 12 - chat memory helpers shared by the API and the worker (pure, deterministic)."""

from __future__ import annotations

import hashlib
import re
from typing import Any

DEFAULT_MAX_TOKENS = 2000   # history budget sent to the model
DEFAULT_KEEP_RECENT = 6     # "summary" memory: newest messages always kept verbatim
_CHARS_PER_TOKEN = 4        # rough, provider-independent estimate (good enough for budgeting)


def memory_key(graph_id: Any, scope: str, session_id: str) -> str:
    """Opaque storage key for one agent's conversation in one session of one graph."""
    raw = f"{graph_id if graph_id is not None else 'local'}|{scope}|{session_id}"
    return "mem:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def estimate_tokens(text: str | None) -> int:
    """~4 characters per token, +4 per message for role/formatting overhead."""
    return (len(text or "") + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN + 4


def trim_to_token_budget(messages: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    """Keep the NEWEST messages that fit in `budget` tokens.

    The result never starts with an assistant reply whose question was cut off
    (models handle that badly), even if that leaves the history empty.
    """
    kept: list[dict[str, Any]] = []
    used = 0
    for message in reversed(messages):
        cost = estimate_tokens(message.get("content"))
        if kept and used + cost > budget:
            break
        kept.append(message)
        used += cost
    kept.reverse()
    # never start the history with an orphan assistant reply
    while kept and kept[0].get("role") == "assistant":
        kept.pop(0)
    return kept


_PII_PATTERNS = [
    ("[email]", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("[card]", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("[phone]", re.compile(r"(?<!\w)(?:\+?\d{1,3}[ .-]?)?(?:\(?\d{2,4}\)?[ .-]?){2,4}\d{3,4}(?!\w)")),
]


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def redact_pii(text: str) -> str:
    """Mask emails, payment-card numbers (Luhn-checked) and phone numbers."""
    text = _PII_PATTERNS[0][1].sub("[email]", text)

    def card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        return "[card]" if 13 <= len(digits) <= 19 and _luhn_ok(digits) else match.group(0)

    text = _PII_PATTERNS[1][1].sub(card, text)

    def phone(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        return "[phone]" if 8 <= len(digits) <= 15 else match.group(0)

    return _PII_PATTERNS[2][1].sub(phone, text)


SUMMARY_PROMPT = (
    "You maintain the memory of a conversation between a user and an AI assistant.\n"
    "Update the running summary with the new messages. Keep every durable fact about the user "
    "(name, preferences, goals, decisions, open questions) and drop small talk. "
    "Write at most 150 words in third person. Reply with the summary only."
)


def summary_request(previous_summary: str | None, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Chat messages that ask the model to fold `messages` into the running summary."""
    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
    body = f"Current summary:\n{previous_summary or '(none yet)'}\n\nNew messages:\n{transcript}"
    return [{"role": "system", "content": SUMMARY_PROMPT}, {"role": "user", "content": body}]
