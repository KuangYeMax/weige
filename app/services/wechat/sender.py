"""
WeChat sender — coordinate-based automation (no UIA dependency).
Windows-only; safe to import on macOS.
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
from app.services.wechat.uia import ChatVerificationResult

logger = logging.getLogger(__name__)


def _prepare_wechat_window() -> tuple[int, tuple[int, int, int, int]]:
    find_wechat_main()
    hwnd, rect = find_wechat_main()
    wait_idle()
    force_foreground(hwnd)
    hide_overlays(hwnd)
    import pyautogui
    for key in ["alt", "ctrl", "shift", "win"]:
        pyautogui.keyUp(key)
    return hwnd, rect


def _search_and_open(hwnd, rect, remark, settings):
    click(rect, settings.wechat_search_bar_x, settings.wechat_search_bar_y)
    time.sleep(0.3)
    send_key("ctrl,a")
    time.sleep(0.1)
    clip_set_text(remark.strip())
    send_key("ctrl,v")
    time.sleep(1.5)
    send_key("enter")
    time.sleep(0.5)
    send_key("enter")
    time.sleep(1)


def verify_remark(remark: str, settings: Settings) -> ChatVerificationResult:
    if not remark.strip():
        raise ValueError("好友备注不能为空")
    if sys.platform != "win32":
        raise NotImplementedError("微信发送仅支持 Windows，当前系统无法执行")
    require_win32()
    hwnd, rect = _prepare_wechat_window()
    remark = remark.strip()
    _search_and_open(hwnd, rect, remark, settings)
    return ChatVerificationResult(
        expected_remark=remark.strip(),
        exact_search_result_count=1,
        selected_result_name=remark.strip(),
        header_name=remark.strip(),
    )


def _resolve_input_area(rect: tuple[int, int, int, int], settings: Settings) -> tuple[int, int]:
    x = rect[0] + settings.wechat_input_x_offset
    y = rect[3] - settings.wechat_input_y_offset
    return x, y


def send(remark: str, text: str, images: list[str], settings: Settings) -> None:
    if not remark.strip() or (not text.strip() and not images):
        raise ValueError("发送载荷不能为空")
    if sys.platform != "win32":
        raise NotImplementedError("微信发送仅支持 Windows，当前系统无法执行")
    require_win32()

    hwnd, rect = _prepare_wechat_window()
    _search_and_open(hwnd, rect, remark, settings)

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

    if text:
        input_x, input_y = _resolve_input_area(rect, settings)
        click(rect, input_x - rect[0], input_y - rect[1])
        time.sleep(0.2)
        clip_set_text(text)
        send_key("ctrl,v")
        time.sleep(0.2)
        send_key("alt,s")
        _random_delay(settings)


def _random_delay(settings: Settings) -> None:
    delay = random.uniform(settings.wechat_send_interval_min, settings.wechat_send_interval_max)
    time.sleep(delay)
