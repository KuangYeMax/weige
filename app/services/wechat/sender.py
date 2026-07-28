"""
WeChat sender — production-grade with safety checks.
Windows-only; uses conditional imports so the module is safe to import on macOS.
"""
from __future__ import annotations

import os
import random
import sys
import time
import logging

from app.config import Settings
from app.services.wechat.win32 import (
    require_win32,
    clip_set_text,
    clip_set_image,
    find_wechat_main,
    hide_overlays,
    force_foreground,
    wait_idle,
    click,
    send_key,
)
from app.services.wechat.uia import (
    ChatVerificationError,
    ChatVerificationResult,
    _assert_verified,
    get_uia_adapter,
)

logger = logging.getLogger(__name__)


# ─── Chat session verification ─────────────────────────


def _resolve_input_area(rect: tuple[int, int, int, int], settings: Settings) -> tuple[int, int]:
    x = rect[0] + settings.wechat_input_x_offset
    y = rect[3] - settings.wechat_input_y_offset
    return x, y


def _check_wechat_connected() -> None:
    """Ensure the WeChat main window can be located without reading its title."""
    find_wechat_main()


def _prepare_wechat_window() -> tuple[int, tuple[int, int, int, int]]:
    _check_wechat_connected()
    hwnd, rect = find_wechat_main()
    wait_idle()
    force_foreground(hwnd)
    hide_overlays(hwnd)
    return hwnd, rect


def _verify_chat_session(hwnd: int, remark: str) -> ChatVerificationResult:
    """Locate the exact contact through UIA and prove the opened chat header."""
    result = get_uia_adapter().verify_remark(hwnd, remark.strip())
    _assert_verified(result)
    return result


def verify_remark(remark: str, settings: Settings) -> ChatVerificationResult:
    """Locate and verify a WeChat contact without sending content."""
    del settings  # Retain the stable API used by the future backend route.
    if not remark.strip():
        raise ValueError("好友备注不能为空")
    if sys.platform != "win32":
        raise NotImplementedError("微信发送仅支持 Windows，当前系统无法执行")
    require_win32()
    hwnd, _ = _prepare_wechat_window()
    return _verify_chat_session(hwnd, remark)


def _release_stuck_modifiers() -> None:
    import pyautogui

    for key in ["alt", "ctrl", "shift", "win"]:
        pyautogui.keyUp(key)


# ─── Core send flow ──────────────────────────────────


def send(remark: str, text: str, images: list[str], settings: Settings) -> None:
    """发送微信消息。失败直接抛异常，绝不静默返回 True。
    
    Args:
        remark: 好友备注名
        text: 文字内容
        images: 图片文件路径列表
        settings: 应用配置（含坐标和间隔参数）
    
    Raises:
        ValueError: 好友备注为空，或文字和图片均为空
        RuntimeError: 任何可归因的失败
        FileNotFoundError: 图片文件缺失
        NotImplementedError: 非 Windows 平台
    """
    if not remark.strip() or (not text.strip() and not images):
        raise ValueError("发送载荷不能为空")
    if sys.platform != "win32":
        raise NotImplementedError(
            "微信发送仅支持 Windows，当前系统无法执行"
        )
    require_win32()

    hwnd, rect = _prepare_wechat_window()
    _verify_chat_session(hwnd, remark)
    _release_stuck_modifiers()

    # 发文字
    if text:
        input_x, input_y = _resolve_input_area(rect, settings)
        click(rect, input_x - rect[0], input_y - rect[1])
        time.sleep(0.2)
        clip_set_text(text)
        send_key("ctrl,v")
        time.sleep(0.2)
        send_key("alt,s")
        _random_delay(settings)

    # 发图片
    for p in images:
        if not os.path.isfile(p):
            raise FileNotFoundError(f"发送图片不存在: {p}")
        input_x, input_y = _resolve_input_area(rect, settings)
        click(rect, input_x - rect[0], input_y - rect[1])
        time.sleep(0.2)
        clip_set_image(p)
        send_key("ctrl,v")
        time.sleep(1.5)
        send_key("alt,s")
        _random_delay(settings)


def _random_delay(settings: Settings) -> None:
    delay = random.uniform(settings.wechat_send_interval_min, settings.wechat_send_interval_max)
    time.sleep(delay)
