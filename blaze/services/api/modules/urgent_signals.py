from __future__ import annotations

from typing import Iterable


URGENT_MARKERS = ("urgent", "asap", "immediately", "today", "overdue")


def detect_urgent_signals(text_items: Iterable[str]) -> list[str]:
    hits = []
    for item in text_items:
        low = item.lower()
        if any(marker in low for marker in URGENT_MARKERS):
            hits.append(item)
    return hits

