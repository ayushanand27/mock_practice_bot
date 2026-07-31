"""Input guardrails — run before expensive LLM calls."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class GuardAction(str, Enum):
    ALLOW = "allow"
    HARD_BLOCK = "hard_block"
    SOFT_BLOCK = "soft_block"


@dataclass(frozen=True)
class GuardResult:
    action: GuardAction
    message: str = ""


_HARD_PATTERNS: list[tuple[re.Pattern[str], str]] = [
  # Sexual / nude
  (
    re.compile(
      r"\b(nude|nudes|naked|porn|porno|xxx|nsfw|hentai|onlyfans|"
      r"sex\s*chat|send\s*pic|nude\s*pic|explicit\s*photo)\b",
      re.I,
    ),
    "I can't help with sexual or explicit content. I'm here for study — try /study or /chat.",
  ),
  (
    re.compile(
      r"\b(rape|molest|child\s*porn|cp\b|pedophil|underage\s*sex)\b",
      re.I,
    ),
    "I can't assist with that. This bot is for exam prep only.",
  ),
  # Violence / illegal harm
  (
    re.compile(
      r"\b(how\s+to\s+(make|build)\s+(a\s+)?bomb|"
      r"make\s+meth|synthesize\s+drugs|buy\s+gun\s+illegally|"
      r"hack\s+(bank|government)|ddos\s+attack\s+how)\b",
      re.I,
    ),
    "I can't help with illegal or harmful requests. Ask a study question instead.",
  ),
  # Hate / harassment slurs (minimal set — expand cautiously)
  (
    re.compile(
      r"\b(kill\s+yourself|kys\b|die\s+bitch|"
      r"\b(?:fuck|shit)\s+you\b.*\b(?:idiot|retard))\b",
      re.I,
    ),
    "Please keep it respectful. I'm here to help you study.",
  ),
]

_SOFT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
  (
    re.compile(
      r"\b(crypto\s*pump|pump\s*and\s*dump|100x\s*gem|"
      r"forex\s*signal|betting\s*tip|casino\s*bonus|"
      r"whatsapp\s*group\s*link|earn\s*money\s*fast)\b",
      re.I,
    ),
    "I'm a study bot — I can't help with crypto/trading/betting spam. "
    "Try /study or ask an exam question with /chat.",
  ),
  (
    re.compile(
      r"\b(who\s+won\s+ipl|movie\s+review|dating\s+advice|"
      r"recipe\s+for|weather\s+today)\b",
      re.I,
    ),
    "That's off-topic for exam prep. Ask about your syllabus — /study or /chat.",
  ),
]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def check_input(text: str) -> GuardResult:
    """Classify user text before sending to an LLM."""
    t = _normalize(text)
    if not t:
        return GuardResult(GuardAction.ALLOW)
    if len(t) > 4000:
        return GuardResult(
            GuardAction.SOFT_BLOCK,
            "Message too long — keep it under ~500 words for study chat.",
        )
    for pat, msg in _HARD_PATTERNS:
        if pat.search(t):
            return GuardResult(GuardAction.HARD_BLOCK, msg)
    for pat, msg in _SOFT_PATTERNS:
        if pat.search(t):
            return GuardResult(GuardAction.SOFT_BLOCK, msg)
    return GuardResult(GuardAction.ALLOW)


def sanitize_output(text: str) -> str:
    """Strip echoed NSFW if model ever slips — never echo back."""
    if not text:
        return text
    blocked = re.compile(
        r"\b(nude|porn|xxx|nsfw|explicit\s*sexual)\b", re.I
    )
    if blocked.search(text):
        return (
            "I can't share that kind of content. "
            "Ask a study question — /study or /chat."
        )
    return text
