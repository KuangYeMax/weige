"""Fail-closed UI Automation verification for WeChat conversations.

``uiautomation`` is deliberately imported only when an automation action is
requested on Windows. Importing this module therefore remains safe on macOS.
"""
from __future__ import annotations

from dataclasses import dataclass
import sys
from typing import Any


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


def _load_uiautomation() -> Any:
    if sys.platform != "win32":
        raise UIAutomationUnavailableError(
            "微信会话校验需要 Windows UI Automation，当前系统不可用"
        )
    try:
        import uiautomation
    except ImportError as exc:
        raise UIAutomationUnavailableError(
            "微信会话校验依赖 uiautomation，请在 Windows 上安装该依赖"
        ) from exc
    return uiautomation


class WeChatUIAAdapter:
    """Owns WeChat's UI Automation tree selectors and verification flow."""

    def verify_remark(self, hwnd: int, remark: str) -> ChatVerificationResult:
        expected_remark = remark.strip()
        if not expected_remark:
            raise ChatVerificationError("好友精确校验失败：目标备注不能为空")

        auto = _load_uiautomation()
        main = auto.ControlFromHandle(hwnd)
        search_box = self._find_search_box(main)
        search_box.SetFocus()
        auto.SendKeys("{Ctrl}a")
        auto.SendKeys(expected_remark)
        auto.Sleep(1)

        matches = self._find_exact_search_results(main, expected_remark)
        if len(matches) != 1:
            raise ChatVerificationError(
                "好友精确校验失败：搜索结果不是唯一精确匹配，需人工确认"
            )

        selected = matches[0]
        selected.Click()
        auto.Sleep(0.5)
        result = ChatVerificationResult(
            expected_remark=expected_remark,
            exact_search_result_count=1,
            selected_result_name=self._control_name(selected),
            header_name=self._read_chat_header_name(main),
        )
        _assert_verified(result)
        return result

    def _find_search_box(self, main: Any) -> Any:
        # WeChat exposes the global search field as an Edit control in both
        # Chinese and English builds. Keep selectors here, never in sender.py.
        for name in ("搜索", "Search"):
            control = main.EditControl(Name=name, searchDepth=6)
            if control.Exists(2):
                return control
        raise ChatVerificationError("好友精确校验失败：无法读取微信搜索框")

    def _find_exact_search_results(self, main: Any, remark: str) -> list[Any]:
        return [
            control
            for control in self._walk_controls(main)
            if self._is_visible_list_item(control) and self._control_name(control) == remark
        ]

    def _read_chat_header_name(self, main: Any) -> str:
        """Read the sole visible text control in WeChat's conversation header."""
        main_rect = main.BoundingRectangle
        header_bottom = main_rect.top + min(160, (main_rect.bottom - main_rect.top) // 3)
        names = {
            self._control_name(control)
            for control in self._walk_controls(main)
            if self._is_visible_text(control)
            and main_rect.left + 200 <= control.BoundingRectangle.left
            and control.BoundingRectangle.top >= main_rect.top
            and control.BoundingRectangle.bottom <= header_bottom
            and self._control_name(control)
        }
        if len(names) != 1:
            raise ChatVerificationError("好友精确校验失败：聊天页头名称不可读或不唯一")
        return names.pop()

    def _walk_controls(self, root: Any, depth: int = 0) -> list[Any]:
        if depth > 8:
            return []
        controls: list[Any] = []
        for child in root.GetChildren():
            controls.append(child)
            controls.extend(self._walk_controls(child, depth + 1))
        return controls

    @staticmethod
    def _control_name(control: Any) -> str:
        return str(getattr(control, "Name", "") or "").strip()

    @staticmethod
    def _is_visible_list_item(control: Any) -> bool:
        return (
            getattr(control, "ControlTypeName", "") == "ListItemControl"
            and not getattr(control, "IsOffscreen", False)
        )

    @staticmethod
    def _is_visible_text(control: Any) -> bool:
        return (
            getattr(control, "ControlTypeName", "") == "TextControl"
            and not getattr(control, "IsOffscreen", False)
        )


def _assert_verified(result: ChatVerificationResult) -> None:
    expected = result.expected_remark.strip()
    selected = result.selected_result_name.strip()
    header = result.header_name.strip()
    if (
        not expected
        or result.exact_search_result_count != 1
        or selected != expected
        or not header
        or header != expected
    ):
        raise ChatVerificationError(
            "好友精确校验失败：搜索结果或聊天页头与目标备注不一致"
        )


def get_uia_adapter() -> WeChatUIAAdapter:
    return WeChatUIAAdapter()
