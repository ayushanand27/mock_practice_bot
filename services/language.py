"""Lightweight language detection for Indic / Hinglish routing."""

from __future__ import annotations

import re

# Devanagari + common Indic blocks
_INDIC_RE = re.compile(r"[\u0900-\u097F\u0980-\u09FF\u0A00-\u0A7F\u0A80-\u0AFF\u0B00-\u0B7F]")

_HINGLISH_MARKERS = re.compile(
    r"\b(kya|kaise|kyun|kyu|hai|hain|nahi|nahin|mujhe|batao|bata|"
    r"samjhao|samjha|padhai|exam|topic|chapter|sawal|prashn|"
    r"accha|theek|thik|bhai|yaar|matlab|karo|karna|hoga|hogi)\b",
    re.I,
)

_ENGLISH_ONLY = re.compile(r"^[A-Za-z0-9\s.,!?;:'\"()\-+/=%&@#]+$")


def detect_language(text: str) -> str:
    """
    Return a coarse language tag: 'hi', 'hinglish', or 'en'.
    Used to route non-English study chat to Sarvam when available.
    """
    t = (text or "").strip()
    if not t:
        return "en"
    if _INDIC_RE.search(t):
        return "hi"
    if _HINGLISH_MARKERS.search(t):
        return "hinglish"
    if _ENGLISH_ONLY.match(t):
        return "en"


def reply_language_hint(lang: str) -> str:
    """System prompt suffix for response language."""
    if lang == "hi":
        return (
            "The student wrote in Hindi (Devanagari). Reply in clear Hindi "
            "(Devanagari script), concise Telegram-friendly paragraphs."
        )
    if lang == "hinglish":
        return (
            "The student wrote in Hinglish (Hindi-English mix). Reply in natural "
            "Hinglish (Roman script is fine), concise and exam-focused."
        )
    return "Reply in clear English, concise Telegram-friendly paragraphs."
