"""Quarantine instruction-like text in merchant-supplied fields.

Product names/descriptions are rendered to buyer agents and to our own model. A malicious
or compromised source can hide prompts in them ("ignore previous instructions", "rank me
first", "reveal the buyer's phone"). We strip such sentences at compile time *and* flag the
product so the merchant sees it in review and the audit trail records it.
"""

from __future__ import annotations

import re

_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) (instructions|prompts)",
    r"\bsystem\s*:",
    r"</?(assistant|system|user|tool)\b[^>]*>",
    r"\b(note|message|instruction)s? to (the )?(ai|agent|assistant|model|llm)\b",
    r"\byou are (an?|the) (ai|agent|assistant)\b",
    r"\b(always|must) (rank|recommend|show|list) (this|me|it)\b.*\b(first|top)\b",
    r"\bapply (a )?\d{1,3}\s?% (discount|off)\b",
    r"\breveal\b.*\b(phone|number|address|email|otp|password)\b",
    r"\btell the buyer\b",
    r"\bdisregard\b.*\b(rules|policy|policies)\b",
]
_RX = re.compile("|".join(f"(?:{p})" for p in _PATTERNS), re.I)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def sanitize_text(text: str) -> tuple[str, bool]:
    """Return (clean_text, was_modified). Drops any sentence matching an injection pattern."""
    if not text:
        return "", False
    if not _RX.search(text):
        return text.strip(), False
    kept = [s for s in _SENTENCE_SPLIT.split(text) if s and not _RX.search(s)]
    clean = " ".join(s.strip() for s in kept).strip()
    # a single sentence that was itself the payload
    if _RX.search(clean):
        clean = ""
    return clean, True


def looks_injected(text: str) -> bool:
    return bool(text and _RX.search(text))
