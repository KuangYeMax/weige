"""Test-only WeChat sender that raises a configured failure exception.

This module is NEVER imported by the production dispatch path.
It exists solely to let the inject-failure test endpoint exercise the real
exception-handling branches in dispatch_scheduler._send_ready_task.
"""
from __future__ import annotations

from app.config import Settings
from app.services.wechat.uia import (
    ChatVerificationError,
    ChatVerificationResult,
    UIAutomationUnavailableError,
)
from app.services.wechat.win32 import ClipboardVerificationError


_VERIFY_EXCEPTIONS = {
    "header-mismatch": lambda: ChatVerificationError("会话头部名称与备注名不一致"),
    "header-unreadable": lambda: ChatVerificationError("无法读取会话头部"),
    "multiple-search-results": lambda: ChatVerificationError("搜索结果不唯一"),
    "uia-unavailable": lambda: UIAutomationUnavailableError("UI Automation 不可用"),
    "verify-unexpected": lambda: ValueError("模拟未知异常：COM 对象返回意外类型"),
}

_SEND_EXCEPTIONS = {
    "clipboard-verification-failed": lambda: ClipboardVerificationError("剪贴板校验失败"),
}


def create_failing_sender(failure_type: str):
    """Return (verify_remark, send) pair that raises the exception matching failure_type."""

    if failure_type in _VERIFY_EXCEPTIONS:
        def verify_remark(remark: str, settings: Settings) -> ChatVerificationResult:
            raise _VERIFY_EXCEPTIONS[failure_type]()

        def send(remark: str, text: str, images: list[str], settings: Settings) -> None:
            raise RuntimeError("failing_sender.send should not be reached for verify-phase failures")

    elif failure_type in _SEND_EXCEPTIONS:
        def verify_remark(remark: str, settings: Settings) -> ChatVerificationResult:
            return ChatVerificationResult(
                expected_remark=remark,
                exact_search_result_count=1,
                selected_result_name=remark,
                header_name=remark,
            )

        def send(remark: str, text: str, images: list[str], settings: Settings) -> None:
            raise _SEND_EXCEPTIONS[failure_type]()

    else:
        raise ValueError(f"Unknown failure_type: {failure_type}")

    return verify_remark, send
