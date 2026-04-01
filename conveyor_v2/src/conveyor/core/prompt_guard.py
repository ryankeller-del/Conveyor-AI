"""Prompt guard (hallucination detection) — STUB.

Legacy implementation is inside the 3,978-line controller.py and is opaque.
This stub always passes — the real implementation will be added after
the parallel run phase when actual hallucination detection logic can be verified.

Legacy status keys:
  hallucination_confidence, hallucination_alert_count, latest_hallucination_alert
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GuardResult:
    confidence: float
    alert_count: int
    latest_alert: str


def evaluate(response_text: str = "", context: str = "") -> GuardResult:
    """Evaluate a response for potential hallucination.

    STUB: Always returns full confidence (1.0), no alerts.
    This matches the legacy default before any hallucination was detected.
    """
    return GuardResult(
        confidence=1.0,
        alert_count=0,
        latest_alert="",
    )
