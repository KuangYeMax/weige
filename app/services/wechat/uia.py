"""Coordinate-based WeChat automation replaces UIA verification.

These exception and data classes are kept for backward compatibility
with dispatch_scheduler and its test infrastructure.
"""
from __future__ import annotations

from dataclasses import dataclass


class UIAutomationUnavailableError(RuntimeError):
    """Raised when UI Automation cannot be used on this host."""


class ChatVerificationError(RuntimeError):
    """Raised when the selected WeChat conversation cannot be proven safe."""


@dataclass(frozen=True)
class ChatVerificationResult:
    """Evidence collected while locating and verifying a conversation."""

    expected_remark: str
    exact_search_result_count: int
    selected_result_name: str
    header_name: str
