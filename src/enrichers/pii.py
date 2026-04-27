"""
PII scrubber. Backstop regex layer; for production add Microsoft Presidio.
Runs at ingest so nothing PII-bearing reaches the summarizer or the digest.
"""
from __future__ import annotations

import re


EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(r"\b(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
# Card-like sequences (13–19 digits with optional spaces/dashes)
CARD  = re.compile(r"\b(?:\d[ -]?){13,19}\b")
SSN   = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def scrub(text: str) -> tuple[str, bool]:
    """Return (scrubbed_text, was_modified)."""
    before = text
    text = EMAIL.sub("[email]", text)
    text = PHONE.sub("[phone]", text)
    text = CARD.sub("[card]", text)
    text = SSN.sub("[ssn]", text)
    return text, (text != before)
